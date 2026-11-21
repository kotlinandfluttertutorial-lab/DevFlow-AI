"""Transactional run status derivation using the Phase 1 transition contract."""

from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.models.workflow_step_run import WorkflowStepRun
from devflow_api.repositories._workflow_state import (
    TERMINAL_RUNS,
    TERMINAL_STEPS,
    is_real_failure,
    run_steps,
)
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRunRepository
from devflow_core.exceptions import InvalidStatusTransitionError


def _target_status(steps: list[WorkflowStepRun]) -> str:
    statuses = {step.status for step in steps}
    if any(is_real_failure(step) for step in steps):
        return "failed" if statuses <= TERMINAL_STEPS else "running"
    if "WAITING_APPROVAL" in statuses:
        return "waiting_approval"
    if statuses & {"READY", "RUNNING", "INTERRUPTED"}:
        return "running"
    if statuses <= {"SUCCEEDED"}:
        return "completed"
    if statuses <= {"PENDING"}:
        return "pending"
    if "PENDING" in statuses and statuses <= {"PENDING", "SUCCEEDED"}:
        return "running"
    # Cancellation is persisted by the cancellation service before deriving.
    raise ValueError("Step states require cancellation or failure-stop settlement")


async def derive_and_persist_run_status(run_id: int, session: AsyncSession) -> str:
    async with immediate_transaction(
        session, commit=not session.info.get("workflow_immediate", False)
    ):
        repo = WorkflowRunRepository(session)
        current = await repo.get_status(run_id)
        if current in TERMINAL_RUNS:
            return current
        target = _target_status(await run_steps(session, run_id))
        if current == target:
            return target
        try:
            if current == "waiting_approval" and target == "failed":
                await repo.transition_status(run_id, "running", [current], commit=False)
                await AuditLogRepository(session).log_event(
                    "run_status_changed",
                    "system",
                    run_id,
                    {"from_status": current, "to_status": "running"},
                    commit=False,
                )
                current = "running"
            await repo.transition_status(run_id, target, [current], commit=False)
        except InvalidStatusTransitionError:
            # Accept a concurrent winner's exact target once. Illegal transitions still raise.
            if await repo.get_status(run_id) != target:
                raise
            return target
        await AuditLogRepository(session).log_event(
            "run_status_changed",
            "system",
            run_id,
            {"from_status": current, "to_status": target},
            commit=False,
        )
        return target
