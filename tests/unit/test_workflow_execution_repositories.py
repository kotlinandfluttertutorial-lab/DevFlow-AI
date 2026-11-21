"""Durable workflow execution invariants on independent file-backed SQLite sessions."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devflow_api.database import _configure_sqlite
from devflow_api.models import ApprovalRequest, Base, Workflow, WorkflowRun, WorkflowStepRun
from devflow_api.repositories._workflow_state import is_real_failure
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import (
    ApprovalRepository,
    AuditLogRepository,
    is_pending_approval_conflict,
)
from devflow_api.repositories.workflow import WorkflowRunRepository
from devflow_api.repositories.workflow_status import derive_and_persist_run_status
from devflow_api.repositories.workflow_step_run import STEP_TRANSITIONS, WorkflowStepRunRepository
from devflow_api.schemas.workflow_template import (
    ApprovalOperation,
    WorkflowTemplate,
    canonical_hash,
)
from devflow_core.exceptions import (
    ApprovalNotConsumableError,
    DatabaseBusyError,
    InvalidStatusTransitionError,
    InvalidStepTransitionError,
    OperationNotPermitted,
    StepDispatchConflict,
)

type Factory = async_sessionmaker[AsyncSession]
DIGEST = canonical_hash({"input": "document"})


@pytest.fixture
async def execution_factory(tmp_path: Path) -> AsyncIterator[Factory]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'execution.db').as_posix()}")
    event.listen(engine.sync_engine, "connect", _configure_sqlite)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed(
    factory: Factory,
    states: dict[str, str],
    *,
    protected: bool = False,
    run_status: str = "running",
) -> tuple[int, dict[str, int]]:
    template = WorkflowTemplate.model_validate(
        {
            "template_version": 1,
            "steps": [
                {
                    "key": key,
                    "version": 1,
                    "title": key,
                    "executor_id": "mock",
                    "tool_selection": {"executor_id": "mock"},
                    "approvable": protected,
                    "protected_operation": "implementation_authorization" if protected else None,
                    "retry_policy": {"safe": True, "idempotent": True, "max_attempts": 3},
                }
                for key in states
            ],
        }
    )
    async with factory() as session:
        workflow = Workflow(name="Execution", definition_json=template.model_dump_json())
        session.add(workflow)
        await session.commit()
        run = await WorkflowRunRepository(session).create_with_snapshot(workflow.id, template)
        run_id = run.id
        async with immediate_transaction(session):
            await session.execute(
                update(WorkflowRun).where(WorkflowRun.id == run_id).values(status=run_status)
            )
            rows = await WorkflowStepRunRepository(session).list_for_run(run_id)
            ids = {row.step_key: row.id for row in rows}
            for row in rows:
                values: dict[str, object] = {"status": states[row.step_key]}
                if states[row.step_key] == "RUNNING":
                    values.update(
                        attempt=1,
                        lease_owner="worker",
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
                    )
                if states[row.step_key] == "FAILED":
                    values.update(
                        result_status="failed",
                        error_json=json.dumps(
                            {
                                "category": "executor",
                                "message": "real failure",
                            }
                        ),
                    )
                await session.execute(
                    update(WorkflowStepRun).where(WorkflowStepRun.id == row.id).values(**values)
                )
        return run_id, ids


def binding(run_id: int, step_id: int) -> dict[str, object]:
    return {
        "workflow_run_id": run_id,
        "workflow_step_run_id": step_id,
        "operation": "implementation_authorization",
        "parameters_hash": DIGEST,
        "actor_id": "local_operator",
        "step_name": "gate",
        "action_description": "Authorize mock",
    }


async def approved(factory: Factory, run_id: int, step_id: int) -> int:
    async with factory() as session:
        repo = ApprovalRepository(session)
        approval = await repo.create_bound(binding(run_id, step_id))
        await repo.resolve(approval.id, "approved", "operator")
        await repo.set_authorization_expires_at(approval.id)
        return approval.id


async def read_step(factory: Factory, step_id: int) -> WorkflowStepRun:
    async with factory() as session:
        row = await session.get(WorkflowStepRun, step_id)
        assert row is not None
        return row


async def dispatch_protected(
    session: AsyncSession, step_id: int, approval_id: int, *, owner: str = "worker"
) -> WorkflowStepRun:
    return await WorkflowStepRunRepository(session).dispatch_protected_txn(
        step_id,
        owner,
        approval_id=approval_id,
        operation="implementation_authorization",
        parameters_hash=DIGEST,
        actor_id="local_operator",
    )


@pytest.mark.parametrize("source", list(STEP_TRANSITIONS))
@pytest.mark.parametrize("target", list(STEP_TRANSITIONS))
async def test_step_transition_contract(
    execution_factory: Factory, source: str, target: str
) -> None:
    _, ids = await seed(execution_factory, {"a": source})
    async with execution_factory() as session:
        repo = WorkflowStepRunRepository(session)
        if source == "RUNNING" and target == "INTERRUPTED":
            await session.execute(
                update(WorkflowStepRun)
                .where(WorkflowStepRun.id == ids["a"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        if target == "RUNNING" and source == "READY":
            assert (await repo.dispatch_cas(ids["a"], "worker")).attempt == 1
        elif target not in STEP_TRANSITIONS[source] or target == "RUNNING":
            with pytest.raises(InvalidStepTransitionError):
                await repo.transition_cas(
                    ids["a"], target, from_status=source, lease_owner="worker", attempt=1
                )
            assert (await repo.get(ids["a"])).status == source  # type: ignore[union-attr]
            events = await AuditLogRepository(session).list_for_run(
                (await read_step(execution_factory, ids["a"])).workflow_run_id
            )
            assert [e.event_type for e in events] == ["step_transition_rejected"]
        else:
            assert await repo.transition_cas(
                ids["a"], target, from_status=source, lease_owner="worker", attempt=1
            )
            assert (await read_step(execution_factory, ids["a"])).status == target


async def test_concurrent_dispatch_only_one_succeeds(execution_factory: Factory) -> None:
    _, ids = await seed(execution_factory, {"a": "READY"})

    async def dispatch(owner: str) -> int:
        async with execution_factory() as session:
            return (await WorkflowStepRunRepository(session).dispatch_cas(ids["a"], owner)).attempt

    results = await asyncio.gather(dispatch("one"), dispatch("two"), return_exceptions=True)
    assert sum(isinstance(result, StepDispatchConflict) for result in results) == 1
    assert sum(result == 1 for result in results) == 1
    step = await read_step(execution_factory, ids["a"])
    assert step.idempotency_key == f"{step.workflow_run_id}:a:1"


async def test_concurrent_consume_only_one_dispatch_proceeds(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "READY"}, protected=True)
    approval_id = await approved(execution_factory, run_id, ids["gate"])

    async def dispatch(owner: str) -> WorkflowStepRun:
        async with execution_factory() as session:
            return await dispatch_protected(session, ids["gate"], approval_id, owner=owner)

    results = await asyncio.gather(dispatch("one"), dispatch("two"), return_exceptions=True)
    assert sum(isinstance(row, WorkflowStepRun) for row in results) == 1
    assert sum(isinstance(row, StepDispatchConflict) for row in results) == 1
    async with execution_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None and approval.consumed_at is not None


@pytest.mark.parametrize(
    "mismatch",
    [
        "status_not_approved",
        "already_consumed",
        "authorization_expired",
        "operation_mismatch",
        "digest_mismatch",
        "step_binding_mismatch",
        "actor_mismatch",
    ],
)
async def test_consume_validation_rejects_every_binding(
    execution_factory: Factory, mismatch: str
) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "READY", "other": "READY"}, protected=True)
    approval_id = await approved(execution_factory, run_id, ids["gate"])
    changes: dict[str, dict[str, object]] = {
        "status_not_approved": {"status": "pending"},
        "already_consumed": {"consumed_at": datetime.now(UTC)},
        "authorization_expired": {"authorization_expires_at": datetime.now(UTC)},
        "operation_mismatch": {"operation": "pr_creation_approval"},
        "digest_mismatch": {"parameters_hash": "0" * 64},
        "step_binding_mismatch": {"workflow_step_run_id": ids["other"]},
        "actor_mismatch": {"actor_id": "someone_else"},
    }
    async with execution_factory() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id)
            .values(**changes[mismatch])
        )
        await session.commit()
        with pytest.raises(ApprovalNotConsumableError):
            await dispatch_protected(session, ids["gate"], approval_id)
    step = await read_step(execution_factory, ids["gate"])
    assert (step.status, step.attempt, step.lease_owner, step.lease_expires_at) == (
        "READY",
        0,
        None,
        None,
    )
    async with execution_factory() as session:
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert [row.event_type for row in events] == ["approval_not_consumable"]
        assert json.loads(events[0].payload_json or "{}")["reason"] == mismatch


@pytest.mark.parametrize("target", ["FAILED", "CANCELLED", "READY", "WAITING_APPROVAL"])
async def test_consumed_approval_never_restored(execution_factory: Factory, target: str) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "READY"}, protected=True)
    approval_id = await approved(execution_factory, run_id, ids["gate"])
    async with execution_factory() as session:
        await dispatch_protected(session, ids["gate"], approval_id)
        assert await WorkflowStepRunRepository(session).persist_result_cas(
            ids["gate"], target, lease_owner="worker", attempt=1
        )
        assert not await ApprovalRepository(session).consume_atomic(
            approval_id,
            operation="implementation_authorization",
            parameters_hash=DIGEST,
            workflow_step_run_id=ids["gate"],
            actor_id="local_operator",
        )
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None and approval.consumed_at is not None


async def test_stale_completion_discarded_by_lease_fence(execution_factory: Factory) -> None:
    _, ids = await seed(execution_factory, {"a": "READY"})
    async with execution_factory() as session:
        repo = WorkflowStepRunRepository(session)
        await repo.dispatch_cas(ids["a"], "old")
        assert not await repo.persist_result_cas(
            ids["a"], "INTERRUPTED", lease_owner="old", attempt=1
        )
        await session.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.id == ids["a"])
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        assert await repo.persist_result_cas(ids["a"], "INTERRUPTED", lease_owner="old", attempt=1)
        assert await repo.transition_cas(ids["a"], "READY", from_status="INTERRUPTED")
        row = await repo.dispatch_cas(ids["a"], "new")
        assert row.attempt == 2 and row.idempotency_key.endswith(":2")  # type: ignore[union-attr]
        assert not await repo.persist_result_cas(
            ids["a"], "SUCCEEDED", lease_owner="old", attempt=1, values={"output_json": "{}"}
        )
        assert not await repo.persist_result_cas(ids["a"], "FAILED", lease_owner="new", attempt=1)
        assert await repo.persist_result_cas(
            ids["a"],
            "SUCCEEDED",
            lease_owner="new",
            attempt=2,
            values={"result_status": "succeeded", "output_json": "{}"},
        )


async def test_heartbeat_only_owner_renews_lease(execution_factory: Factory) -> None:
    _, ids = await seed(execution_factory, {"a": "RUNNING"})
    async with execution_factory() as session:
        repo = WorkflowStepRunRepository(session)
        assert not await repo.renew_lease(ids["a"], "wrong", 1)
        assert not await repo.renew_lease(ids["a"], "worker", 0)
        assert await repo.renew_lease(ids["a"], "worker", 1)
        assert not await repo.release_lease(ids["a"], "wrong", 1)
        assert await repo.release_lease(ids["a"], "worker", 1)


async def test_cancelled_run_rejects_heartbeat(execution_factory: Factory) -> None:
    _, ids = await seed(execution_factory, {"a": "RUNNING"}, run_status="cancelled")
    async with execution_factory() as session:
        assert not await WorkflowStepRunRepository(session).renew_lease(ids["a"], "worker", 1)


async def test_find_stale_and_interrupted_steps(execution_factory: Factory) -> None:
    _, ids = await seed(execution_factory, {"a": "RUNNING", "b": "INTERRUPTED"})
    async with execution_factory() as session:
        repo = WorkflowStepRunRepository(session)
        assert await repo.find_stale_running() == []
        stale = await repo.find_stale_running(datetime.now(UTC) + timedelta(minutes=2))
        assert [step.id for step in stale] == [ids["a"]]
        assert [step.id for step in await repo.list_interrupted()] == [ids["b"]]


@pytest.mark.parametrize("swept", [False, True])
async def test_expire_and_re_gate_stale_pending_approval_not_reused(
    execution_factory: Factory, swept: bool
) -> None:
    run_id, ids = await seed(
        execution_factory,
        {"gate": "WAITING_APPROVAL"},
        protected=True,
        run_status="waiting_approval",
    )
    async with execution_factory() as session:
        repo = ApprovalRepository(session)
        old = await repo.create_bound(binding(run_id, ids["gate"]))
        old_id = old.id
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == old_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        if swept:
            assert await repo.expire_pending() == 1
        new = await repo.expire_and_re_gate(ids["gate"], binding(run_id, ids["gate"]))
        assert new.id != old_id and new.expires_at is not None
        assert new.expires_at > datetime.now(UTC)
        assert (
            await repo.expire_and_re_gate(ids["gate"], binding(run_id, ids["gate"]))
        ).id == new.id
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert [row.event_type for row in events] == [
            "approval_expired",
            "approval_re_gated",
            "approval_re_gate_noop",
        ]
        old_row = await session.get(ApprovalRequest, old_id, populate_existing=True)
        assert old_row is not None and old_row.status == "expired"
    assert (await read_step(execution_factory, ids["gate"])).status == "WAITING_APPROVAL"


async def test_expire_and_re_gate_concurrent_calls_produce_exactly_one_pending_approval(
    execution_factory: Factory,
) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "WAITING_APPROVAL"}, protected=True)

    async def re_gate() -> int:
        async with execution_factory() as session:
            return (
                await ApprovalRepository(session).expire_and_re_gate(
                    ids["gate"], binding(run_id, ids["gate"])
                )
            ).id

    results = await asyncio.gather(re_gate(), re_gate())
    assert results[0] == results[1]
    async with execution_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApprovalRequest)) == 1


@pytest.mark.parametrize("stop", ["cancelled", "failed"])
async def test_expire_and_re_gate_refused_when_run_stopped(
    execution_factory: Factory, stop: str
) -> None:
    run_id, ids = await seed(
        execution_factory,
        {"gate": "WAITING_APPROVAL", "other": "FAILED" if stop == "failed" else "PENDING"},
        run_status="cancelled" if stop == "cancelled" else "running",
        protected=True,
    )
    async with execution_factory() as session:
        with pytest.raises(OperationNotPermitted):
            await ApprovalRepository(session).expire_and_re_gate(
                ids["gate"], binding(run_id, ids["gate"])
            )


async def test_partial_unique_index_excludes_phase1(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "WAITING_APPROVAL"})
    async with execution_factory() as session:
        repo = ApprovalRepository(session)
        for _ in range(2):
            await repo.create(
                {
                    "workflow_run_id": run_id,
                    "workflow_step_run_id": ids["gate"],
                    "step_name": "legacy",
                    "action_description": "legacy",
                }
            )
            await session.commit()
        await repo.create_bound(binding(run_id, ids["gate"]))
        with pytest.raises(IntegrityError) as error:
            await repo.create_bound(binding(run_id, ids["gate"]))
        assert is_pending_approval_conflict(error.value)
        assert not session.in_transaction()


@pytest.mark.parametrize("kind", ["foreign_key", "check", "other_unique"])
async def test_expire_and_re_gate_non_unique_integrity_error_propagates_unchanged(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "WAITING_APPROVAL"}, protected=True)
    original = sqlite3.IntegrityError(kind)
    original.sqlite_errorcode = (
        sqlite3.SQLITE_CONSTRAINT_UNIQUE
        if kind == "other_unique"
        else sqlite3.SQLITE_CONSTRAINT_CHECK
    )
    error = IntegrityError("INSERT", {}, original)
    async with execution_factory() as session:
        repo = ApprovalRepository(session)
        monkeypatch.setattr(repo, "create_bound", AsyncMock(side_effect=error))
        with pytest.raises(IntegrityError) as caught:
            await repo.expire_and_re_gate(ids["gate"], binding(run_id, ids["gate"]))
        assert caught.value is error
        assert not session.in_transaction()


async def test_expire_and_re_gate_session_rolled_back_after_unique_constraint_violation(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "WAITING_APPROVAL"}, protected=True)
    async with execution_factory() as session:
        repo = ApprovalRepository(session)
        winner = await repo.create_bound(binding(run_id, ids["gate"]))
        valid_pending = repo._valid_pending
        reads = 0

        async def hide_first(step_id: int) -> ApprovalRequest | None:
            nonlocal reads
            reads += 1
            if reads == 1:
                return None
            return await valid_pending(step_id)

        monkeypatch.setattr(repo, "_valid_pending", hide_first)
        rollback = AsyncMock(wraps=session.rollback)
        monkeypatch.setattr(session, "rollback", rollback)
        result = await repo.expire_and_re_gate(ids["gate"], binding(run_id, ids["gate"]))
        assert result.id == winner.id
        assert rollback.await_count >= 1
        assert not session.in_transaction()


async def test_expire_and_re_gate_sqlite_busy_retried_bounded(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with execution_factory() as session:
        repo = ApprovalRepository(session)
        original = sqlite3.OperationalError("database is locked")
        original.sqlite_errorcode = sqlite3.SQLITE_BUSY
        error = OperationalError("BEGIN IMMEDIATE", {}, original)
        operation = AsyncMock(side_effect=error)
        monkeypatch.setattr(repo, "_re_gate_once", operation)
        sleep = AsyncMock()
        monkeypatch.setattr("devflow_api.repositories.approval.asyncio.sleep", sleep)
        with pytest.raises(DatabaseBusyError):
            await repo.expire_and_re_gate(1, {})
        assert operation.await_count == 3
        assert [call.args[0] for call in sleep.await_args_list] == [0.01, 0.02]
        assert not session.in_transaction()


@pytest.mark.parametrize(
    "error",
    [
        '{"category":"executor","message":"skipped is mentioned in diagnostic text"}',
        '{"category":"timeout"}',
        None,
        "malformed",
        "[]",
    ],
)
async def test_derive_uses_structured_failure_category(
    execution_factory: Factory, error: str | None
) -> None:
    run_id, ids = await seed(
        execution_factory,
        {"fail": "FAILED", "gate": "WAITING_APPROVAL"},
        run_status="waiting_approval",
    )
    async with execution_factory() as session:
        await session.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.id == ids["fail"])
            .values(
                error_json=error,
                result_status="indeterminate",
            )
        )
        await session.commit()
        assert await derive_and_persist_run_status(run_id, session) == "running"


async def test_failure_stop_settlement_and_inflight_completion(execution_factory: Factory) -> None:
    run_id, ids = await seed(
        execution_factory,
        {
            "fail": "FAILED",
            "gate": "WAITING_APPROVAL",
            "ready": "READY",
            "pending": "PENDING",
            "interrupted": "INTERRUPTED",
            "active": "RUNNING",
        },
        run_status="waiting_approval",
    )
    async with execution_factory() as session:
        repo = WorkflowStepRunRepository(session)
        approval = await ApprovalRepository(session).create_bound(binding(run_id, ids["gate"]))
        approval_id = approval.id
        assert await derive_and_persist_run_status(run_id, session) == "running"
        assert await repo.settle_to_failed(run_id) == 4
        assert await repo.settle_to_failed(run_id) == 0
        assert await derive_and_persist_run_status(run_id, session) == "running"
        with pytest.raises(OperationNotPermitted):
            await repo.dispatch_cas(ids["ready"], "late")
        assert await repo.persist_result_cas(
            ids["active"], "SUCCEEDED", lease_owner="worker", attempt=1
        )
        assert await derive_and_persist_run_status(run_id, session) == "failed"
        old = await session.get(ApprovalRequest, approval_id, populate_existing=True)
        assert old is not None and old.status == "expired"
        rows = await repo.list_for_run(run_id)
        for step in rows:
            if step.step_key in {"gate", "ready", "pending", "interrupted"}:
                assert step.status == "FAILED" and not is_real_failure(step)
                assert json.loads(step.error_json or "{}")["category"] == "skipped"


async def test_failure_stop_all_terminal_while_run_waiting_approval_executes_two_step_transition(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, _ = await seed(
        execution_factory, {"fail": "FAILED", "done": "SUCCEEDED"}, run_status="waiting_approval"
    )
    calls: list[tuple[str, bool]] = []
    transition = WorkflowRunRepository.transition_status

    async def observe(
        self: WorkflowRunRepository,
        run_id: int,
        new_status: str,
        valid_from: list[str],
        *,
        error_message: str | None = None,
        commit: bool = True,
    ) -> WorkflowRun:
        result = await transition(
            self, run_id, new_status, valid_from, error_message=error_message, commit=commit
        )
        calls.append((new_status, commit))
        async with execution_factory() as reader:
            assert await WorkflowRunRepository(reader).get_status(run_id) == "waiting_approval"
        return result

    monkeypatch.setattr(WorkflowRunRepository, "transition_status", observe)
    async with execution_factory() as session:
        assert await derive_and_persist_run_status(run_id, session) == "failed"
    assert calls == [("running", False), ("failed", False)]


async def test_failure_stop_two_step_rollback_on_second_transition_error(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, _ = await seed(execution_factory, {"fail": "FAILED"}, run_status="waiting_approval")
    transition = WorkflowRunRepository.transition_status

    async def fail(
        self: WorkflowRunRepository,
        run_id: int,
        new_status: str,
        valid_from: list[str],
        *,
        error_message: str | None = None,
        commit: bool = True,
    ) -> WorkflowRun:
        if new_status == "failed":
            raise RuntimeError("Injected second transition failure")
        return await transition(
            self, run_id, new_status, valid_from, error_message=error_message, commit=commit
        )

    monkeypatch.setattr(WorkflowRunRepository, "transition_status", fail)
    async with execution_factory() as session:
        with pytest.raises(RuntimeError):
            await derive_and_persist_run_status(run_id, session)
    async with execution_factory() as session:
        assert await WorkflowRunRepository(session).get_status(run_id) == "waiting_approval"
        assert await AuditLogRepository(session).list_for_run(run_id) == []


@pytest.mark.parametrize("terminal", ["failed", "completed", "cancelled"])
async def test_derive_run_status_idempotent_on_terminal_run(
    execution_factory: Factory, terminal: str
) -> None:
    run_id, _ = await seed(execution_factory, {"gate": "WAITING_APPROVAL"}, run_status=terminal)
    async with execution_factory() as session:
        assert await derive_and_persist_run_status(run_id, session) == terminal
        assert await derive_and_persist_run_status(run_id, session) == terminal


async def test_derive_concurrent_calls_and_multi_gate_precedence(
    execution_factory: Factory,
) -> None:
    run_id, ids = await seed(execution_factory, {"a": "WAITING_APPROVAL", "b": "WAITING_APPROVAL"})

    async def derive() -> str:
        async with execution_factory() as session:
            return await derive_and_persist_run_status(run_id, session)

    assert tuple(await asyncio.gather(derive(), derive())) == (
        "waiting_approval",
        "waiting_approval",
    )
    async with execution_factory() as session:
        repo = WorkflowStepRunRepository(session)
        assert await repo.transition_cas(ids["a"], "READY", from_status="WAITING_APPROVAL")
        assert await derive_and_persist_run_status(run_id, session) == "waiting_approval"
        assert await repo.transition_cas(ids["b"], "READY", from_status="WAITING_APPROVAL")
        assert await derive_and_persist_run_status(run_id, session) == "running"


async def test_derive_illegal_transition_raises_not_silent_noop(execution_factory: Factory) -> None:
    run_id, _ = await seed(execution_factory, {"a": "SUCCEEDED"}, run_status="pending")
    async with execution_factory() as session:
        with pytest.raises(InvalidStatusTransitionError):
            await derive_and_persist_run_status(run_id, session)


async def test_template_snapshot_immutable_after_live_workflow_edit(
    execution_factory: Factory,
) -> None:
    run_id, ids = await seed(execution_factory, {"a": "PENDING"})
    async with execution_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.template_snapshot_json is not None
        before = run.template_snapshot_json
        assert run.template_hash == canonical_hash(json.loads(before))
        await session.execute(
            update(Workflow)
            .where(Workflow.id == run.workflow_id)
            .values(definition_json='{"changed": true}')
        )
        await session.commit()
        with pytest.raises(ValueError, match="immutable"):
            await WorkflowRunRepository(session).update(run_id, {"template_hash": "changed"})
    async with execution_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.template_snapshot_json == before
        assert (await read_step(execution_factory, ids["a"])).step_version == 1


async def test_create_snapshot_and_steps_roll_back_atomically(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with execution_factory() as session:
        workflow = Workflow(name="atomic")
        session.add(workflow)
        await session.commit()
        template = WorkflowTemplate.model_validate(
            {
                "template_version": 1,
                "steps": [
                    {
                        "key": "a",
                        "version": 1,
                        "title": "a",
                        "executor_id": "mock",
                        "tool_selection": {"executor_id": "mock"},
                    }
                ],
            }
        )
        monkeypatch.setattr(
            WorkflowStepRunRepository,
            "create_for_run",
            AsyncMock(side_effect=RuntimeError("Injected failure")),
        )
        with pytest.raises(RuntimeError):
            await WorkflowRunRepository(session).create_with_snapshot(workflow.id, template)
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 0


async def test_run_delete_cascades_steps_and_approvals(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "WAITING_APPROVAL"})
    async with execution_factory() as session:
        await ApprovalRepository(session).create_bound(binding(run_id, ids["gate"]))
        assert await WorkflowRunRepository(session).delete(run_id)
        assert await session.scalar(select(func.count()).select_from(WorkflowStepRun)) == 0
        assert await session.scalar(select(func.count()).select_from(ApprovalRequest)) == 0
    assert not hasattr(WorkflowStepRunRepository, "delete")


async def test_immediate_transaction_preserves_caller_work_and_rolls_back_on_error(
    execution_factory: Factory,
) -> None:
    async with execution_factory() as session:
        session.add(Workflow(name="uncommitted"))
        with pytest.raises(InvalidRequestError):
            async with immediate_transaction(session):
                pass
        assert session.new
        await session.rollback()
        with pytest.raises(RuntimeError):
            async with immediate_transaction(session):
                session.add(Workflow(name="rollback"))
                await session.flush()
                raise RuntimeError("Injected failure")
        assert not session.in_transaction()
        assert await session.scalar(select(func.count()).select_from(Workflow)) == 0


async def test_cas_uses_begin_immediate(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ids = await seed(execution_factory, {"a": "PENDING"})
    async with execution_factory() as session:
        execute = AsyncMock(wraps=session.execute)
        monkeypatch.setattr(session, "execute", execute)
        assert await WorkflowStepRunRepository(session).transition_cas(
            ids["a"], "READY", from_status="PENDING"
        )
        assert str(execute.await_args_list[0].args[0]) == "BEGIN IMMEDIATE"
        assert not session.in_transaction()


async def test_consume_and_lease_acquired_in_one_transaction(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "READY"}, protected=True)
    approval_id = await approved(execution_factory, run_id, ids["gate"])
    consume = ApprovalRepository.consume_atomic
    observed = False

    async def observe(
        self: ApprovalRepository,
        approval_id: int,
        *,
        operation: ApprovalOperation,
        parameters_hash: str,
        workflow_step_run_id: int,
        actor_id: str,
        commit: bool = True,
    ) -> bool:
        nonlocal observed
        observed = True
        assert self.session.info["workflow_immediate"] and not commit
        async with execution_factory() as reader:
            row = await reader.get(WorkflowStepRun, workflow_step_run_id)
            assert row is not None and row.status == "READY" and row.attempt == 0
        return await consume(
            self,
            approval_id,
            operation=operation,
            parameters_hash=parameters_hash,
            workflow_step_run_id=workflow_step_run_id,
            actor_id=actor_id,
            commit=commit,
        )

    monkeypatch.setattr(ApprovalRepository, "consume_atomic", observe)
    async with execution_factory() as session:
        row = await dispatch_protected(session, ids["gate"], approval_id)
        assert observed and row.status == "RUNNING"
        assert not session.in_transaction()
    async with execution_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None and approval.consumed_at is not None


async def test_caught_nested_failure_cannot_commit_partial_work(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"a": "PENDING"})
    async with execution_factory() as session:
        with pytest.raises(InvalidRequestError, match="rolled back"):
            async with immediate_transaction(session):
                await WorkflowStepRunRepository(session).transition_cas(
                    ids["a"], "READY", from_status="PENDING", commit=False
                )
                try:
                    async with immediate_transaction(session, commit=False):
                        raise RuntimeError("Nested failure caught by caller")
                except RuntimeError:
                    pass
        assert (await read_step(execution_factory, ids["a"])).status == "PENDING"
        assert await AuditLogRepository(session).list_for_run(run_id) == []


async def test_dispatch_cannot_bypass_snapshot_approval_or_attempt_budget(
    execution_factory: Factory,
) -> None:
    _, ids = await seed(execution_factory, {"gate": "READY"}, protected=True)
    async with execution_factory() as session:
        with pytest.raises(OperationNotPermitted, match="approval policy"):
            await WorkflowStepRunRepository(session).dispatch_cas(ids["gate"], "worker")
    _, other = await seed(execution_factory, {"a": "READY"})
    async with execution_factory() as session:
        await session.execute(
            update(WorkflowStepRun).where(WorkflowStepRun.id == other["a"]).values(attempt=3)
        )
        await session.commit()
        with pytest.raises(OperationNotPermitted, match="budget"):
            await WorkflowStepRunRepository(session).dispatch_cas(other["a"], "worker")


async def test_legacy_snapshot_and_vacuous_completion(execution_factory: Factory) -> None:
    async with execution_factory() as session:
        workflow = Workflow(name="legacy")
        session.add(workflow)
        await session.commit()
        run = await WorkflowRunRepository(session).create({"workflow_id": workflow.id})
        run_id = run.id
        assert run.template_snapshot_json is None
        await session.commit()
        await WorkflowRunRepository(session).transition_status(run_id, "running", ["pending"])
        assert await derive_and_persist_run_status(run_id, session) == "completed"


async def test_authorization_validity_is_anchored_to_resolution(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "READY"})
    async with execution_factory() as session:
        repo = ApprovalRepository(session)
        approval = await repo.create_bound(binding(run_id, ids["gate"]))
        approval_id = approval.id
        resolved_at = datetime.now(UTC) - timedelta(hours=2)
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id)
            .values(status="approved", resolved_at=resolved_at)
        )
        await session.commit()
        assert await repo.set_authorization_expires_at(approval_id)
        assert not await repo.set_authorization_expires_at(approval_id)
        assert not await repo.consume_atomic(
            approval_id,
            operation="implementation_authorization",
            parameters_hash=DIGEST,
            workflow_step_run_id=ids["gate"],
            actor_id="local_operator",
        )
        assert await repo.expire_pending() == 0
        persisted = await session.get(ApprovalRequest, approval_id, populate_existing=True)
        assert persisted is not None
        assert persisted.authorization_expires_at == resolved_at + timedelta(hours=1)
        assert persisted.status == "approved"
        with pytest.raises(ValueError, match="consume_atomic"):
            await repo.update(approval_id, {"consumed_at": None})


async def test_concurrent_approval_resolution_cannot_bypass_failure_stop(
    execution_factory: Factory,
) -> None:
    run_id, ids = await seed(
        execution_factory,
        {"failed": "FAILED", "gate": "WAITING_APPROVAL"},
        protected=True,
        run_status="waiting_approval",
    )
    async with execution_factory() as session:
        approval_id = (
            await ApprovalRepository(session).create_bound(binding(run_id, ids["gate"]))
        ).id

    async def resolve_and_derive() -> str:
        async with execution_factory() as session:
            async with immediate_transaction(session):
                repo = ApprovalRepository(session)
                await repo.resolve(approval_id, "approved", "operator", commit=False)
                await repo.set_authorization_expires_at(approval_id, commit=False)
                assert await WorkflowStepRunRepository(session).transition_cas(
                    ids["gate"], "READY", from_status="WAITING_APPROVAL", commit=False
                )
                return await derive_and_persist_run_status(run_id, session)

    async def derive() -> str:
        async with execution_factory() as session:
            return await derive_and_persist_run_status(run_id, session)

    assert tuple(await asyncio.gather(resolve_and_derive(), derive())) == ("running", "running")


async def test_released_running_lease_is_recoverable(execution_factory: Factory) -> None:
    _, ids = await seed(execution_factory, {"a": "RUNNING"})
    async with execution_factory() as session:
        repo = WorkflowStepRunRepository(session)
        assert await repo.release_lease(ids["a"], "worker", 1)
        assert [row.id for row in await repo.find_stale_running()] == [ids["a"]]
        await session.commit()
        assert await repo.transition_cas(
            ids["a"], "INTERRUPTED", from_status="RUNNING", lease_owner=None, attempt=1
        )


async def test_pending_downstream_after_success_keeps_run_running(
    execution_factory: Factory,
) -> None:
    run_id, _ = await seed(execution_factory, {"a": "SUCCEEDED", "b": "PENDING"})
    async with execution_factory() as session:
        assert await derive_and_persist_run_status(run_id, session) == "running"


async def test_re_gate_real_sqlite_lock_exhaustion_leaves_no_approval(
    execution_factory: Factory,
) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "WAITING_APPROVAL"})
    async with execution_factory() as blocker, execution_factory() as contender:
        await contender.execute(text("PRAGMA busy_timeout=1"))
        await contender.commit()
        async with immediate_transaction(blocker):
            with pytest.raises(DatabaseBusyError):
                await ApprovalRepository(contender).expire_and_re_gate(
                    ids["gate"], binding(run_id, ids["gate"])
                )
        assert not contender.in_transaction()
        assert await contender.scalar(select(func.count()).select_from(ApprovalRequest)) == 0
