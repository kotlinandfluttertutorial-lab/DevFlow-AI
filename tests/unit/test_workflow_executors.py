"""Executor contracts, determinism, and mandatory dependency/security guards."""

import ast
import asyncio
import builtins
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from devflow_api.executors.mock import MockExecutor
from devflow_api.executors.registry import ExecutorRegistry
from devflow_api.schemas.executor import (
    CancellationHandle,
    ExecutionContext,
    ExecutionMetadata,
    MockInput,
    StepExecutionResult,
    StructuredError,
    TimeoutPolicy,
)
from devflow_api.schemas.workflow_template import ToolSelection, canonical_hash
from devflow_core.exceptions import ExecutorSelectionError
from tests.conftest import ROOT


def context(options: dict[str, object] | None = None, attempt: int = 1) -> ExecutionContext:
    return ExecutionContext.model_validate(
        {
            "run_id": 1,
            "step_run_id": 1,
            "step_key": "a",
            "step_version": 1,
            "inputs": {"value": "document"},
            "tool_selection": {"executor_id": "mock", "options": options},
            "timeout_s": 60,
            "cancellation": CancellationHandle(),
            "actor_id": "local_operator",
            "idempotency_key": f"1:a:{attempt}",
            "input_digest": canonical_hash({"value": "document"}),
            "attempt": attempt,
        }
    )


async def test_mock_executor_deterministic_output() -> None:
    executor = MockExecutor()
    assert executor.safe and executor.idempotent
    first = await executor.execute(context())
    second = await executor.execute(context())
    assert first.output == second.output == {"echo": {"value": "document"}, "marker": "mock-v1"}
    assert first.artifact_refs == second.artifact_refs
    assert first.error is None


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "indeterminate"])
async def test_mock_failure_modes(outcome: str) -> None:
    result = await MockExecutor().execute(context({"outcome": outcome}))
    assert result.outcome == outcome and result.error is not None
    assert result.error.retryable == (outcome == "failed")
    assert result.output is None and result.artifact_refs == []


async def test_mock_attempt_failure_mode() -> None:
    assert (await MockExecutor().execute(context({"fail_after_attempt": 2}, 2))).outcome == "failed"
    assert (
        await MockExecutor().execute(context({"fail_after_attempt": 2}, 3))
    ).outcome == "succeeded"


async def test_mock_cooperative_cancellation() -> None:
    execution = context({"sleep_s": 20})
    task = asyncio.create_task(MockExecutor().execute(execution))
    await execution.cancellation.cancel()
    assert (await asyncio.wait_for(task, 1)).outcome == "cancelled"


def test_mock_executor_only_registered_executor() -> None:
    registry = ExecutorRegistry()
    assert registry.list_executors() == ["mock"]
    with pytest.raises(ExecutorSelectionError) as error:
        registry.select("codex", ToolSelection(executor_id="codex"))
    assert error.value.code == "EXECUTOR_NOT_FOUND"
    with pytest.raises(ExecutorSelectionError) as mismatch:
        registry.select("mock", ToolSelection(executor_id="mock", tool="shell"))
    assert mismatch.value.code == "CAPABILITY_MISMATCH"


@pytest.mark.parametrize(
    "payload", [{"invalid": float("nan")}, {"value": object()}, "not an object"]
)
def test_executor_input_validation(payload: object) -> None:
    with pytest.raises((ValidationError, ValueError, TypeError)):
        MockInput.model_validate(payload)


def test_executor_contract_rejects_malformed_results() -> None:
    metadata = ExecutionMetadata(
        executor_id="mock",
        executor_version=1,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        StepExecutionResult(outcome="succeeded", metadata=metadata)
    with pytest.raises(ValidationError):
        StepExecutionResult(outcome="failed", output={"bad": True}, metadata=metadata)
    with pytest.raises(ValidationError):
        StepExecutionResult(
            outcome="indeterminate",
            metadata=metadata,
            error=StructuredError(code="x", category="executor", message="", retryable=True),
        )
    with pytest.raises(ValidationError):
        TimeoutPolicy(default_s=3, max_s=2)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_no_shell_execution_in_executors_package() -> None:
    forbidden = {"os", "subprocess", "socket", "httpx", "requests", "pathlib", "shutil"}
    for path in (ROOT / "src/devflow_api/executors").glob("*.py"):
        assert not {module.split(".")[0] for module in imports(path)} & forbidden
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                assert not (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"open", "eval", "exec", "__import__"}
                )
                assert not any(keyword.arg == "shell" for keyword in node.keywords)


def test_executors_do_not_import_repositories_services_models() -> None:
    for path in (ROOT / "src/devflow_api/executors").glob("*.py"):
        assert not any(
            module.startswith(
                (
                    "devflow_api.repositories",
                    "devflow_api.services",
                    "devflow_api.models",
                )
            )
            for module in imports(path)
        )


def test_schemas_do_not_import_executors_package() -> None:
    for path in (ROOT / "src/devflow_api/schemas").glob("*.py"):
        assert not any(module.startswith("devflow_api.executors") for module in imports(path))


async def test_mock_executor_no_fs_network_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    execution = context()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Executor attempted an external side effect")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert (await MockExecutor().execute(execution)).outcome == "succeeded"


def test_registry_rejects_non_mock_registration() -> None:
    registry = ExecutorRegistry()
    executor = MockExecutor()
    executor.identifier = executor.identifier.model_copy(update={"id": "external"})
    with pytest.raises(ExecutorSelectionError):
        registry.register(executor)
    assert registry.list_executors() == ["mock"]
