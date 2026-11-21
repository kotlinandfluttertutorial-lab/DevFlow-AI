"""Persisted failure classification shared by dispatch, derivation, and approval guards."""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.models.workflow_step_run import WorkflowStepRun

TERMINAL_STEPS = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
TERMINAL_RUNS = frozenset({"completed", "failed", "cancelled"})


def is_real_failure(step: WorkflowStepRun) -> bool:
    if step.status != "FAILED":
        return False
    try:
        error = json.loads(step.error_json or "{}")
    except (ValueError, TypeError):
        return True
    # Missing or malformed error data is conservatively a real failure.
    return not (isinstance(error, dict) and error.get("category") == "skipped")


async def run_steps(session: AsyncSession, run_id: int) -> list[WorkflowStepRun]:
    return list(
        (
            await session.scalars(
                select(WorkflowStepRun)
                .where(WorkflowStepRun.workflow_run_id == run_id)
                .order_by(WorkflowStepRun.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
