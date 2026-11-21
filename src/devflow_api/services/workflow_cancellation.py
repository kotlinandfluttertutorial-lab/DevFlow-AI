"""Shared cancellation settlement for user cancellation and approval rejection."""

from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.models.workflow import WorkflowRun
from devflow_api.repositories._workflow_state import TERMINAL_RUNS, TERMINAL_STEPS, run_steps
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRunRepository
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.executor import CancellationHandle

ACTIVE_EXECUTIONS: dict[tuple[int, int, int, int], CancellationHandle] = {}


async def signal_run_cancellation(session: AsyncSession, run_id: int) -> None:
    for (database, active_run, _, _), handle in list(ACTIVE_EXECUTIONS.items()):
        if database == id(session.bind) and active_run == run_id:
            await handle.cancel()


async def cancel_workflow_run(
    run_id: int, session: AsyncSession, actor_id: str, *, commit: bool = True
) -> WorkflowRun:
    async with immediate_transaction(session, commit=commit):
        run_repo = WorkflowRunRepository(session)
        run = await run_repo.get(run_id)
        if run is None:
            raise LookupError(f"Run {run_id} does not exist")
        if run.status in TERMINAL_RUNS:
            return run
        steps = WorkflowStepRunRepository(session)
        count = 0
        for step in await run_steps(session, run_id):
            if step.status in TERMINAL_STEPS:
                continue
            changed = await steps.transition_cas(
                step.id,
                "CANCELLED",
                from_status=step.status,
                lease_owner=step.lease_owner,
                attempt=step.attempt,
                commit=False,
            )
            if changed:
                count += 1
                await ApprovalRepository(session).expire_pending(
                    step_run_id=step.id, force=True, commit=False
                )
                await AuditLogRepository(session).log_event(
                    "step_cancelled",
                    "system",
                    run_id,
                    {"step_id": step.id, "attempt": step.attempt},
                    commit=False,
                )
        # The Phase 1 repository permits cancellation from running and waiting states.
        if run.status == "pending":
            await run_repo.transition_status(run_id, "running", ["pending"], commit=False)
            await AuditLogRepository(session).log_event(
                "run_status_changed",
                "system",
                run_id,
                {"from": "pending", "to": "running", "actor_id": actor_id},
                commit=False,
            )
        run = await run_repo.transition_status(
            run_id, "cancelled", ["running", "waiting_approval"], commit=False
        )
        await AuditLogRepository(session).log_event(
            "run_cancelled",
            "user",
            run_id,
            {"actor_id": actor_id, "cancelled_step_count": count},
            commit=False,
        )
        return run
