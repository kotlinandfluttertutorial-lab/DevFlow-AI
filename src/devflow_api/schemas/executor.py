"""Persistence-independent execution contracts."""

import asyncio
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PrivateAttr, model_validator

from devflow_api.schemas.workflow_template import ToolSelection, canonical_json


class ExecutorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutorIdentifier(ExecutorModel):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)


class Capability(ExecutorModel):
    operation: str
    tool: str | None = None


class TimeoutPolicy(ExecutorModel):
    default_s: int = Field(gt=0)
    max_s: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.default_s > self.max_s:
            raise ValueError("Default timeout exceeds maximum timeout")
        return self


class CancellationHandle(ExecutorModel):
    cancelled: bool = False
    _event: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)

    async def cancel(self) -> None:
        self.cancelled = True
        self._event.set()

    async def wait(self) -> None:
        if not self.cancelled:
            await self._event.wait()


class ArtifactRef(ExecutorModel):
    name: str
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    uri: str | None = None


class StructuredError(ExecutorModel):
    code: str
    category: Literal["executor", "timeout", "cancelled", "input_resolution", "internal", "skipped"]
    message: str
    retryable: bool = False
    attempt: int = Field(default=0, ge=0)


class ExecutionMetadata(ExecutorModel):
    executor_id: str
    executor_version: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
    logs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.started_at.utcoffset() is None or self.finished_at.utcoffset() is None:
            raise ValueError("Execution timestamps must be timezone aware")
        if self.finished_at < self.started_at:
            raise ValueError("Execution finish precedes start")
        return self


class ExecutionContext(ExecutorModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: int
    step_run_id: int
    step_key: str
    step_version: int
    inputs: dict[str, JsonValue]
    tool_selection: ToolSelection
    working_directory: str | None = None
    env_allowlist: dict[str, str] = Field(default_factory=dict)
    timeout_s: int = Field(gt=0)
    cancellation: CancellationHandle
    actor_id: str
    idempotency_key: str
    input_digest: str
    attempt: int = Field(ge=1)


class StepExecutionResult(ExecutorModel):
    outcome: Literal["succeeded", "failed", "cancelled", "indeterminate"]
    output: dict[str, JsonValue] | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    error: StructuredError | None = None
    metadata: ExecutionMetadata

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        canonical_json(self.output)
        if self.outcome == "succeeded":
            if self.output is None or self.error is not None:
                raise ValueError("Success requires output and a clean error field")
        elif self.error is None or self.output is not None or self.artifact_refs:
            raise ValueError("Unsuccessful execution requires an error and empty outputs")
        if self.outcome == "indeterminate" and self.error is not None and self.error.retryable:
            raise ValueError("Indeterminate outcomes cannot be retryable")
        return self


class MockInput(ExecutorModel):
    """The mock accepts any JSON object."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def validate_json(cls, value: object) -> object:
        canonical_json(value)
        return value


class MockOutput(ExecutorModel):
    echo: dict[str, JsonValue]
    marker: Literal["mock-v1"]


class MockOptions(ExecutorModel):
    outcome: Literal["succeeded", "failed", "cancelled", "indeterminate"] = "succeeded"
    sleep_s: float = Field(default=0, ge=0, le=300, allow_inf_nan=False)
    fail_after_attempt: int | None = Field(default=None, ge=1)
    code: str = "MOCK_FAILURE"
    working_directory: str | None = None
