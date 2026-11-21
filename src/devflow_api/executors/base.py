"""One-way executor protocol."""

from typing import Protocol

from pydantic import BaseModel

from devflow_api.schemas.executor import (
    Capability,
    ExecutionContext,
    ExecutorIdentifier,
    StepExecutionResult,
    TimeoutPolicy,
)
from devflow_api.schemas.workflow_template import ToolSelection


class Executor(Protocol):
    identifier: ExecutorIdentifier
    capabilities: list[Capability]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    timeout_policy: TimeoutPolicy

    def supports(self, tool_selection: ToolSelection) -> bool: ...

    async def execute(self, context: ExecutionContext) -> StepExecutionResult: ...
