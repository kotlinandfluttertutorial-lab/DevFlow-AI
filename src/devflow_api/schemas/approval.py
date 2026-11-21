from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from devflow_api.schemas.workflow_template import ApprovalOperation

type ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]
type ApprovalDecision = Literal["approved", "rejected"]


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_run_id: int
    step_name: str
    action_description: str
    status: str
    requested_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    operation: ApprovalOperation | None = None
    workflow_step_run_id: int | None = None
    parameters_hash: str | None = None
    actor_id: str | None = None
    expires_at: datetime | None = None
    authorization_expires_at: datetime | None = None
    consumed_at: datetime | None = None


class ResolveApprovalRequest(BaseModel):
    decision: ApprovalDecision
    resolved_by: str | None = None


class BoundApprovalData(BaseModel):
    """Trusted server-side data for a Phase 2 approval."""

    model_config = ConfigDict(extra="forbid")
    workflow_run_id: int = Field(gt=0)
    workflow_step_run_id: int = Field(gt=0)
    operation: ApprovalOperation
    parameters_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_id: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    action_description: str
    payload: dict[str, JsonValue] | None = None
