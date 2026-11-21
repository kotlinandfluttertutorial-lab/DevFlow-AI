"""Read-only, redacted execution projections for the local API."""

import json

from pydantic import BaseModel, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.models import WorkflowRun, WorkflowStepRun
from devflow_api.repositories.approval import ApprovalRepository
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.approval import ApprovalResponse
from devflow_api.schemas.workflow import StepRunResponse, WorkflowRunResponse
from devflow_core.config import Settings
from devflow_core.logging import RedactSecretsProcessor


def redact_public(value: object, settings: Settings) -> object:
    secrets = tuple(
        item.get_secret_value()
        for name in type(settings).model_fields
        if isinstance(item := getattr(settings, name), SecretStr)
    )
    return RedactSecretsProcessor(secrets)(None, "info", {"value": value})["value"]


def public_model[ModelT: BaseModel](model: ModelT, settings: Settings) -> ModelT:
    # Schema fields are public metadata. Nested user-provided mappings retain field redaction.
    return type(model).model_validate(
        {
            key: redact_public(value, settings)
            for key, value in model.model_dump(mode="python").items()
        }
    )


def audit_payload(value: object, settings: Settings) -> object:
    redacted = redact_public(value, settings)
    if isinstance(value, dict) and isinstance(redacted, dict):
        for identifier in ("step_key", "step_keys", "idempotency_key"):
            if identifier in value:
                redacted[identifier] = redact_public(value[identifier], settings)
    return redacted


def step_response(row: WorkflowStepRun, settings: Settings) -> StepRunResponse:
    response = StepRunResponse(
        id=row.id,
        step_key=row.step_key,
        step_version=row.step_version,
        executor_id=row.executor_id,
        status=row.status,
        attempt=row.attempt,
        result_status=row.result_status,
        tool_selection=json.loads(row.tool_selection_json) if row.tool_selection_json else None,
        ready_at=row.ready_at,
        dispatched_at=row.dispatched_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        output=json.loads(row.output_json)
        if row.status == "SUCCEEDED" and row.output_json
        else None,
        artifact_refs=json.loads(row.artifact_refs_json)
        if row.status == "SUCCEEDED" and row.artifact_refs_json
        else [],
        error=json.loads(row.error_json)
        if row.status in {"FAILED", "CANCELLED"} and row.error_json
        else None,
    )
    return public_model(response, settings)


async def run_response(
    run: WorkflowRun, session: AsyncSession, settings: Settings
) -> WorkflowRunResponse:
    response = WorkflowRunResponse.model_validate(run)
    response.error_message = (
        str(redact_public(response.error_message, settings))
        if response.error_message is not None
        else None
    )
    response.approvals = [
        public_model(ApprovalResponse.model_validate(row), settings)
        for row in await ApprovalRepository(session).list_for_run(run.id)
    ]
    response.pending_approvals = [row for row in response.approvals if row.status == "pending"]
    response.steps = [
        step_response(row, settings)
        for row in await WorkflowStepRunRepository(session).list_for_run(run.id)
    ]
    return response
