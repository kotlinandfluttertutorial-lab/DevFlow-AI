"""Restart reconciliation of durable leases and approval gates."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devflow_api.models import WorkflowRun, WorkflowStepRun
from devflow_api.repositories._workflow_state import TERMINAL_STEPS, is_real_failure, run_steps
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRunRepository
from devflow_api.repositories.workflow_status import derive_and_persist_run_status
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.executor import StructuredError
from devflow_api.schemas.workflow_template import WorkflowTemplate
from devflow_api.services.approval import ApprovalService
from devflow_core.config import Settings, get_settings


class RecoveryService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._sessions = async_sessionmaker(session.bind, expire_on_commit=False)
        self.settings = settings or get_settings()

    async def recover(self) -> int:
        async with self._sessions() as session:
            run_ids = list(
                (
                    await session.scalars(
                        select(WorkflowStepRun.workflow_run_id)
                        .where(WorkflowStepRun.status.not_in(TERMINAL_STEPS))
                        .distinct()
                        .order_by(WorkflowStepRun.workflow_run_id)
                    )
                ).all()
            )
        interrupted = 0
        for run_id in run_ids:
            interrupted += await self._recover_run(run_id)
            async with self._sessions() as session:
                try:
                    await ApprovalService(
                        ApprovalRepository(session, self.settings),
                        WorkflowRunRepository(session),
                        AuditLogRepository(session),
                    ).reconcile_expired(run_id)
                except LookupError:
                    continue
        return interrupted

    async def _recover_run(self, run_id: int) -> int:
        async with self._sessions() as session:
            async with immediate_transaction(session):
                run = await session.get(WorkflowRun, run_id, populate_existing=True)
                if run is None or run.status in {"completed", "failed"}:
                    return 0
                rows = await run_steps(session, run_id)
                steps = WorkflowStepRunRepository(session)
                audit = AuditLogRepository(session)
                if run.status == "cancelled":
                    for row in rows:
                        if row.status in TERMINAL_STEPS:
                            continue
                        if await steps.transition_cas(
                            row.id,
                            "CANCELLED",
                            from_status=row.status,
                            lease_owner=row.lease_owner,
                            attempt=row.attempt,
                            commit=False,
                        ):
                            await ApprovalRepository(session, self.settings).expire_pending(
                                step_run_id=row.id, force=True, commit=False
                            )
                            await audit.log_event(
                                "step_recovery_decided",
                                "system",
                                run_id,
                                {
                                    "step_id": row.id,
                                    "decision": "cancel",
                                    "reason": "run_cancelled",
                                    "attempt": row.attempt,
                                },
                                commit=False,
                            )
                    return 0
                definitions = (
                    {
                        step.key: step
                        for step in WorkflowTemplate.model_validate_json(
                            run.template_snapshot_json
                        ).steps
                    }
                    if run.template_snapshot_json
                    else {}
                )
                count = 0
                now = datetime.now(UTC)
                for row in rows:
                    if row.status != "RUNNING" or (
                        row.lease_expires_at is not None and row.lease_expires_at > now
                    ):
                        continue
                    prior_owner, prior_expiry = row.lease_owner, row.lease_expires_at
                    if await steps.transition_cas(
                        row.id,
                        "INTERRUPTED",
                        from_status="RUNNING",
                        lease_owner=prior_owner,
                        attempt=row.attempt,
                        commit=False,
                    ):
                        count += 1
                        await audit.log_event(
                            "step_interrupted",
                            "system",
                            run_id,
                            {
                                "step_id": row.id,
                                "step_key": row.step_key,
                                "prior_lease_owner": prior_owner,
                                "prior_lease_expires_at": prior_expiry.isoformat()
                                if prior_expiry
                                else None,
                                "attempt": row.attempt,
                            },
                            commit=False,
                        )
                failure_stop = any(is_real_failure(row) for row in rows)
                for row in rows:
                    if row.status != "INTERRUPTED":
                        continue
                    definition = definitions.get(row.step_key)
                    retry = (
                        not failure_stop
                        and definition is not None
                        and definition.retry_policy.safe
                        and definition.retry_policy.idempotent
                        and row.attempt < definition.retry_policy.max_attempts
                    )
                    reason = (
                        "safe_retry"
                        if retry
                        else ("run_failure_stop" if failure_stop else "unsafe_or_budget_exhausted")
                    )
                    values: dict[str, object] = {}
                    if not retry:
                        error = StructuredError(
                            code="STEP_BLOCKED_BY_RUN_FAILURE"
                            if failure_stop
                            else "STEP_RECOVERY_UNSAFE_OR_EXHAUSTED",
                            category="skipped" if failure_stop else "internal",
                            message="Interrupted execution requires terminal settlement",
                            attempt=row.attempt,
                        )
                        values.update(result_status="failed", error_json=error.model_dump_json())
                    await steps.transition_cas(
                        row.id,
                        "READY" if retry else "FAILED",
                        from_status="INTERRUPTED",
                        values=values,
                        commit=False,
                    )
                    await audit.log_event(
                        "step_recovery_decided",
                        "system",
                        run_id,
                        {
                            "step_id": row.id,
                            "step_key": row.step_key,
                            "decision": "retry" if retry else "fail",
                            "reason": reason,
                            "attempt": row.attempt,
                        },
                        commit=False,
                    )
                    if not retry:
                        await audit.log_event(
                            "step_failed",
                            "system",
                            run_id,
                            {"step_id": row.id, "reason": reason, "attempt": row.attempt},
                            commit=False,
                        )
                        failure_stop = True
                if failure_stop:
                    await steps.settle_to_failed(run_id, commit=False)
                if run.status in {"running", "waiting_approval"}:
                    await derive_and_persist_run_status(run_id, session)
                return count
