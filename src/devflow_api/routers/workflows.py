"""Persistent workflow and read-only run history endpoints."""

import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.database import get_db
from devflow_api.models.project import Project
from devflow_api.models.workflow import Workflow, WorkflowRun
from devflow_api.orchestration import WorkflowOrchestrator
from devflow_api.repositories.approval import AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRepository, WorkflowRunRepository
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.common import ErrorResponse
from devflow_api.schemas.workflow import (
    AuditLogEntry,
    StepRunResponse,
    WorkflowCreate,
    WorkflowResponse,
    WorkflowRunAction,
    WorkflowRunResponse,
    WorkflowStatus,
    WorkflowUpdate,
)
from devflow_api.services.workflow_projection import audit_payload, run_response, step_response
from devflow_core.config import Settings
from devflow_core.exceptions import InvalidStatusTransitionError, PausedWorkflowError

router = APIRouter(prefix="/api/v1", tags=["workflows"])
type Session = Annotated[AsyncSession, Depends(get_db)]
ResourceId = Annotated[int, Path(gt=0)]
_NOT_FOUND: dict[int | str, dict[str, object]] = {404: {"model": ErrorResponse}}
_VALIDATION: dict[int | str, dict[str, object]] = {422: {"model": ErrorResponse}}


async def _require_workflow(id: int, session: AsyncSession) -> Workflow:
    workflow = await WorkflowRepository(session).get(id)
    if workflow is None:
        raise HTTPException(404)
    return workflow


async def _require_run(id: int, session: AsyncSession) -> WorkflowRun:
    run = await WorkflowRunRepository(session).get(id)
    if run is None:
        raise HTTPException(404)
    return run


async def _validate_project(project_id: int | None, session: AsyncSession) -> None:
    if project_id is not None and await session.get(Project, project_id) is None:
        raise HTTPException(422)


def execution_settings(request: Request) -> Settings:
    config: Settings = request.app.state.settings
    return config


type ExecutionSettings = Annotated[Settings, Depends(execution_settings)]


@router.get("/workflows", responses=_VALIDATION)
async def list_workflows(
    session: Session, project_id: int | None = None, status: WorkflowStatus | None = None
) -> list[WorkflowResponse]:
    rows = await WorkflowRepository(session).list_filtered(project_id, status)
    return [WorkflowResponse.model_validate(row) for row in rows]


@router.post("/workflows", status_code=201, responses=_VALIDATION)
async def create_workflow(body: WorkflowCreate, session: Session) -> WorkflowResponse:
    await _validate_project(body.project_id, session)
    row = await WorkflowRepository(session).create(body.model_dump())
    return WorkflowResponse.model_validate(row)


@router.get("/workflows/{id}", responses={**_NOT_FOUND, **_VALIDATION})
async def get_workflow(id: ResourceId, session: Session) -> WorkflowResponse:
    return WorkflowResponse.model_validate(await _require_workflow(id, session))


@router.put("/workflows/{id}", responses={**_NOT_FOUND, **_VALIDATION})
async def update_workflow(
    id: ResourceId, body: WorkflowUpdate, session: Session
) -> WorkflowResponse:
    await _require_workflow(id, session)
    await _validate_project(body.project_id, session)
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if "definition_json" in body.model_fields_set:
        fields["definition_json"] = body.definition_json
    row = await WorkflowRepository(session).update(id, fields)
    if row is None:
        raise HTTPException(404)
    return WorkflowResponse.model_validate(row)


@router.delete("/workflows/{id}", status_code=204, responses={**_NOT_FOUND, **_VALIDATION})
async def delete_workflow(id: ResourceId, session: Session) -> Response:
    if not await WorkflowRepository(session).delete(id):
        raise HTTPException(404)
    return Response(status_code=204)


@router.post("/workflows/{id}/runs", status_code=201, responses={**_NOT_FOUND, **_VALIDATION})
async def create_run(
    id: ResourceId,
    session: Session,
    config: ExecutionSettings,
    body: Annotated[WorkflowRunAction | None, Body()] = None,
) -> WorkflowRunResponse:
    service = WorkflowOrchestrator(session, settings=config)
    try:
        run = await service.create_run(id)
    except LookupError as exc:
        raise HTTPException(404) from exc
    except PausedWorkflowError as exc:
        raise InvalidStatusTransitionError(id, "paused", "pending") from exc
    return await run_response(run, session, config)


@router.get("/workflows/{id}/runs", responses={**_NOT_FOUND, **_VALIDATION})
async def list_runs(
    id: ResourceId, session: Session, config: ExecutionSettings
) -> list[WorkflowRunResponse]:
    await _require_workflow(id, session)
    runs = await WorkflowRunRepository(session).list_by_workflow(id)
    return [await run_response(run, session, config) for run in runs]


@router.get("/workflow-runs/{run_id}", responses={**_NOT_FOUND, **_VALIDATION})
async def get_run(
    run_id: ResourceId, session: Session, config: ExecutionSettings
) -> WorkflowRunResponse:
    return await run_response(await _require_run(run_id, session), session, config)


@router.get("/workflow-runs/{run_id}/audit", responses={**_NOT_FOUND, **_VALIDATION})
async def list_run_audit(
    run_id: ResourceId,
    session: Session,
    config: ExecutionSettings,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditLogEntry]:
    await _require_run(run_id, session)
    rows = await AuditLogRepository(session).list_for_run(run_id, limit=limit, offset=offset)
    responses = []
    for row in rows:
        response = AuditLogEntry.model_validate(row)
        if response.payload_json is not None:
            payload = json.loads(response.payload_json)
            response.payload_json = json.dumps(audit_payload(payload, config), allow_nan=False)
        responses.append(response)
    return responses


@router.post(
    "/workflow-runs/{run_id}/start",
    responses={**_NOT_FOUND, **_VALIDATION, 409: {"model": ErrorResponse}},
)
async def start_run(
    run_id: ResourceId,
    session: Session,
    config: ExecutionSettings,
    body: Annotated[WorkflowRunAction | None, Body()] = None,
) -> WorkflowRunResponse:
    try:
        run = await WorkflowOrchestrator(session, settings=config).start_or_resume(run_id)
    except LookupError as exc:
        raise HTTPException(404) from exc
    session.expire_all()
    return await run_response(run, session, config)


@router.post(
    "/workflow-runs/{run_id}/cancel",
    responses={**_NOT_FOUND, **_VALIDATION, 409: {"model": ErrorResponse}},
)
async def cancel_run(
    run_id: ResourceId,
    session: Session,
    config: ExecutionSettings,
    body: Annotated[WorkflowRunAction | None, Body()] = None,
) -> WorkflowRunResponse:
    try:
        run = await WorkflowOrchestrator(session, settings=config).cancel(run_id)
    except LookupError as exc:
        raise HTTPException(404) from exc
    session.expire_all()
    return await run_response(run, session, config)


@router.get("/workflow-runs/{run_id}/steps", responses={**_NOT_FOUND, **_VALIDATION})
async def list_steps(
    run_id: ResourceId, session: Session, config: ExecutionSettings
) -> list[StepRunResponse]:
    await _require_run(run_id, session)
    return [
        step_response(row, config)
        for row in await WorkflowStepRunRepository(session).list_for_run(run_id)
    ]


@router.get("/workflow-runs/{run_id}/steps/{step_id}", responses={**_NOT_FOUND, **_VALIDATION})
async def get_step(
    run_id: ResourceId, step_id: ResourceId, session: Session, config: ExecutionSettings
) -> StepRunResponse:
    await _require_run(run_id, session)
    row = await WorkflowStepRunRepository(session).get(step_id)
    if row is None or row.workflow_run_id != run_id:
        raise HTTPException(404)
    return step_response(row, config)
