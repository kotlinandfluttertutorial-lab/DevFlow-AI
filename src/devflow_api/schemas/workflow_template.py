"""Versioned workflow definitions, validated before execution snapshots are created."""

import hashlib
import json
from graphlib import CycleError, TopologicalSorter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from devflow_core.exceptions import InvalidWorkflowTemplateError

ApprovalOperation = Literal[
    "specification_approval",
    "implementation_authorization",
    "pr_creation_approval",
    "merge_approval",
]


class TemplateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolSelection(TemplateModel):
    executor_id: str = Field(min_length=1)
    tool: str | None = None
    options: dict[str, JsonValue] | None = None


class RetryPolicy(TemplateModel):
    safe: bool = False
    idempotent: bool = False
    max_attempts: int = Field(default=1, ge=1, strict=True)


class StepDefinition(TemplateModel):
    key: str = Field(min_length=1)
    version: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1)
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    executor_id: str = Field(min_length=1)
    tool_selection: ToolSelection
    inputs: dict[str, JsonValue] | None = None
    expected_outputs: list[str] | None = None
    timeout_s: int = Field(default=60, ge=1, strict=True)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    approvable: bool = False
    protected_operation: ApprovalOperation | None = None

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        if self.approvable != (self.protected_operation is not None):
            raise ValueError("protected_operation must be set exactly when approvable is true")
        if self.executor_id != self.tool_selection.executor_id:
            raise ValueError("executor_id must match tool_selection.executor_id")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("Duplicate dependencies")
        canonical_json(self.inputs)
        canonical_json(self.tool_selection.options)
        return self


class TemplateDefaults(TemplateModel):
    executor_id: str = Field(default="mock", min_length=1)
    timeout_s: int = Field(default=60, ge=1, strict=True)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


class WorkflowTemplate(TemplateModel):
    template_version: int = Field(ge=1, strict=True)
    defaults: TemplateDefaults = Field(default_factory=TemplateDefaults)
    steps: list[StepDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        keys = {step.key for step in self.steps}
        if len(keys) != len(self.steps):
            raise ValueError("Duplicate step keys")
        for step in self.steps:
            if not set(step.depends_on) <= keys:
                raise ValueError(f"Unknown dependency for step {step.key}")
        try:
            tuple(
                TopologicalSorter({step.key: step.depends_on for step in self.steps}).static_order()
            )
        except CycleError as exc:
            raise ValueError("Workflow dependencies must be acyclic") from exc
        return self


def canonical_json(payload: object) -> str:
    """Preserve string content and JSON types, sort mapping keys, and reject non-finite numbers."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_hash(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def parse_workflow_template(definition_json: str) -> WorkflowTemplate:
    try:
        return WorkflowTemplate.model_validate_json(definition_json)
    except (ValidationError, ValueError) as exc:
        raise InvalidWorkflowTemplateError(str(exc)) from exc
