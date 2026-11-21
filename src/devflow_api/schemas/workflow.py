from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from devflow_api.schemas.approval import ApprovalResponse
from devflow_api.schemas.executor import ArtifactRef, StructuredError
from devflow_api.schemas.workflow_template import ToolSelection

type WorkflowStatus = Literal["draft", "active", "paused", "completed", "failed"]
type WorkflowName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WorkflowCreate(BaseModel):
    name: WorkflowName
    description: str | None = None
    project_id: int | None = None
    status: WorkflowStatus = "draft"
    definition_json: str | None = None


class WorkflowUpdate(BaseModel):
    name: WorkflowName | None = None
    description: str | None = None
    project_id: int | None = None
    status: WorkflowStatus | None = None
    definition_json: str | None = None


class WorkflowRunAction(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepRunResponse(BaseModel):
    id: int
    step_key: str
    step_version: int
    executor_id: str
    status: str
    attempt: int
    result_status: str | None
    tool_selection: ToolSelection | None
    ready_at: datetime | None
    dispatched_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    output: dict[str, JsonValue] | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    error: StructuredError | None = None


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    project_id: int | None
    status: str
    created_at: datetime
    updated_at: datetime


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_id: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    approvals: list[ApprovalResponse] = Field(default_factory=list)
    pending_approvals: list[ApprovalResponse] = Field(default_factory=list)
    steps: list[StepRunResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_run_id: int | None
    event_type: str
    actor: str
    payload_json: str | None
    created_at: datetime
