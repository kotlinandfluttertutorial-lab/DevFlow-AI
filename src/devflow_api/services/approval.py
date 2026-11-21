"""Workflow step approval gates."""

import json
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select

from devflow_api.models.approval import ApprovalRequest
from devflow_api.models.workflow import WorkflowRun
from devflow_api.repositories._workflow_state import TERMINAL_RUNS, is_real_failure, run_steps
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRunRepository
from devflow_api.repositories.workflow_status import derive_and_persist_run_status
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.workflow_template import WorkflowTemplate, canonical_hash
from devflow_api.services._transaction import shared_session, transaction
from devflow_api.services.workflow_cancellation import cancel_workflow_run, signal_run_cancellation
from devflow_core.exceptions import (
    ApprovalAlreadyResolvedError,
    ApprovalExpiredError,
    OperationNotPermitted,
)


class ApprovalService:
    def __init__(
        self,
        approval_repo: ApprovalRepository,
        run_repo: WorkflowRunRepository,
        audit_repo: AuditLogRepository,
    ) -> None:
        self._approval_repo = approval_repo
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._session = shared_session(approval_repo.session, run_repo.session, audit_repo.session)
        self._settings = approval_repo.settings

    async def request_approval(
        self,
        run_id: int,
        step_name: str,
        action_description: str,
        payload: dict[str, object] | None = None,
    ) -> ApprovalRequest:
        async with transaction(self._session):
            await self._run_repo.transition_status(
                run_id, "waiting_approval", ["running"], commit=False
            )
            approval = await self._approval_repo.create(
                {
                    "workflow_run_id": run_id,
                    "step_name": step_name,
                    "action_description": action_description,
                    "payload_json": json.dumps(payload, allow_nan=False)
                    if payload is not None
                    else None,
                },
                commit=False,
            )
            await self._audit_repo.log_event(
                "approval_requested",
                "system",
                run_id,
                {"approval_id": approval.id, "step_name": step_name},
                commit=False,
            )
        return approval

    async def resolve(
        self,
        approval_id: int,
        decision: Literal["approved", "rejected"],
        resolved_by: str | None,
    ) -> tuple[ApprovalRequest, WorkflowRun]:
        if decision not in ("approved", "rejected"):
            raise ValueError("Approval decision must be approved or rejected")
        existing = await self._approval_repo.get(approval_id)
        if existing is not None and existing.operation is not None:
            run_id = existing.workflow_run_id
            try:
                result = await self._resolve_bound(approval_id, decision, resolved_by)
            except ApprovalExpiredError:
                await self.reconcile_expired(run_id)
                raise
            if result[1].status == "cancelled":
                await signal_run_cancellation(self._session, result[1].id)
            return result
        async with transaction(self._session):
            approval = await self._approval_repo.resolve(
                approval_id, decision, resolved_by, commit=False
            )
            new_status = "running" if decision == "approved" else "cancelled"
            run = await self._run_repo.transition_status(
                approval.workflow_run_id, new_status, ["waiting_approval"], commit=False
            )
            await self._audit_repo.log_event(
                "approval_resolved",
                "user",
                run.id,
                {"approval_id": approval.id, "decision": decision, "resolved_by": resolved_by},
                commit=False,
            )
        return approval, run

    async def _binding_data(self, run_id: int, step_run_id: int) -> dict[str, object]:
        run = await self._run_repo.get(run_id)
        step = await WorkflowStepRunRepository(self._session).get(step_run_id)
        if run is None or step is None or step.workflow_run_id != run_id:
            raise LookupError("Approval run or step does not exist")
        if run.template_snapshot_json is None:
            raise OperationNotPermitted("Bound approvals require an execution snapshot")
        template = WorkflowTemplate.model_validate_json(run.template_snapshot_json)
        definition = next(item for item in template.steps if item.key == step.step_key)
        if not definition.approvable or definition.protected_operation is None:
            raise OperationNotPermitted("Step does not require approval")
        digest = canonical_hash(json.loads(step.inputs_json or "{}"))
        if step.input_digest != digest:
            raise OperationNotPermitted("Persisted input digest does not match the action")
        return {
            "workflow_run_id": run_id,
            "workflow_step_run_id": step_run_id,
            "operation": definition.protected_operation,
            "parameters_hash": digest,
            "actor_id": self._settings.LOCAL_OPERATOR_ID,
            "step_name": step.step_key,
            "action_description": definition.title,
        }

    async def request_bound_approval(
        self, run_id: int, step_run_id: int, *, commit: bool = True
    ) -> ApprovalRequest:
        async with immediate_transaction(self._session, commit=commit):
            rows = await run_steps(self._session, run_id)
            run = await self._run_repo.get(run_id)
            if run is None:
                raise LookupError(f"Run {run_id} does not exist")
            if run.status in TERMINAL_RUNS or any(is_real_failure(row) for row in rows):
                raise OperationNotPermitted("Run state forbids an approval gate")
            data = await self._binding_data(run_id, step_run_id)
            steps = WorkflowStepRunRepository(self._session)
            step = await steps.get(step_run_id)
            if step is None or step.status not in {"READY", "WAITING_APPROVAL"}:
                raise OperationNotPermitted("Step is not eligible for an approval gate")
            existing = await self._session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workflow_step_run_id == step_run_id,
                    ApprovalRequest.operation == data["operation"],
                    ApprovalRequest.parameters_hash == data["parameters_hash"],
                    ApprovalRequest.actor_id == data["actor_id"],
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.expires_at > datetime.now(UTC),
                )
            )
            if step.status == "READY":
                changed = await steps.transition_cas(
                    step_run_id, "WAITING_APPROVAL", from_status="READY", commit=False
                )
                if not changed:
                    raise OperationNotPermitted("Step changed before approval gating")
            if existing is not None:
                await derive_and_persist_run_status(run_id, self._session)
                return existing
            await self._approval_repo.expire_pending(
                step_run_id=step_run_id, force=True, commit=False
            )
            approval = await self._approval_repo.create_bound(data, commit=False)
            await self._audit_repo.log_event(
                "approval_requested",
                "system",
                run_id,
                {
                    "approval_id": approval.id,
                    "step_key": step.step_key,
                    "operation": approval.operation,
                    "parameters_hash": approval.parameters_hash,
                    "actor_id": approval.actor_id,
                },
                commit=False,
            )
            await derive_and_persist_run_status(run_id, self._session)
            return approval

    async def _resolve_bound(
        self, approval_id: int, decision: Literal["approved", "rejected"], resolved_by: str | None
    ) -> tuple[ApprovalRequest, WorkflowRun]:
        async with immediate_transaction(self._session):
            approval = await self._session.get(ApprovalRequest, approval_id, populate_existing=True)
            if approval is None:
                raise LookupError(f"Approval {approval_id} does not exist")
            if approval.status == "expired" or (
                approval.status == "pending"
                and approval.expires_at is not None
                and approval.expires_at <= datetime.now(UTC)
            ):
                raise ApprovalExpiredError("Approval has expired")
            if approval.status != "pending":
                raise ApprovalAlreadyResolvedError(approval_id, approval.status)
            run_id = approval.workflow_run_id
            rows = await run_steps(self._session, run_id)
            run = await self._run_repo.get(run_id)
            if run is None or run.status in TERMINAL_RUNS:
                raise OperationNotPermitted("Run is terminal")
            if any(is_real_failure(row) for row in rows):
                await WorkflowStepRunRepository(self._session).settle_to_failed(
                    run_id, commit=False
                )
                await derive_and_persist_run_status(run_id, self._session)
                stopped = True
            else:
                stopped = False
                step = next((row for row in rows if row.id == approval.workflow_step_run_id), None)
                if step is None or step.status != "WAITING_APPROVAL":
                    raise OperationNotPermitted("Approval is no longer bound to a waiting step")
                approval = await self._approval_repo.resolve(
                    approval_id, decision, resolved_by, commit=False
                )
                if decision == "approved":
                    await self._approval_repo.set_authorization_expires_at(
                        approval_id, commit=False
                    )
                    changed = await WorkflowStepRunRepository(self._session).transition_cas(
                        step.id, "READY", from_status="WAITING_APPROVAL", commit=False
                    )
                    if not changed:
                        raise OperationNotPermitted("Step changed before approval resolution")
                    await derive_and_persist_run_status(run_id, self._session)
                else:
                    await cancel_workflow_run(
                        run_id, self._session, self._settings.LOCAL_OPERATOR_ID, commit=False
                    )
                await self._audit_repo.log_event(
                    "approval_resolved",
                    "user",
                    run_id,
                    {
                        "approval_id": approval_id,
                        "decision": decision,
                        "actor_id": self._settings.LOCAL_OPERATOR_ID,
                    },
                    commit=False,
                )
            run = await self._run_repo.get(run_id)
            if run is None:
                raise LookupError(f"Run {run_id} does not exist")
        if stopped:
            raise OperationNotPermitted("Failure-stop settled the approval gate")
        return approval, run

    async def reconcile_expired(self, run_id: int) -> int:
        async with immediate_transaction(self._session):
            run = await self._run_repo.get(run_id)
            rows = await run_steps(self._session, run_id)
            if run is None:
                raise LookupError(f"Run {run_id} does not exist")
            if run.status in TERMINAL_RUNS:
                return 0
            if any(is_real_failure(row) for row in rows):
                await WorkflowStepRunRepository(self._session).settle_to_failed(
                    run_id, commit=False
                )
                await derive_and_persist_run_status(run_id, self._session)
                return 0
            replacements = []
            for step in rows:
                if step.status != "WAITING_APPROVAL":
                    continue
                valid = await self._session.scalar(
                    select(ApprovalRequest.id).where(
                        ApprovalRequest.workflow_step_run_id == step.id,
                        ApprovalRequest.operation.is_not(None),
                        ApprovalRequest.status == "pending",
                        ApprovalRequest.expires_at > datetime.now(UTC),
                    )
                )
                if valid is None:
                    replacements.append((step.id, await self._binding_data(run_id, step.id)))
        count = 0
        for step_id, data in replacements:
            try:
                await self._approval_repo.expire_and_re_gate(step_id, data)
            except OperationNotPermitted:
                continue
            count += 1
        return count
