"""Workflow approval read and resolution endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.database import get_db
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRunRepository
from devflow_api.routers.workflows import ExecutionSettings, ResourceId
from devflow_api.schemas.approval import ApprovalResponse, ApprovalStatus, ResolveApprovalRequest
from devflow_api.schemas.common import ErrorResponse
from devflow_api.services.approval import ApprovalService
from devflow_api.services.workflow_projection import public_model

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])
type Session = Annotated[AsyncSession, Depends(get_db)]
_NOT_FOUND: dict[int | str, dict[str, object]] = {404: {"model": ErrorResponse}}
_VALIDATION: dict[int | str, dict[str, object]] = {422: {"model": ErrorResponse}}


@router.get("", responses=_VALIDATION)
async def list_approvals(
    session: Session, config: ExecutionSettings, status: ApprovalStatus | None = None
) -> list[ApprovalResponse]:
    rows = await ApprovalRepository(session).list_by_status(status)
    return [public_model(ApprovalResponse.model_validate(row), config) for row in rows]


@router.get("/{id}", responses={**_NOT_FOUND, **_VALIDATION})
async def get_approval(
    id: ResourceId, session: Session, config: ExecutionSettings
) -> ApprovalResponse:
    approval = await ApprovalRepository(session).get(id)
    if approval is None:
        raise HTTPException(404)
    return public_model(ApprovalResponse.model_validate(approval), config)


@router.post(
    "/{id}/resolve", responses={**_NOT_FOUND, **_VALIDATION, 409: {"model": ErrorResponse}}
)
async def resolve_approval(
    id: ResourceId, body: ResolveApprovalRequest, session: Session, config: ExecutionSettings
) -> ApprovalResponse:
    service = ApprovalService(
        ApprovalRepository(session, config),
        WorkflowRunRepository(session),
        AuditLogRepository(session),
    )
    try:
        approval, _ = await service.resolve(id, body.decision, body.resolved_by)
    except LookupError as exc:
        raise HTTPException(404) from exc
    return public_model(ApprovalResponse.model_validate(approval), config)
