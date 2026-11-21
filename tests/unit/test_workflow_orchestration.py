"""Group B workflows exercised through service entry points and durable SQLite state."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select, update

from devflow_api.executors.mock import MockExecutor
from devflow_api.executors.registry import ExecutorRegistry
from devflow_api.models import ApprovalRequest, Workflow, WorkflowRun, WorkflowStepRun
from devflow_api.orchestration import WorkflowOrchestrator, approval_service
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRepository, WorkflowRunRepository
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.executor import ExecutionContext, StepExecutionResult
from devflow_api.schemas.workflow_template import canonical_hash
from devflow_api.services.approval import ApprovalService
from devflow_core.config import get_settings
from devflow_core.exceptions import (
    ApprovalAlreadyResolvedError,
    ApprovalExpiredError,
    InvalidWorkflowTemplateError,
    OperationNotPermitted,
    RunNotStartableError,
)
from tests.unit.test_workflow_execution_repositories import (
    Factory,
)

pytest_plugins = ["tests.unit.test_workflow_execution_repositories"]


def step(key: str, **changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "key": key,
        "version": 1,
        "title": key,
        "executor_id": "mock",
        "tool_selection": {"executor_id": "mock"},
    }
    data.update(changes)
    return data


def protected(key: str, **changes: object) -> dict[str, object]:
    return step(key, approvable=True, protected_operation="implementation_authorization", **changes)


async def create(
    factory: Factory,
    steps: list[dict[str, object]] | None,
    registry: ExecutorRegistry | None = None,
) -> tuple[WorkflowOrchestrator, int]:
    async with factory() as session:
        workflow = await WorkflowRepository(session).create(
            {
                "name": "Workflow",
                "definition_json": json.dumps({"template_version": 1, "steps": steps})
                if steps is not None
                else None,
            }
        )
        orchestrator = WorkflowOrchestrator(session, registry)
        run = await orchestrator.create_run(workflow.id)
        return orchestrator, run.id


async def states(factory: Factory, run_id: int) -> dict[str, WorkflowStepRun]:
    async with factory() as session:
        return {
            row.step_key: row
            for row in await WorkflowStepRunRepository(session).list_for_run(run_id)
        }


async def approvals(factory: Factory, run_id: int) -> list[ApprovalRequest]:
    async with factory() as session:
        return await ApprovalRepository(session).list_for_run(run_id)


async def resolve(factory: Factory, approval_id: int, decision: str = "approved") -> str:
    async with factory() as session:
        service = approval_service(session)
        approved, run = await service.resolve(
            approval_id, "approved" if decision == "approved" else "rejected", "operator"
        )
        assert approved.status == decision
        return run.status


async def test_linear_chain_dispatches_in_order_and_resolves_inputs(
    execution_factory: Factory,
) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            step("a", inputs={"document": "source"}),
            step("b", depends_on=["a"], inputs={"source": "$steps.a.echo"}),
            step("c", depends_on=["b"], inputs={"nested": ["$steps.b.echo"]}),
        ],
    )
    assert (await orch.start_or_resume(run_id)).status == "completed"
    rows = await states(execution_factory, run_id)
    assert json.loads(rows["b"].inputs_json or "{}") == {"source": {"document": "source"}}
    assert json.loads(rows["c"].output_json or "{}")["echo"]["nested"] == [
        {"source": {"document": "source"}}
    ]
    async with execution_factory() as session:
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert [
            json.loads(event.payload_json or "{}")["step_key"]
            for event in events
            if event.event_type == "step_dispatched"
        ] == ["a", "b", "c"]
    with pytest.raises(RunNotStartableError):
        await orch.start_or_resume(run_id)


async def test_diamond_dependency_gates_until_all_succeeded(execution_factory: Factory) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            step("a"),
            step("b", depends_on=["a"]),
            step("c", depends_on=["a"]),
            step(
                "d", depends_on=["b", "c"], inputs={"b": "$steps.b.marker", "c": "$steps.c.marker"}
            ),
        ],
    )
    assert (await orch.start_or_resume(run_id)).status == "completed"
    rows = await states(execution_factory, run_id)
    assert json.loads(rows["d"].inputs_json or "{}") == {"b": "mock-v1", "c": "mock-v1"}


@pytest.mark.parametrize("reference", ["$steps.a.missing", "$steps.bad", "$steps.absent.echo"])
async def test_missing_reference_fails_pending_step(
    execution_factory: Factory, reference: str
) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            step("a"),
            step("b", depends_on=["a"], inputs={"source": reference}),
            step("c", depends_on=["b"]),
        ],
    )
    assert (await orch.start_or_resume(run_id)).status == "failed"
    rows = await states(execution_factory, run_id)
    assert rows["b"].attempt == 0
    assert json.loads(rows["b"].error_json or "{}")["code"] == "INPUT_RESOLUTION_FAILED"
    assert json.loads(rows["c"].error_json or "{}")["category"] == "skipped"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {"executor_id": "codex", "tool_selection": {"executor_id": "codex"}},
            "EXECUTOR_NOT_FOUND",
        ),
        ({"tool_selection": {"executor_id": "mock", "tool": "shell"}}, "CAPABILITY_MISMATCH"),
        (
            {
                "tool_selection": {
                    "executor_id": "mock",
                    "options": {"working_directory": "outside"},
                }
            },
            "WORKING_DIRECTORY_NOT_APPROVED",
        ),
    ],
)
async def test_pre_dispatch_validation_fails_without_execution(
    execution_factory: Factory,
    changes: dict[str, object],
    code: str,
) -> None:
    orch, run_id = await create(execution_factory, [step("a", **changes), step("b")])
    assert (await orch.start_or_resume(run_id)).status == "failed"
    rows = await states(execution_factory, run_id)
    assert rows["a"].attempt == rows["b"].attempt == 0
    assert json.loads(rows["a"].error_json or "{}")["code"] == code


@pytest.mark.parametrize(
    ("failures", "expected", "attempts"), [(2, "completed", 3), (3, "failed", 3)]
)
async def test_retry_budget_and_transient_state(
    execution_factory: Factory,
    failures: int,
    expected: str,
    attempts: int,
) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            step(
                "a",
                retry_policy={"safe": True, "idempotent": True, "max_attempts": 3},
                tool_selection={"executor_id": "mock", "options": {"fail_after_attempt": failures}},
            )
        ],
    )
    assert (await orch.start_or_resume(run_id)).status == expected
    assert (await states(execution_factory, run_id))["a"].attempt == attempts
    async with execution_factory() as session:
        transitions = [
            json.loads(event.payload_json or "{}")
            for event in await AuditLogRepository(session).list_for_run(run_id)
            if event.event_type == "step_transition"
        ]
    assert sum(event["from"] == "RUNNING" and event["to"] == "READY" for event in transitions) == 2


@pytest.mark.parametrize("mode", ["failed", "indeterminate"])
async def test_unsafe_or_indeterminate_not_retried(execution_factory: Factory, mode: str) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            step(
                "a",
                retry_policy={
                    "safe": mode == "indeterminate",
                    "idempotent": True,
                    "max_attempts": 3,
                },
                tool_selection={"executor_id": "mock", "options": {"outcome": mode}},
            )
        ],
    )
    assert (await orch.start_or_resume(run_id)).status == "failed"
    assert (await states(execution_factory, run_id))["a"].attempt == 1


async def test_multiple_gates_derive_and_protected_execution(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [protected("a"), protected("b")])
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    first, second = await approvals(execution_factory, run_id)
    assert await resolve(execution_factory, first.id) == "waiting_approval"
    assert await resolve(execution_factory, second.id) == "running"
    assert (await orch.start_or_resume(run_id)).status == "completed"
    assert all(
        approval.consumed_at is not None for approval in await approvals(execution_factory, run_id)
    )


async def test_approve_one_gate_can_execute_while_other_waits(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [protected("a"), protected("b")])
    await orch.start_or_resume(run_id)
    first, second = await approvals(execution_factory, run_id)
    await resolve(execution_factory, first.id)
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    rows = await states(execution_factory, run_id)
    assert rows["a"].status == "SUCCEEDED" and rows["b"].status == "WAITING_APPROVAL"
    await resolve(execution_factory, second.id)
    assert (await orch.start_or_resume(run_id)).status == "completed"


async def test_protected_retry_requires_fresh_authorization(execution_factory: Factory) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            protected(
                "a",
                retry_policy={"safe": True, "idempotent": True, "max_attempts": 2},
                tool_selection={"executor_id": "mock", "options": {"fail_after_attempt": 1}},
            )
        ],
    )
    await orch.start_or_resume(run_id)
    first = (await approvals(execution_factory, run_id))[0]
    await resolve(execution_factory, first.id)
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    first, second = await approvals(execution_factory, run_id)
    assert first.consumed_at is not None and second.status == "pending"
    assert (await states(execution_factory, run_id))["a"].attempt == 1
    await resolve(execution_factory, second.id)
    assert (await orch.start_or_resume(run_id)).status == "completed"
    assert (await states(execution_factory, run_id))["a"].attempt == 2


@pytest.mark.parametrize("swept", [False, True])
async def test_expiry_before_and_after_sweep_returns_same_error_and_regates(
    execution_factory: Factory,
    swept: bool,
) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    old = (await approvals(execution_factory, run_id))[0]
    async with execution_factory() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == old.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        if swept:
            await ApprovalRepository(session).expire_pending()
        with pytest.raises(ApprovalExpiredError):
            await approval_service(session).resolve(old.id, "approved", None)
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    old, new = await approvals(execution_factory, run_id)
    assert old.status == "expired" and new.status == "pending" and new.id != old.id
    await orch.start_or_resume(run_id)
    assert len(await approvals(execution_factory, run_id)) == 2


async def test_authorization_expiry_and_replay_regate(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    old = (await approvals(execution_factory, run_id))[0]
    await resolve(execution_factory, old.id)
    async with execution_factory() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == old.id)
            .values(authorization_expires_at=datetime.now(UTC))
        )
        await session.commit()
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    assert (await states(execution_factory, run_id))["a"].attempt == 0
    assert len(await approvals(execution_factory, run_id)) == 2
    with pytest.raises(ApprovalAlreadyResolvedError):
        await resolve(execution_factory, old.id)


async def test_rejection_cancels_all_steps_and_expires_other_gates(
    execution_factory: Factory,
) -> None:
    orch, run_id = await create(
        execution_factory, [protected("a"), protected("b"), step("c", depends_on=["a"])]
    )
    await orch.start_or_resume(run_id)
    first, _ = await approvals(execution_factory, run_id)
    assert await resolve(execution_factory, first.id, "rejected") == "cancelled"
    assert {row.status for row in (await states(execution_factory, run_id)).values()} == {
        "CANCELLED"
    }
    assert [row.status for row in await approvals(execution_factory, run_id)] == [
        "rejected",
        "expired",
    ]


async def test_concurrent_start_single_active_executor_per_run(execution_factory: Factory) -> None:
    class CountingMock(MockExecutor):
        active = 0
        maximum = 0
        calls: list[str] = []

        async def execute(self, context: ExecutionContext) -> StepExecutionResult:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls.append(context.step_key)
            try:
                await asyncio.sleep(0.03)
                return await super().execute(context)
            finally:
                self.active -= 1

    registry = ExecutorRegistry()
    executor = CountingMock()
    registry.register(executor)
    orch, run_id = await create(execution_factory, [step("a"), step("b"), step("c")], registry)
    results = await asyncio.gather(orch.start_or_resume(run_id), orch.start_or_resume(run_id))
    assert executor.maximum == 1 and executor.calls == ["a", "b", "c"]
    assert any(run.status == "completed" for run in results)


async def test_protected_executor_not_invoked_until_consumed_and_committed(
    execution_factory: Factory,
) -> None:
    class InspectingMock(MockExecutor):
        async def execute(self, context: ExecutionContext) -> StepExecutionResult:
            async with execution_factory() as session:
                approval = await session.scalar(
                    select(ApprovalRequest).where(
                        ApprovalRequest.workflow_step_run_id == context.step_run_id
                    )
                )
                assert approval is not None and approval.consumed_at is not None
                # A second writer can obtain the write lock while execution is in progress.
                await session.rollback()
                async with immediate_transaction(session):
                    pass
            return await super().execute(context)

    registry = ExecutorRegistry()
    registry.register(InspectingMock())
    orch, run_id = await create(execution_factory, [protected("a")], registry)
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    assert (await orch.start_or_resume(run_id)).status == "completed"


async def test_failed_consume_leaves_step_unexecuted_and_regates(
    execution_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ExecutorRegistry()
    executor = MockExecutor()
    execute = AsyncMock(wraps=executor.execute)
    monkeypatch.setattr(executor, "execute", execute)
    registry.register(executor)
    orch, run_id = await create(execution_factory, [protected("a")], registry)
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    monkeypatch.setattr(ApprovalRepository, "consume_atomic", AsyncMock(return_value=False))
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    row = (await states(execution_factory, run_id))["a"]
    assert row.status == "WAITING_APPROVAL" and row.attempt == 0 and row.lease_owner is None
    execute.assert_not_awaited()
    assert len(await approvals(execution_factory, run_id)) == 2


async def test_cancel_inflight_keeps_approval_spent_and_fences_result(
    execution_factory: Factory,
) -> None:
    started = asyncio.Event()

    class SlowMock(MockExecutor):
        async def execute(self, context: ExecutionContext) -> StepExecutionResult:
            started.set()
            return await super().execute(context)

    registry = ExecutorRegistry()
    registry.register(SlowMock())
    orch, run_id = await create(
        execution_factory,
        [
            protected("a", tool_selection={"executor_id": "mock", "options": {"sleep_s": 20}}),
            step("b", depends_on=["a"]),
        ],
        registry,
    )
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    task = asyncio.create_task(orch.start_or_resume(run_id))
    await asyncio.wait_for(started.wait(), 2)
    assert (await orch.cancel(run_id)).status == "cancelled"
    assert (await asyncio.wait_for(task, 2)).status == "cancelled"
    assert (await approvals(execution_factory, run_id))[0].consumed_at is not None
    assert {row.status for row in (await states(execution_factory, run_id)).values()} == {
        "CANCELLED"
    }
    async with execution_factory() as session:
        assert any(
            event.event_type == "step_stale_completion_discarded"
            for event in await AuditLogRepository(session).list_for_run(run_id)
        )


@pytest.mark.parametrize("legacy", [True, False])
async def test_cancel_pending_run_is_idempotent(execution_factory: Factory, legacy: bool) -> None:
    orch, run_id = await create(execution_factory, None if legacy else [step("a")])
    assert (await orch.cancel(run_id)).status == "cancelled"
    assert (await orch.cancel(run_id)).status == "cancelled"


async def test_legacy_zero_step_run_vacuously_completes(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, None)
    assert (await orch.start_or_resume(run_id)).status == "completed"


async def test_invalid_template_creation_rolls_back(execution_factory: Factory) -> None:
    async with execution_factory() as session:
        workflow = await WorkflowRepository(session).create(
            {"name": "invalid", "definition_json": "{}"}
        )
        orch = WorkflowOrchestrator(session)
        with pytest.raises(InvalidWorkflowTemplateError):
            await orch.create_run(workflow.id)
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 0


async def test_snapshot_survives_live_workflow_edit(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [step("original")])
    async with execution_factory() as session:
        await session.execute(update(Workflow).values(definition_json='{"broken": true}'))
        await session.commit()
    assert (await orch.start_or_resume(run_id)).status == "completed"
    assert list(await states(execution_factory, run_id)) == ["original"]


async def test_failed_workflow_settles_waiting_approval(execution_factory: Factory) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            protected("a"),
            step("b", tool_selection={"executor_id": "mock", "options": {"outcome": "failed"}}),
            step("c", depends_on=["b"]),
        ],
    )
    assert (await orch.start_or_resume(run_id)).status == "failed"
    rows = await states(execution_factory, run_id)
    assert rows["a"].status == rows["c"].status == "FAILED"
    assert json.loads(rows["a"].error_json or "{}")["category"] == "skipped"
    assert (await approvals(execution_factory, run_id))[0].status == "expired"


@pytest.mark.parametrize("malformed", ["output", "metadata", "exception", "none"])
async def test_malformed_execution_results_fail_safely(
    execution_factory: Factory,
    malformed: str,
) -> None:
    class BrokenMock(MockExecutor):
        async def execute(self, context: ExecutionContext) -> StepExecutionResult:
            result = await super().execute(context)
            if malformed == "exception":
                raise RuntimeError("Internal provider details")
            if malformed == "none":
                return cast(StepExecutionResult, None)
            if malformed == "metadata":
                result.metadata.executor_id = "another_executor"
            else:
                result.output = {"malformed": True}
            return result

    registry = ExecutorRegistry()
    registry.register(BrokenMock())
    orch, run_id = await create(execution_factory, [protected("a")], registry)
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    assert (await orch.start_or_resume(run_id)).status == "failed"
    row = (await states(execution_factory, run_id))["a"]
    assert row.output_json is None and row.artifact_refs_json is None and row.attempt == 1
    assert (await approvals(execution_factory, run_id))[0].consumed_at is not None


@pytest.mark.parametrize("safe", [False, True])
async def test_timeout_uses_failure_or_transient_retry_and_preserves_consumption(
    execution_factory: Factory,
    safe: bool,
) -> None:
    orch, run_id = await create(
        execution_factory,
        [
            protected(
                "a",
                timeout_s=1,
                retry_policy={"safe": safe, "idempotent": safe, "max_attempts": 2},
                tool_selection={"executor_id": "mock", "options": {"sleep_s": 20}},
            )
        ],
    )
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    assert (await orch.start_or_resume(run_id)).status == ("waiting_approval" if safe else "failed")
    row = (await states(execution_factory, run_id))["a"]
    assert row.status == ("WAITING_APPROVAL" if safe else "FAILED")
    assert json.loads(row.error_json or "{}")["category"] == "timeout"
    assert json.loads(row.error_json or "{}")["retryable"] == safe
    assert (await approvals(execution_factory, run_id))[0].consumed_at is not None


async def test_concurrent_bound_approval_requests_are_idempotent(
    execution_factory: Factory,
) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    step_id = (await states(execution_factory, run_id))["a"].id

    async def request() -> int:
        async with execution_factory() as session:
            return (await approval_service(session).request_bound_approval(run_id, step_id)).id

    first, second = await asyncio.gather(request(), request())
    assert first == second
    assert len(await approvals(execution_factory, run_id)) == 1


async def test_concurrent_approved_starts_consume_once(execution_factory: Factory) -> None:
    invocations = 0

    class ObservedMock(MockExecutor):
        async def execute(self, context: ExecutionContext) -> StepExecutionResult:
            nonlocal invocations
            invocations += 1
            await asyncio.sleep(0.03)
            return await super().execute(context)

    registry = ExecutorRegistry()
    registry.register(ObservedMock())
    orch, run_id = await create(execution_factory, [protected("a")], registry)
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    await asyncio.gather(orch.start_or_resume(run_id), orch.start_or_resume(run_id))
    assert invocations == 1
    assert (await states(execution_factory, run_id))["a"].attempt == 1
    assert (await approvals(execution_factory, run_id))[0].consumed_at is not None


@pytest.mark.parametrize(
    "field", ["operation", "actor_id", "parameters_hash", "workflow_step_run_id"]
)
async def test_mismatched_approval_regates_without_execution(
    execution_factory: Factory,
    field: str,
) -> None:
    orch, run_id = await create(execution_factory, [protected("a"), step("b", depends_on=["a"])])
    await orch.start_or_resume(run_id)
    old = (await approvals(execution_factory, run_id))[0]
    await resolve(execution_factory, old.id)
    rows = await states(execution_factory, run_id)
    invalid: dict[str, object] = {
        "operation": "pr_creation_approval",
        "actor_id": "forged",
        "parameters_hash": "0" * 64,
        "workflow_step_run_id": rows["b"].id,
    }
    async with execution_factory() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == old.id)
            .values(**{field: invalid[field]})
        )
        await session.commit()
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    assert (await states(execution_factory, run_id))["a"].attempt == 0
    assert len(await approvals(execution_factory, run_id)) == 2


async def test_failure_stop_suppresses_inflight_retry_and_settles_gates(
    execution_factory: Factory,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class ControlledMock(MockExecutor):
        async def execute(self, context: ExecutionContext) -> StepExecutionResult:
            started.set()
            await release.wait()
            return await super().execute(context)

    registry = ExecutorRegistry()
    registry.register(ControlledMock())
    orch, run_id = await create(
        execution_factory,
        [
            protected("gate"),
            step(
                "active",
                retry_policy={"safe": True, "idempotent": True, "max_attempts": 3},
                tool_selection={"executor_id": "mock", "options": {"outcome": "failed"}},
            ),
            step("failure", depends_on=["active"]),
        ],
        registry,
    )
    task = asyncio.create_task(orch.start_or_resume(run_id))
    await asyncio.wait_for(started.wait(), 2)
    rows = await states(execution_factory, run_id)
    async with execution_factory() as session:
        await session.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.id == rows["failure"].id)
            .values(status="FAILED", result_status="failed", error_json='{"category":"executor"}')
        )
        await session.commit()
    assert (await orch.start_or_resume(run_id)).status == "running"
    assert (await approvals(execution_factory, run_id))[0].status == "expired"
    release.set()
    assert (await asyncio.wait_for(task, 2)).status == "failed"
    assert (await states(execution_factory, run_id))["active"].attempt == 1
    async with execution_factory() as session:
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert any(
            json.loads(event.payload_json or "{}").get("reason") == "run_failure_stop"
            for event in events
            if event.event_type == "step_failed"
        )


async def test_resolve_cannot_bypass_failure_stop(execution_factory: Factory) -> None:
    orch, run_id = await create(
        execution_factory, [protected("gate"), step("failure", depends_on=["gate"])]
    )
    await orch.start_or_resume(run_id)
    rows = await states(execution_factory, run_id)
    old = (await approvals(execution_factory, run_id))[0]
    async with execution_factory() as session:
        await session.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.id == rows["failure"].id)
            .values(
                status="FAILED", result_status="indeterminate", error_json='{"category":"executor"}'
            )
        )
        await session.commit()
        with pytest.raises(OperationNotPermitted):
            await approval_service(session).resolve(old.id, "approved", None)
    assert (await approvals(execution_factory, run_id))[0].status == "expired"
    async with execution_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "failed"


async def test_concurrent_expiry_reconciliation_creates_one_replacement(
    execution_factory: Factory,
) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    old = (await approvals(execution_factory, run_id))[0]
    async with execution_factory() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == old.id)
            .values(expires_at=datetime.now(UTC))
        )
        await session.commit()

    async def reconcile() -> int:
        async with execution_factory() as session:
            return await approval_service(session).reconcile_expired(run_id)

    await asyncio.gather(reconcile(), reconcile())
    assert [approval.status for approval in await approvals(execution_factory, run_id)] == [
        "expired",
        "pending",
    ]


async def test_configured_secret_absent_from_execution_audit(
    execution_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_secret: str,
) -> None:
    token = synthetic_secret
    monkeypatch.setenv("CHATGPT_API_KEY", token)
    get_settings.cache_clear()
    orch, run_id = await create(
        execution_factory,
        [
            protected(
                "a",
                inputs={"api_token": token},
                tool_selection={
                    "executor_id": "mock",
                    "options": {"outcome": "failed", "code": token},
                },
            )
        ],
    )
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    assert (await orch.start_or_resume(run_id)).status == "failed"
    async with execution_factory() as session:
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert all(token not in (event.payload_json or "") for event in events)


async def test_bound_approval_audit_failure_rolls_back_resolution(
    execution_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    old = (await approvals(execution_factory, run_id))[0]
    async with execution_factory() as session:
        audit = AuditLogRepository(session)
        monkeypatch.setattr(audit, "log_event", AsyncMock(side_effect=RuntimeError("Audit failed")))
        service = ApprovalService(
            ApprovalRepository(session), WorkflowRunRepository(session), audit
        )
        with pytest.raises(RuntimeError):
            await service.resolve(old.id, "approved", None)
    old = (await approvals(execution_factory, run_id))[0]
    assert (
        old.status == "pending" and old.resolved_at is None and old.authorization_expires_at is None
    )
    assert (await states(execution_factory, run_id))["a"].status == "WAITING_APPROVAL"


async def test_run_creation_audit_failure_rolls_back_all_rows(
    execution_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with execution_factory() as session:
        workflow = await WorkflowRepository(session).create(
            {
                "name": "atomic",
                "definition_json": json.dumps({"template_version": 1, "steps": [step("a")]}),
            }
        )
        orch = WorkflowOrchestrator(session)
        original = AuditLogRepository.log_event

        async def fail(
            self: AuditLogRepository,
            event_type: str,
            actor: str,
            workflow_run_id: int | None = None,
            payload: dict[str, object] | None = None,
            *,
            commit: bool = True,
        ) -> object:
            if event_type == "run_created":
                raise RuntimeError("Run audit failed")
            return await original(self, event_type, actor, workflow_run_id, payload, commit=commit)

        monkeypatch.setattr(AuditLogRepository, "log_event", fail)
        with pytest.raises(RuntimeError):
            await orch.create_run(workflow.id)
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 0
        assert await session.scalar(select(func.count()).select_from(WorkflowStepRun)) == 0


async def test_concurrent_approval_resolution_has_one_winner(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    approval = (await approvals(execution_factory, run_id))[0]
    outcomes = await asyncio.gather(
        resolve(execution_factory, approval.id),
        resolve(execution_factory, approval.id, "rejected"),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, ApprovalAlreadyResolvedError) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, str) for outcome in outcomes) == 1
    rows = await states(execution_factory, run_id)
    persisted = (await approvals(execution_factory, run_id))[0]
    assert rows["a"].status == ("READY" if persisted.status == "approved" else "CANCELLED")


async def test_approval_cancel_race_cannot_dispatch(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    old = (await approvals(execution_factory, run_id))[0]
    await asyncio.gather(
        resolve(execution_factory, old.id), orch.cancel(run_id), return_exceptions=True
    )
    async with execution_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "cancelled"
    row = (await states(execution_factory, run_id))["a"]
    assert row.status == "CANCELLED" and row.attempt == 0


async def test_cancel_released_running_step(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [step("a")])
    async with execution_factory() as session:
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id).values(status="running")
        )
        await session.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.workflow_run_id == run_id)
            .values(status="RUNNING", attempt=1)
        )
        await session.commit()
    assert (await orch.cancel(run_id)).status == "cancelled"
    assert (await states(execution_factory, run_id))["a"].status == "CANCELLED"


async def test_on_approval_resolved_resumes_authorized_step(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    await orch.start_or_resume(run_id)
    old = (await approvals(execution_factory, run_id))[0]
    assert (await orch.on_approval_resolved(old.id, "approved")).status == "completed"


@pytest.mark.parametrize("phase2", [False, True])
async def test_existing_resolve_endpoint_expiry_semantics(
    session_factory: Factory,
    test_client: httpx.AsyncClient,
    phase2: bool,
) -> None:
    if phase2:
        orch, run_id = await create(session_factory, [protected("a")])
        await orch.start_or_resume(run_id)
        old_id = (await approvals(session_factory, run_id))[0].id
    else:
        async with session_factory() as session:
            workflow = await WorkflowRepository(session).create({"name": "legacy"})
            run = await WorkflowRunRepository(session).create(
                {"workflow_id": workflow.id, "status": "waiting_approval"}
            )
            approval = await ApprovalRepository(session).create(
                {
                    "workflow_run_id": run.id,
                    "step_name": "legacy",
                    "action_description": "Review",
                    "status": "expired",
                }
            )
            old_id = approval.id
    async with session_factory() as session:
        if phase2:
            await session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == old_id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
    response = await test_client.post(
        f"/api/v1/approvals/{old_id}/resolve", json={"decision": "approved"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == (
        "APPROVAL_EXPIRED" if phase2 else "APPROVAL_ALREADY_RESOLVED"
    )
    repeated = await test_client.post(
        f"/api/v1/approvals/{old_id}/resolve", json={"decision": "approved"}
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == (
        "APPROVAL_EXPIRED" if phase2 else "APPROVAL_ALREADY_RESOLVED"
    )


async def test_concurrent_initial_gates_create_one_pending_approval(
    execution_factory: Factory,
) -> None:
    orch, run_id = await create(execution_factory, [protected("a")])
    async with execution_factory() as session:
        await WorkflowRunRepository(session).transition_status(run_id, "running", ["pending"])
        row = (await states(execution_factory, run_id))["a"]
        await WorkflowStepRunRepository(session).transition_cas(
            row.id,
            "READY",
            from_status="PENDING",
            values={"inputs_json": "{}", "input_digest": canonical_hash({})},
        )

    async def gate() -> int:
        async with execution_factory() as session:
            return (await approval_service(session).request_bound_approval(run_id, row.id)).id

    first, second = await asyncio.gather(gate(), gate())
    assert first == second and len(await approvals(execution_factory, run_id)) == 1
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"


async def test_late_success_after_cancellation_is_discarded(execution_factory: Factory) -> None:
    ready, release = asyncio.Event(), asyncio.Event()

    class LateMock(MockExecutor):
        async def execute(self, context: ExecutionContext) -> StepExecutionResult:
            result = await super().execute(context)
            ready.set()
            await release.wait()
            return result

    registry = ExecutorRegistry()
    registry.register(LateMock())
    orch, run_id = await create(execution_factory, [step("a")], registry)
    task = asyncio.create_task(orch.start_or_resume(run_id))
    await asyncio.wait_for(ready.wait(), 2)
    await orch.cancel(run_id)
    release.set()
    assert (await asyncio.wait_for(task, 2)).status == "cancelled"
    row = (await states(execution_factory, run_id))["a"]
    assert row.status == "CANCELLED" and row.output_json is None and row.artifact_refs_json is None


async def test_legacy_resume_preserves_phase1_approval_gate(execution_factory: Factory) -> None:
    orch, run_id = await create(execution_factory, None)
    async with execution_factory() as session:
        await WorkflowRunRepository(session).transition_status(run_id, "running", ["pending"])
        approval_id = (
            await approval_service(session).request_approval(
                run_id, "Legacy review", "Review the legacy run"
            )
        ).id
    assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    await resolve(execution_factory, approval_id)
    assert (await orch.start_or_resume(run_id)).status == "completed"
