"""Workflow approvals and append-only audit persistence."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import SecretStr
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.models.approval import ApprovalRequest
from devflow_api.models.audit import WorkflowAuditLog
from devflow_api.models.workflow import WorkflowRun
from devflow_api.models.workflow_step_run import WorkflowStepRun
from devflow_api.repositories._workflow_state import TERMINAL_RUNS, is_real_failure, run_steps
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.base import BaseRepository
from devflow_api.schemas.approval import BoundApprovalData
from devflow_api.schemas.workflow_template import ApprovalOperation, canonical_json
from devflow_core.config import Settings, get_settings
from devflow_core.exceptions import (
    ApprovalAlreadyResolvedError,
    DatabaseBusyError,
    OperationNotPermitted,
)
from devflow_core.logging import RedactSecretsProcessor


class ApprovalRepository(BaseRepository[ApprovalRequest]):
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        super().__init__(ApprovalRequest, session)
        self.settings = settings or get_settings()

    async def update(self, id: int, obj_in: dict[str, object]) -> ApprovalRequest | None:
        if "consumed_at" in obj_in:
            raise ValueError("Approval consumption is managed by consume_atomic")
        return await super().update(id, obj_in)

    async def create_bound(
        self, data: dict[str, object], *, commit: bool = True
    ) -> ApprovalRequest:
        binding = BoundApprovalData.model_validate(data)
        if binding.actor_id != self.settings.LOCAL_OPERATOR_ID:
            raise ValueError("Approval actor must be the configured local operator")
        async with immediate_transaction(self.session, commit=commit):
            step = await self.session.get(WorkflowStepRun, binding.workflow_step_run_id)
            if step is None or step.workflow_run_id != binding.workflow_run_id:
                raise ValueError("Approval step must belong to the bound run")
            now = datetime.now(UTC)
            fields = binding.model_dump(exclude={"payload"})
            fields.update(
                payload_json=canonical_json(binding.payload)
                if binding.payload is not None
                else None,
                status="pending",
                requested_at=now,
                expires_at=now + timedelta(seconds=self.settings.APPROVAL_TTL_SECONDS),
            )
            return await self.create(fields, commit=False)

    async def consume_atomic(
        self,
        approval_id: int,
        *,
        operation: ApprovalOperation,
        parameters_hash: str,
        workflow_step_run_id: int,
        actor_id: str,
        commit: bool = True,
    ) -> bool:
        async with immediate_transaction(self.session, commit=commit):
            now = datetime.now(UTC)
            return (
                await self.session.scalar(
                    update(ApprovalRequest)
                    .where(
                        ApprovalRequest.id == approval_id,
                        ApprovalRequest.status == "approved",
                        ApprovalRequest.consumed_at.is_(None),
                        or_(
                            ApprovalRequest.authorization_expires_at.is_(None),
                            ApprovalRequest.authorization_expires_at > now,
                        ),
                        ApprovalRequest.operation == operation,
                        ApprovalRequest.parameters_hash == parameters_hash,
                        ApprovalRequest.workflow_step_run_id == workflow_step_run_id,
                        ApprovalRequest.actor_id == actor_id,
                    )
                    .values(consumed_at=now, updated_at=now)
                    .returning(ApprovalRequest.id)
                )
                is not None
            )

    async def set_authorization_expires_at(self, approval_id: int, *, commit: bool = True) -> bool:
        async with immediate_transaction(self.session, commit=commit):
            approval = await self.session.get(ApprovalRequest, approval_id, populate_existing=True)
            if approval is None or approval.resolved_at is None:
                return False
            # Resolution and this helper are composed in the same outer transaction by Group B.
            return (
                await self.session.scalar(
                    update(ApprovalRequest)
                    .where(
                        ApprovalRequest.id == approval_id,
                        ApprovalRequest.status == "approved",
                        ApprovalRequest.operation.is_not(None),
                        ApprovalRequest.resolved_at.is_not(None),
                        ApprovalRequest.authorization_expires_at.is_(None),
                        ApprovalRequest.consumed_at.is_(None),
                    )
                    .values(
                        authorization_expires_at=approval.resolved_at
                        + timedelta(seconds=self.settings.APPROVAL_AUTHORIZATION_VALIDITY_SECONDS)
                    )
                    .returning(ApprovalRequest.id)
                )
                is not None
            )

    async def expire_pending(
        self, *, step_run_id: int | None = None, force: bool = False, commit: bool = True
    ) -> int:
        async with immediate_transaction(self.session, commit=commit):
            statement = update(ApprovalRequest).where(ApprovalRequest.status == "pending")
            if step_run_id is not None:
                statement = statement.where(ApprovalRequest.workflow_step_run_id == step_run_id)
            if force:
                step = await self.session.get(WorkflowStepRun, step_run_id)
                if step is None or step.status not in {"FAILED", "CANCELLED", "WAITING_APPROVAL"}:
                    raise OperationNotPermitted("Forced expiry requires a settled step")
            else:
                statement = statement.where(ApprovalRequest.expires_at <= datetime.now(UTC))
            rows = list(
                (
                    await self.session.execute(
                        statement.values(status="expired", updated_at=datetime.now(UTC)).returning(
                            ApprovalRequest.id, ApprovalRequest.workflow_run_id
                        )
                    )
                ).all()
            )
            for approval_id, run_id in rows:
                await AuditLogRepository(self.session).log_event(
                    "approval_expired", "system", run_id, {"approval_id": approval_id}, commit=False
                )
            return len(rows)

    async def _guard_re_gate(self, step_run_id: int) -> WorkflowStepRun:
        step = await self.session.get(WorkflowStepRun, step_run_id, populate_existing=True)
        if step is None:
            raise LookupError(f"Step {step_run_id} does not exist")
        run = await self.session.get(WorkflowRun, step.workflow_run_id, populate_existing=True)
        if (
            run is None
            or run.status in TERMINAL_RUNS
            or step.status != "WAITING_APPROVAL"
            or any(
                is_real_failure(row) for row in await run_steps(self.session, step.workflow_run_id)
            )
        ):
            raise OperationNotPermitted("Run or step state forbids re-gating")
        return step

    async def _valid_pending(self, step_run_id: int) -> ApprovalRequest | None:
        return await self.session.scalar(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.workflow_step_run_id == step_run_id,
                ApprovalRequest.status == "pending",
                ApprovalRequest.operation.is_not(None),
                ApprovalRequest.expires_at > datetime.now(UTC),
            )
            .execution_options(populate_existing=True)
        )

    async def _re_gate_once(
        self, step_run_id: int, new_approval_data: dict[str, object]
    ) -> ApprovalRequest:
        async with immediate_transaction(self.session):
            step = await self._guard_re_gate(step_run_id)
            binding = BoundApprovalData.model_validate(new_approval_data)
            if (binding.workflow_run_id, binding.workflow_step_run_id) != (
                step.workflow_run_id,
                step_run_id,
            ):
                raise ValueError("Replacement binding must match the gated step")
            existing = await self._valid_pending(step_run_id)
            if existing is not None:
                await self._audit_re_gate_noop(step, existing)
                return existing
            await self.expire_pending(step_run_id=step_run_id, commit=False)
            approval = await self.create_bound(new_approval_data, commit=False)
            await AuditLogRepository(self.session).log_event(
                "approval_re_gated",
                "system",
                step.workflow_run_id,
                {"approval_id": approval.id, "step_id": step_run_id},
                commit=False,
            )
            return approval

    async def _audit_re_gate_noop(self, step: WorkflowStepRun, approval: ApprovalRequest) -> None:
        await AuditLogRepository(self.session).log_event(
            "approval_re_gate_noop",
            "system",
            step.workflow_run_id,
            {"step_id": step.id, "approval_id": approval.id},
            commit=False,
        )

    async def expire_and_re_gate(
        self, step_run_id: int, new_approval_data: dict[str, object]
    ) -> ApprovalRequest:
        for attempt in range(3):
            try:
                try:
                    return await self._re_gate_once(step_run_id, new_approval_data)
                except IntegrityError as exc:
                    await self.session.rollback()
                    if not is_pending_approval_conflict(exc):
                        raise
                    async with immediate_transaction(self.session):
                        step = await self._guard_re_gate(step_run_id)
                        existing = await self._valid_pending(step_run_id)
                        if existing is None:
                            raise DatabaseBusyError(
                                "Concurrent re-gate conflict; retry required"
                            ) from exc
                        await self._audit_re_gate_noop(step, existing)
                        return existing
            except OperationalError as exc:
                await self.session.rollback()
                if not is_sqlite_busy(exc):
                    raise
                if attempt == 2:
                    raise DatabaseBusyError("SQLite re-gate retries exhausted") from exc
                await asyncio.sleep(0.01 * 2**attempt)
        raise DatabaseBusyError("SQLite re-gate retries exhausted")

    async def list_pending(self) -> list[ApprovalRequest]:
        return await self.list_by_status("pending")

    async def list_by_status(self, status: str | None) -> list[ApprovalRequest]:
        query = select(ApprovalRequest).order_by(ApprovalRequest.created_at, ApprovalRequest.id)
        if status is not None:
            query = query.where(ApprovalRequest.status == status)
        return list((await self.session.scalars(query)).all())

    async def list_for_run(self, run_id: int) -> list[ApprovalRequest]:
        query = (
            select(ApprovalRequest)
            .where(ApprovalRequest.workflow_run_id == run_id)
            .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
        )
        return list((await self.session.scalars(query)).all())

    async def resolve(
        self,
        approval_id: int,
        decision: Literal["approved", "rejected"],
        resolved_by: str | None,
        *,
        commit: bool = True,
    ) -> ApprovalRequest:
        if decision not in ("approved", "rejected"):
            raise ValueError("Approval decision must be approved or rejected")
        now = datetime.now(UTC)
        statement = (
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.status == "pending")
            .values(status=decision, resolved_at=now, resolved_by=resolved_by, updated_at=now)
            .returning(ApprovalRequest)
            .execution_options(populate_existing=True)
        )
        try:
            approval = (await self.session.scalars(statement)).one_or_none()
            if approval is None:
                status = await self.session.scalar(
                    select(ApprovalRequest.status).where(ApprovalRequest.id == approval_id)
                )
                if status is None:
                    raise LookupError(f"Approval {approval_id} does not exist")
                raise ApprovalAlreadyResolvedError(approval_id, status)
            if commit:
                await self.session.commit()
        except Exception:
            if commit:
                await self.session.rollback()
            raise
        return approval


def is_pending_approval_conflict(exc: IntegrityError) -> bool:
    """SQLite identifies this partial-index violation by its single indexed column."""
    return (
        isinstance(exc.orig, sqlite3.IntegrityError)
        and getattr(exc.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        and str(exc.orig) == "UNIQUE constraint failed: approval_requests.workflow_step_run_id"
    )


def is_sqlite_busy(exc: OperationalError) -> bool:
    code = getattr(exc.orig, "sqlite_errorcode", 0)
    return isinstance(code, int) and code & 0xFF == sqlite3.SQLITE_BUSY


class AuditLogRepository(BaseRepository[WorkflowAuditLog]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(WorkflowAuditLog, session)

    async def update(self, id: int, obj_in: dict[str, object]) -> WorkflowAuditLog | None:
        raise TypeError("Audit records are append-only")

    async def delete(self, id: int) -> bool:
        raise TypeError("Audit records are append-only")

    async def log_event(
        self,
        event_type: str,
        actor: str,
        workflow_run_id: int | None = None,
        payload: dict[str, object] | None = None,
        *,
        commit: bool = True,
    ) -> WorkflowAuditLog:
        if actor not in ("system", "user"):
            raise ValueError("Audit actor must be system or user")
        if payload is not None:
            settings = get_settings()
            secret_values = tuple(
                value.get_secret_value()
                for name in type(settings).model_fields
                if isinstance(value := getattr(settings, name), SecretStr)
            )
            redactor = RedactSecretsProcessor(secret_values)
            # Identifier field names are public metadata. Their values still undergo redaction.
            identifiers = {"step_key", "step_keys", "idempotency_key"}
            safe_payload: dict[str, object] = {}
            for key, value in payload.items():
                label = "value" if key in identifiers else key
                safe_payload[key] = redactor(None, "info", {label: value})[label]
            payload = safe_payload
        return await self.create(
            {
                "event_type": event_type,
                "actor": actor,
                "workflow_run_id": workflow_run_id,
                "payload_json": json.dumps(payload, allow_nan=False)
                if payload is not None
                else None,
            },
            commit=commit,
        )

    async def list_for_run(
        self, run_id: int, *, limit: int | None = None, offset: int = 0
    ) -> list[WorkflowAuditLog]:
        query = (
            select(WorkflowAuditLog)
            .where(WorkflowAuditLog.workflow_run_id == run_id)
            .order_by(WorkflowAuditLog.created_at, WorkflowAuditLog.id)
        )
        if limit is not None:
            query = query.limit(limit)
        query = query.offset(offset)
        return list((await self.session.scalars(query)).all())
