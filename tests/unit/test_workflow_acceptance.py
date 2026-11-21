"""Additional Wave 1 acceptance evidence for security and persistence boundaries."""

import inspect
import json
import logging

import pytest

from devflow_api.executors.mock import MockExecutor
from devflow_api.models import WorkflowStepRun
from devflow_api.models.base import UTCDateTime
from devflow_api.repositories.approval import AuditLogRepository
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.executor import ExecutionContext, StepExecutionResult
from devflow_core.config import get_settings
from devflow_core.logging import configure_logging
from tests.unit.test_workflow_execution_repositories import Factory
from tests.unit.test_workflow_orchestration import approvals, create, protected, resolve, step

pytest_plugins = ["tests.unit.test_workflow_execution_repositories"]


async def test_no_secret_in_audit_or_logs(
    execution_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    synthetic_secret: str,
) -> None:
    monkeypatch.setenv("CHATGPT_API_KEY", synthetic_secret)
    get_settings.cache_clear()
    configure_logging()
    caplog.set_level(logging.DEBUG)
    orch, run_id = await create(
        execution_factory, [protected("gate", inputs={"api_token": synthetic_secret})]
    )
    await orch.start_or_resume(run_id)
    await resolve(execution_factory, (await approvals(execution_factory, run_id))[0].id)
    assert (await orch.start_or_resume(run_id)).status == "completed"
    async with execution_factory() as session:
        events = await AuditLogRepository(session).list_for_run(run_id)
        assert all(synthetic_secret not in (event.payload_json or "") for event in events)
    assert synthetic_secret not in caplog.text


async def test_env_allowlist_no_parent_inheritance_contract(
    execution_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVFLOW_PARENT_ONLY", "must-not-inherit")
    original = MockExecutor.execute
    contexts: list[ExecutionContext] = []

    async def observe(self: MockExecutor, context: ExecutionContext) -> StepExecutionResult:
        contexts.append(context)
        return await original(self, context)

    monkeypatch.setattr(MockExecutor, "execute", observe)
    orch, run_id = await create(execution_factory, [step("a")])
    assert (await orch.start_or_resume(run_id)).status == "completed"
    assert len(contexts) == 1
    assert contexts[0].env_allowlist == {} and contexts[0].working_directory is None


async def test_audit_append_only_no_update_delete(execution_factory: Factory) -> None:
    async with execution_factory() as session:
        repo = AuditLogRepository(session)
        event = await repo.log_event("acceptance", "system", payload={"document": "original"})
        with pytest.raises(TypeError, match="append-only"):
            await repo.update(event.id, {"payload_json": "{}"})
        with pytest.raises(TypeError, match="append-only"):
            await repo.delete(event.id)
        await session.refresh(event)
        assert json.loads(event.payload_json or "{}") == {"document": "original"}


def test_step_repository_has_no_single_step_delete() -> None:
    assert not hasattr(WorkflowStepRunRepository, "delete")
    assert "delete(" not in inspect.getsource(WorkflowStepRunRepository)


def test_execution_timestamps_use_utc_decorator() -> None:
    names = {
        "created_at",
        "updated_at",
        "ready_at",
        "dispatched_at",
        "started_at",
        "completed_at",
        "lease_expires_at",
    }
    assert all(isinstance(WorkflowStepRun.__table__.c[name].type, UTCDateTime) for name in names)


async def test_terminal_failure_populates_run_error(execution_factory: Factory) -> None:
    orch, run_id = await create(
        execution_factory,
        [step("a", tool_selection={"executor_id": "mock", "options": {"outcome": "failed"}})],
    )
    run = await orch.start_or_resume(run_id)
    assert run.status == "failed" and run.completed_at is not None
    assert run.error_message
