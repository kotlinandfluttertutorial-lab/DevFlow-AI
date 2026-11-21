"""Deterministic, cooperative mock execution with JSON inputs and outputs."""

import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel

from devflow_api.schemas.executor import (
    ArtifactRef,
    Capability,
    ExecutionContext,
    ExecutionMetadata,
    ExecutorIdentifier,
    MockInput,
    MockOptions,
    MockOutput,
    StepExecutionResult,
    StructuredError,
    TimeoutPolicy,
)
from devflow_api.schemas.workflow_template import ToolSelection, canonical_hash


class MockExecutor:
    safe = True
    idempotent = True
    identifier = ExecutorIdentifier(id="mock", version=1)
    capabilities = [Capability(operation="noop")]
    input_schema: type[BaseModel] = MockInput
    output_schema: type[BaseModel] = MockOutput
    timeout_policy = TimeoutPolicy(default_s=60, max_s=300)

    def supports(self, tool_selection: ToolSelection) -> bool:
        return tool_selection.executor_id == "mock" and tool_selection.tool in (None, "noop")

    async def execute(self, context: ExecutionContext) -> StepExecutionResult:
        MockInput.model_validate(context.inputs)
        options = MockOptions.model_validate(context.tool_selection.options or {})
        started_at = datetime.now(UTC)
        if options.sleep_s and not context.cancellation.cancelled:
            try:
                await asyncio.wait_for(context.cancellation.wait(), timeout=options.sleep_s)
            except TimeoutError:
                pass
        outcome = options.outcome
        if options.fail_after_attempt is not None:
            outcome = "failed" if context.attempt <= options.fail_after_attempt else "succeeded"
        if context.cancellation.cancelled:
            outcome = "cancelled"
        metadata = ExecutionMetadata(
            executor_id="mock",
            executor_version=1,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
        if outcome == "succeeded":
            output = MockOutput(echo=context.inputs, marker="mock-v1").model_dump(mode="json")
            return StepExecutionResult(
                outcome="succeeded",
                output=output,
                metadata=metadata,
                artifact_refs=[ArtifactRef(name="mock-artifact", digest=canonical_hash(output))],
            )
        return StepExecutionResult(
            outcome=outcome,
            metadata=metadata,
            error=StructuredError(
                code=options.code,
                category="cancelled" if outcome == "cancelled" else "executor",
                message="Mock execution completed without success",
                retryable=outcome == "failed",
                attempt=context.attempt,
            ),
        )
