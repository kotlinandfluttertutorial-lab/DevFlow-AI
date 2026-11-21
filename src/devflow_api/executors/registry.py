"""Executor selection and registration."""

from devflow_api.executors.base import Executor
from devflow_api.executors.mock import MockExecutor
from devflow_api.schemas.workflow_template import ToolSelection
from devflow_core.exceptions import ExecutorSelectionError


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}
        self.register(MockExecutor())

    def register(self, executor: Executor) -> None:
        if executor.identifier.id != "mock":
            raise ExecutorSelectionError("EXECUTOR_NOT_FOUND")
        self._executors[executor.identifier.id] = executor

    def get(self, executor_id: str) -> Executor | None:
        return self._executors.get(executor_id)

    def select(self, executor_id: str, tool_selection: ToolSelection) -> Executor:
        executor = self.get(executor_id)
        if executor is None:
            raise ExecutorSelectionError("EXECUTOR_NOT_FOUND")
        if not executor.supports(tool_selection):
            raise ExecutorSelectionError("CAPABILITY_MISMATCH")
        return executor

    def list_executors(self) -> list[str]:
        return sorted(self._executors)
