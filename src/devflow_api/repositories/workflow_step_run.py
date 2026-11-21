"""Atomic step lifecycle, dispatch authorization, and lease fencing."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.models.approval import ApprovalRequest
from devflow_api.models.workflow import WorkflowRun
from devflow_api.models.workflow_step_run import WorkflowStepRun
from devflow_api.repositories._workflow_state import TERMINAL_RUNS, is_real_failure, run_steps
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.schemas.workflow_template import (
    ApprovalOperation,
    WorkflowTemplate,
    canonical_hash,
    canonical_json,
)
from devflow_core.exceptions import (
    ApprovalNotConsumableError,
    InvalidStepTransitionError,
    OperationNotPermitted,
    StepDispatchConflict,
)

STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"READY", "FAILED", "CANCELLED"}),
    "READY": frozenset({"RUNNING", "WAITING_APPROVAL", "FAILED", "CANCELLED"}),
    "RUNNING": frozenset(
        {"SUCCEEDED", "READY", "WAITING_APPROVAL", "FAILED", "INTERRUPTED", "CANCELLED"}
    ),
    "WAITING_APPROVAL": frozenset({"READY", "CANCELLED", "FAILED"}),
    "INTERRUPTED": frozenset({"READY", "FAILED", "CANCELLED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}
RESULT_FIELDS = frozenset(
    {
        "inputs_json",
        "input_digest",
        "output_json",
        "artifact_refs_json",
        "error_json",
        "result_status",
    }
)


class WorkflowStepRunRepository:
    """Intentionally exposes no individual step deletion or unrestricted update."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditLogRepository(session)

    async def get(self, step_id: int) -> WorkflowStepRun | None:
        return await self.session.get(WorkflowStepRun, step_id, populate_existing=True)

    async def list_for_run(self, run_id: int) -> list[WorkflowStepRun]:
        return await run_steps(self.session, run_id)

    async def create_for_run(
        self, run_id: int, template: WorkflowTemplate, *, commit: bool = True
    ) -> list[WorkflowStepRun]:
        async with immediate_transaction(self.session, commit=commit):
            rows = [
                WorkflowStepRun(
                    workflow_run_id=run_id,
                    step_key=step.key,
                    step_version=step.version,
                    executor_id=step.executor_id,
                    tool_selection_json=canonical_json(step.tool_selection.model_dump(mode="json")),
                    depends_on_json=canonical_json(step.depends_on),
                    status="PENDING",
                    attempt=0,
                )
                for step in template.steps
            ]
            self.session.add_all(rows)
            await self.session.flush()
        return rows

    async def transition_cas(
        self,
        step_id: int,
        to_status: str,
        *,
        from_status: str,
        lease_owner: str | None = None,
        attempt: int | None = None,
        values: dict[str, object] | None = None,
        commit: bool = True,
    ) -> bool:
        fields = dict(values or {})
        if fields.keys() - RESULT_FIELDS:
            raise ValueError("Unsupported transition fields")
        if to_status != "SUCCEEDED" and fields.keys() & {"output_json", "artifact_refs_json"}:
            raise ValueError("Outputs may only be persisted on success")
        invalid = (
            to_status not in STEP_TRANSITIONS.get(from_status, frozenset())
            or to_status == "RUNNING"
            or (
                from_status == "RUNNING"
                and (
                    attempt is None
                    or (not lease_owner and to_status not in {"INTERRUPTED", "CANCELLED"})
                )
            )
        )
        async with immediate_transaction(self.session, commit=commit):
            row = await self.get(step_id)
            if row is None:
                raise LookupError(f"Step {step_id} does not exist")
            if invalid:
                await self.audit.log_event(
                    "step_transition_rejected",
                    "system",
                    row.workflow_run_id,
                    {"step_id": step_id, "from": from_status, "to": to_status},
                    commit=False,
                )
                changed = False
            else:
                now = datetime.now(UTC)
                fields.update(status=to_status, updated_at=now)
                if to_status == "READY":
                    fields["ready_at"] = now
                if to_status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    fields["completed_at"] = now
                    fields.setdefault("result_status", to_status.lower())
                if to_status == "SUCCEEDED":
                    fields["error_json"] = None
                if from_status == "RUNNING":
                    fields.update(lease_owner=None, lease_expires_at=None)
                statement = update(WorkflowStepRun).where(
                    WorkflowStepRun.id == step_id, WorkflowStepRun.status == from_status
                )
                if from_status == "RUNNING":
                    statement = statement.where(
                        WorkflowStepRun.lease_owner == lease_owner,
                        WorkflowStepRun.attempt == attempt,
                    )
                    if to_status == "INTERRUPTED":
                        statement = statement.where(
                            or_(
                                WorkflowStepRun.lease_expires_at.is_(None),
                                WorkflowStepRun.lease_expires_at <= now,
                            )
                        )
                changed = (
                    await self.session.scalar(
                        statement.values(**fields).returning(WorkflowStepRun.id)
                    )
                    is not None
                )
                await self.audit.log_event(
                    "step_transition" if changed else "step_transition_rejected",
                    "system",
                    row.workflow_run_id,
                    {"step_id": step_id, "from": from_status, "to": to_status},
                    commit=False,
                )
        if invalid:
            raise InvalidStepTransitionError(f"{from_status} → {to_status}")
        return changed

    async def _dispatch(
        self,
        step_id: int,
        lease_owner: str,
        lease_ttl_s: int,
        *,
        protected: bool,
        concurrency_limit: int | None = None,
        expected_input_digest: str | None = None,
    ) -> WorkflowStepRun:
        if not lease_owner or lease_ttl_s <= 0:
            raise ValueError("A lease requires an owner and positive lifetime")
        row = await self.get(step_id)
        if row is None:
            raise LookupError(f"Step {step_id} does not exist")
        run = await self.session.get(WorkflowRun, row.workflow_run_id, populate_existing=True)
        if run is None or run.status in TERMINAL_RUNS or run.status == "pending":
            raise OperationNotPermitted("Run must be started before dispatch")
        steps = await run_steps(self.session, run.id)
        if any(is_real_failure(step) for step in steps):
            raise OperationNotPermitted("Failure-stop prevents dispatch")
        if row.status != "READY":
            raise StepDispatchConflict(f"Step {step_id} is no longer READY")
        if (
            concurrency_limit is not None
            and sum(step.status == "RUNNING" for step in steps) >= concurrency_limit
        ):
            raise StepDispatchConflict("Run executor capacity is occupied")
        if expected_input_digest is not None and (
            row.input_digest != expected_input_digest
            or canonical_hash(json.loads(row.inputs_json or "{}")) != expected_input_digest
        ):
            raise OperationNotPermitted("Execution inputs changed before dispatch")
        if run.template_snapshot_json:
            template = WorkflowTemplate.model_validate_json(run.template_snapshot_json)
            definition = next(step for step in template.steps if step.key == row.step_key)
            if definition.approvable != protected:
                raise OperationNotPermitted("Dispatch must match the snapshot approval policy")
            if row.attempt >= definition.retry_policy.max_attempts:
                raise OperationNotPermitted("Step attempt budget is exhausted")
        elif protected:
            raise OperationNotPermitted("Protected dispatch requires an execution snapshot")
        now = datetime.now(UTC)
        next_attempt = row.attempt + 1
        result = await self.session.scalar(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.id == step_id, WorkflowStepRun.status == "READY")
            .values(
                status="RUNNING",
                lease_owner=lease_owner,
                lease_expires_at=now + timedelta(seconds=lease_ttl_s),
                attempt=next_attempt,
                idempotency_key=f"{row.workflow_run_id}:{row.step_key}:{next_attempt}",
                dispatched_at=now,
                started_at=now,
                updated_at=now,
                completed_at=None,
                result_status=None,
                error_json=None,
            )
            .returning(WorkflowStepRun)
            .execution_options(populate_existing=True)
        )
        if result is None:
            raise StepDispatchConflict(f"Step {step_id} is no longer READY")
        await self.audit.log_event(
            "step_dispatched",
            "system",
            row.workflow_run_id,
            {
                "step_id": step_id,
                "step_key": row.step_key,
                "attempt": next_attempt,
                "lease_owner": lease_owner,
                "idempotency_key": result.idempotency_key,
                "input_digest": result.input_digest,
            },
            commit=False,
        )
        return result

    async def dispatch_cas(
        self,
        step_id: int,
        lease_owner: str,
        lease_ttl_s: int = 60,
        *,
        commit: bool = True,
        concurrency_limit: int | None = None,
        expected_input_digest: str | None = None,
    ) -> WorkflowStepRun:
        async with immediate_transaction(self.session, commit=commit):
            return await self._dispatch(
                step_id,
                lease_owner,
                lease_ttl_s,
                protected=False,
                concurrency_limit=concurrency_limit,
                expected_input_digest=expected_input_digest,
            )

    async def dispatch_protected_txn(
        self,
        step_id: int,
        lease_owner: str,
        *,
        approval_id: int,
        operation: ApprovalOperation,
        parameters_hash: str,
        actor_id: str,
        lease_ttl_s: int = 60,
        concurrency_limit: int | None = None,
        expected_input_digest: str | None = None,
    ) -> WorkflowStepRun:
        try:
            async with immediate_transaction(self.session):
                row = await self._dispatch(
                    step_id,
                    lease_owner,
                    lease_ttl_s,
                    protected=True,
                    concurrency_limit=concurrency_limit,
                    expected_input_digest=expected_input_digest,
                )
                approvals = ApprovalRepository(self.session)
                if not await approvals.consume_atomic(
                    approval_id,
                    operation=operation,
                    parameters_hash=parameters_hash,
                    workflow_step_run_id=step_id,
                    actor_id=actor_id,
                    commit=False,
                ):
                    raise ApprovalNotConsumableError(
                        "Approval binding, state, or validity mismatch"
                    )
                return row
        except ApprovalNotConsumableError:
            # The lease and consume have both rolled back before writing rejection evidence.
            async with immediate_transaction(self.session):
                rejected_step = await self.get(step_id)
                approval = await self.session.get(
                    ApprovalRequest, approval_id, populate_existing=True
                )
                reason = "approval_missing"
                if approval is not None:
                    checks = (
                        ("status_not_approved", approval.status == "approved"),
                        ("already_consumed", approval.consumed_at is None),
                        (
                            "authorization_expired",
                            approval.authorization_expires_at is None
                            or approval.authorization_expires_at > datetime.now(UTC),
                        ),
                        ("operation_mismatch", approval.operation == operation),
                        ("digest_mismatch", approval.parameters_hash == parameters_hash),
                        ("step_binding_mismatch", approval.workflow_step_run_id == step_id),
                        ("actor_mismatch", approval.actor_id == actor_id),
                    )
                    reason = next(
                        (name for name, valid in checks if not valid), "concurrent_change"
                    )
                await self.audit.log_event(
                    "approval_not_consumable",
                    "system",
                    rejected_step.workflow_run_id if rejected_step else None,
                    {
                        "step_id": step_id,
                        "approval_id": approval_id,
                        "reason": reason,
                    },
                    commit=False,
                )
            raise

    async def persist_result_cas(
        self,
        step_id: int,
        to_status: str,
        *,
        lease_owner: str,
        attempt: int,
        values: dict[str, object] | None = None,
        commit: bool = True,
    ) -> bool:
        async with immediate_transaction(self.session, commit=commit):
            changed = await self.transition_cas(
                step_id,
                to_status,
                from_status="RUNNING",
                lease_owner=lease_owner,
                attempt=attempt,
                values=values,
                commit=False,
            )
            if not changed:
                row = await self.get(step_id)
                await self.audit.log_event(
                    "step_stale_completion_discarded",
                    "system",
                    row.workflow_run_id if row else None,
                    {
                        "step_id": step_id,
                        "prior_attempt": attempt,
                        "prior_lease_owner": lease_owner,
                    },
                    commit=False,
                )
            return changed

    async def renew_lease(
        self, step_id: int, lease_owner: str, attempt: int, lease_ttl_s: int = 60
    ) -> bool:
        if lease_ttl_s <= 0:
            raise ValueError("Lease lifetime must be positive")
        async with immediate_transaction(self.session):
            now = datetime.now(UTC)
            return (
                await self.session.scalar(
                    update(WorkflowStepRun)
                    .where(
                        WorkflowStepRun.id == step_id,
                        WorkflowStepRun.status == "RUNNING",
                        WorkflowStepRun.lease_owner == lease_owner,
                        WorkflowStepRun.attempt == attempt,
                        WorkflowStepRun.lease_expires_at > now,
                        WorkflowStepRun.workflow_run_id.in_(
                            select(WorkflowRun.id).where(
                                WorkflowRun.status.in_(["running", "waiting_approval"])
                            )
                        ),
                    )
                    .values(lease_expires_at=now + timedelta(seconds=lease_ttl_s), updated_at=now)
                    .returning(WorkflowStepRun.id)
                )
                is not None
            )

    async def release_lease(
        self, step_id: int, lease_owner: str, attempt: int, *, commit: bool = True
    ) -> bool:
        async with immediate_transaction(self.session, commit=commit):
            return (
                await self.session.scalar(
                    update(WorkflowStepRun)
                    .where(
                        WorkflowStepRun.id == step_id,
                        WorkflowStepRun.lease_owner == lease_owner,
                        WorkflowStepRun.attempt == attempt,
                    )
                    .values(lease_owner=None, lease_expires_at=None, updated_at=datetime.now(UTC))
                    .returning(WorkflowStepRun.id)
                )
                is not None
            )

    async def find_stale_running(self, now: datetime | None = None) -> list[WorkflowStepRun]:
        return list(
            (
                await self.session.scalars(
                    select(WorkflowStepRun)
                    .where(
                        WorkflowStepRun.status == "RUNNING",
                        or_(
                            WorkflowStepRun.lease_expires_at.is_(None),
                            WorkflowStepRun.lease_expires_at <= (now or datetime.now(UTC)),
                        ),
                    )
                    .order_by(WorkflowStepRun.id)
                )
            ).all()
        )

    async def list_interrupted(self) -> list[WorkflowStepRun]:
        return list(
            (
                await self.session.scalars(
                    select(WorkflowStepRun)
                    .where(WorkflowStepRun.status == "INTERRUPTED")
                    .order_by(WorkflowStepRun.id)
                )
            ).all()
        )

    async def settle_to_failed(self, run_id: int, *, commit: bool = True) -> int:
        async with immediate_transaction(self.session, commit=commit):
            rows = await run_steps(self.session, run_id)
            if not any(is_real_failure(step) for step in rows):
                return 0
            count = 0
            for row in rows:
                if row.status not in {"PENDING", "READY", "WAITING_APPROVAL", "INTERRUPTED"}:
                    continue
                waiting = row.status == "WAITING_APPROVAL"
                changed = await self.transition_cas(
                    row.id,
                    "FAILED",
                    from_status=row.status,
                    values={
                        "result_status": "failed",
                        "error_json": json.dumps(
                            {
                                "code": "STEP_BLOCKED_BY_RUN_FAILURE",
                                "category": "skipped",
                                "message": "Blocked by run failure",
                                "retryable": False,
                                "attempt": row.attempt,
                            }
                        ),
                    },
                    commit=False,
                )
                count += int(changed)
                if changed:
                    if waiting:
                        await ApprovalRepository(self.session).expire_pending(
                            step_run_id=row.id, force=True, commit=False
                        )
                    await self.audit.log_event(
                        "step_failed",
                        "system",
                        run_id,
                        {
                            "step_id": row.id,
                            "category": "skipped",
                            "code": "STEP_BLOCKED_BY_RUN_FAILURE",
                        },
                        commit=False,
                    )
            return count
