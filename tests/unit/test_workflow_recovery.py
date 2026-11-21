"""Deterministic restart recovery on durable SQLite state."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import update

from devflow_api.executors.mock import MockExecutor
from devflow_api.main import create_app
from devflow_api.models import WorkflowRun, WorkflowStepRun
from devflow_api.orchestration import RecoveryService, WorkflowOrchestrator
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.workflow_template import canonical_hash
from devflow_core.config import Settings
from devflow_core.exceptions import ConfigurationError
from tests.unit.test_workflow_execution_repositories import (
    Factory,
    approved,
    binding,
    dispatch_protected,
    seed,
)

pytest_plugins = ["tests.unit.test_workflow_execution_repositories"]


async def expire(factory: Factory, step_ids: list[int]) -> None:
    async with factory() as session:
        await session.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.id.in_(step_ids))
            .values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
                inputs_json="{}",
                input_digest=canonical_hash({}),
            )
        )
        await session.commit()


async def recover(factory: Factory) -> int:
    async with factory() as session:
        return await RecoveryService(session).recover()


async def test_recovery_interrupts_expired_lease_without_dispatch(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, ids = await seed(execution_factory, {"a": "RUNNING"})
    await expire(execution_factory, [ids["a"]])
    execute = AsyncMock()
    monkeypatch.setattr(MockExecutor, "execute", execute)
    assert await recover(execution_factory) == 1
    assert await recover(execution_factory) == 0
    execute.assert_not_awaited()
    async with execution_factory() as session:
        row = await WorkflowStepRunRepository(session).get(ids["a"])
        assert row is not None and row.status == "READY" and row.attempt == 1
        assert row.lease_owner is None and row.lease_expires_at is None
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert sum(event.event_type == "step_interrupted" for event in events) == 1
        assert sum(event.event_type == "step_recovery_decided" for event in events) == 1
        interrupted = next(event for event in events if event.event_type == "step_interrupted")
        assert json.loads(interrupted.payload_json or "{}")["prior_lease_owner"] == "worker"
        assert not await WorkflowStepRunRepository(session).persist_result_cas(
            ids["a"], "SUCCEEDED", lease_owner="worker", attempt=1
        )


async def test_valid_lease_survives_repeated_recovery(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"a": "RUNNING"})
    assert await recover(execution_factory) == 0
    assert await recover(execution_factory) == 0
    async with execution_factory() as session:
        row = await WorkflowStepRunRepository(session).get(ids["a"])
        assert row is not None and row.status == "RUNNING" and row.lease_owner == "worker"
        assert await AuditLogRepository(session).list_for_run(run_id) == []


@pytest.mark.parametrize(
    ("safe", "idempotent", "attempt", "target"),
    [
        (True, True, 1, "READY"),
        (False, True, 1, "FAILED"),
        (True, False, 1, "FAILED"),
        (True, True, 3, "FAILED"),
    ],
)
async def test_recovery_retry_policy_and_attempt_budget(
    execution_factory: Factory,
    safe: bool,
    idempotent: bool,
    attempt: int,
    target: str,
) -> None:
    run_id, ids = await seed(execution_factory, {"a": "RUNNING"})
    await expire(execution_factory, [ids["a"]])
    async with execution_factory() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.template_snapshot_json is not None
        snapshot = json.loads(run.template_snapshot_json)
        snapshot["steps"][0]["retry_policy"].update(safe=safe, idempotent=idempotent)
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(template_snapshot_json=json.dumps(snapshot))
        )
        await session.execute(
            update(WorkflowStepRun).where(WorkflowStepRun.id == ids["a"]).values(attempt=attempt)
        )
        await session.commit()
    assert await recover(execution_factory) == 1
    async with execution_factory() as session:
        row = await WorkflowStepRunRepository(session).get(ids["a"])
        assert row is not None and row.status == target and row.attempt == attempt


async def test_null_lease_is_recovered(execution_factory: Factory) -> None:
    _, ids = await seed(execution_factory, {"a": "RUNNING"})
    async with execution_factory() as session:
        await session.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.id == ids["a"])
            .values(lease_owner=None, lease_expires_at=None)
        )
        await session.commit()
    assert await recover(execution_factory) == 1


async def test_concurrent_recovery_reconciles_once(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"a": "RUNNING"})
    await expire(execution_factory, [ids["a"]])
    assert sum(await asyncio.gather(recover(execution_factory), recover(execution_factory))) == 1
    async with execution_factory() as session:
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert sum(event.event_type == "step_recovery_decided" for event in events) == 1


async def test_cancelled_run_recovery_settles_leftover_steps(execution_factory: Factory) -> None:
    run_id, ids = await seed(
        execution_factory, {"a": "RUNNING", "b": "WAITING_APPROVAL"}, run_status="cancelled"
    )
    async with execution_factory() as session:
        await ApprovalRepository(session).create_bound(binding(run_id, ids["b"]))
    assert await recover(execution_factory) == 0
    async with execution_factory() as session:
        assert {
            row.status for row in await WorkflowStepRunRepository(session).list_for_run(run_id)
        } == {"CANCELLED"}
        assert (await ApprovalRepository(session).list_for_run(run_id))[0].status == "expired"


async def test_recovery_failure_stop_settles_interrupted_and_gates(
    execution_factory: Factory,
) -> None:
    run_id, ids = await seed(
        execution_factory,
        {"failed": "FAILED", "a": "RUNNING", "gate": "WAITING_APPROVAL"},
        run_status="waiting_approval",
    )
    await expire(execution_factory, [ids["a"]])
    async with execution_factory() as session:
        await ApprovalRepository(session).create_bound(binding(run_id, ids["gate"]))
    assert await recover(execution_factory) == 1
    async with execution_factory() as session:
        rows = await WorkflowStepRunRepository(session).list_for_run(run_id)
        assert {row.status for row in rows} == {"FAILED"}
        interrupted = next(row for row in rows if row.step_key == "a")
        assert json.loads(interrupted.error_json or "{}")["category"] == "skipped"
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "failed"
        assert (await ApprovalRepository(session).list_for_run(run_id))[0].status == "expired"


async def test_protected_restart_requires_fresh_approval(execution_factory: Factory) -> None:
    run_id, ids = await seed(execution_factory, {"gate": "READY"}, protected=True)
    old_id = await approved(execution_factory, run_id, ids["gate"])
    async with execution_factory() as session:
        await dispatch_protected(session, ids["gate"], old_id)
    await expire(execution_factory, [ids["gate"]])
    assert await recover(execution_factory) == 1
    async with execution_factory() as session:
        orch = WorkflowOrchestrator(session)
        assert (await orch.start_or_resume(run_id)).status == "waiting_approval"
    async with execution_factory() as session:
        approvals = await ApprovalRepository(session).list_for_run(run_id)
        assert len(approvals) == 2 and approvals[0].consumed_at is not None
        assert approvals[1].status == "pending"
        row = await WorkflowStepRunRepository(session).get(ids["gate"])
        assert row is not None and row.attempt == 1


async def test_recovery_replaces_expired_approval_without_dispatch(
    execution_factory: Factory,
) -> None:
    from tests.unit.test_workflow_orchestration import create, protected

    orch, run_id = await create(execution_factory, [protected("gate")])
    await orch.start_or_resume(run_id)
    async with execution_factory() as session:
        old = (await ApprovalRepository(session).list_for_run(run_id))[0]
        await session.execute(
            update(type(old)).where(type(old).id == old.id).values(expires_at=datetime.now(UTC))
        )
        await session.commit()
    assert await recover(execution_factory) == 0
    assert await recover(execution_factory) == 0
    async with execution_factory() as session:
        rows = await ApprovalRepository(session).list_for_run(run_id)
        assert [row.status for row in rows] == ["expired", "pending"]


async def test_startup_recovery_uses_configured_database(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, ids = await seed(execution_factory, {"a": "RUNNING"})
    await expire(execution_factory, [ids["a"]])
    app = create_app(Settings(DATABASE_URL=str(execution_factory.kw["bind"].url)))
    execute = AsyncMock(wraps=MockExecutor().execute)
    monkeypatch.setattr(MockExecutor, "execute", execute)
    async with app.router.lifespan_context(app):
        assert app.state.recovered_steps == 1
        execute.assert_not_awaited()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/workflow-runs/{run_id}")
            assert response.status_code == 200 and response.json()["steps"][0]["status"] == "READY"
            response = await client.post(f"/api/v1/workflow-runs/{run_id}/start")
            assert response.status_code == 200 and response.json()["status"] == "completed"
            assert execute.await_count == 1


async def test_startup_requires_migrated_schema(tmp_path: Path) -> None:
    app = create_app(
        Settings(DATABASE_URL=f"sqlite+aiosqlite:///{(tmp_path / 'unmigrated.db').as_posix()}")
    )
    with pytest.raises(ConfigurationError, match="migration 008"):
        async with app.router.lifespan_context(app):
            pass
