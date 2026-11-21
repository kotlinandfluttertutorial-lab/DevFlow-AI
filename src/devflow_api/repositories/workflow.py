"""Workflow and run queries and conditional state updates."""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from devflow_api.models.workflow import Workflow, WorkflowRun
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.base import BaseRepository
from devflow_api.schemas.workflow_template import WorkflowTemplate, canonical_hash, canonical_json
from devflow_core.exceptions import InvalidStatusTransitionError

VALID_FROM = {
    "running": ("pending", "waiting_approval"),
    "waiting_approval": ("running",),
    "completed": ("running",),
    "failed": ("running",),
    "cancelled": ("running", "waiting_approval"),
}


class WorkflowRepository(BaseRepository[Workflow]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Workflow, session)

    async def get_status(self, workflow_id: int) -> str | None:
        return await self.session.scalar(select(Workflow.status).where(Workflow.id == workflow_id))

    async def list_by_project(self, project_id: int) -> list[Workflow]:
        return await self.list_filtered(project_id, None)

    async def list_by_status(self, status: str) -> list[Workflow]:
        return await self.list_filtered(None, status)

    async def list_filtered(self, project_id: int | None, status: str | None) -> list[Workflow]:
        query = select(Workflow).order_by(Workflow.id)
        if project_id is not None:
            query = query.where(Workflow.project_id == project_id)
        if status is not None:
            query = query.where(Workflow.status == status)
        return list((await self.session.scalars(query)).all())


class WorkflowRunRepository(BaseRepository[WorkflowRun]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(WorkflowRun, session)

    async def create_with_snapshot(
        self, workflow_id: int, template: WorkflowTemplate, *, commit: bool = True
    ) -> WorkflowRun:
        from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository

        async with immediate_transaction(self.session, commit=commit):
            payload = template.model_dump(mode="json")
            run = await self.create(
                {
                    "workflow_id": workflow_id,
                    "template_snapshot_json": canonical_json(payload),
                    "template_version": template.template_version,
                    "template_hash": canonical_hash(payload),
                },
                commit=False,
            )
            await WorkflowStepRunRepository(self.session).create_for_run(
                run.id, template, commit=False
            )
            return run

    async def update(self, id: int, obj_in: dict[str, object]) -> WorkflowRun | None:
        if obj_in.keys() & {"template_snapshot_json", "template_version", "template_hash"}:
            raise ValueError("Execution snapshots are immutable")
        return await super().update(id, obj_in)

    async def list_by_workflow(self, workflow_id: int) -> list[WorkflowRun]:
        query = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
        return list((await self.session.scalars(query.order_by(WorkflowRun.id))).all())

    async def get_status(self, run_id: int) -> str:
        status = await self.session.scalar(
            select(WorkflowRun.status).where(WorkflowRun.id == run_id)
        )
        if status is None:
            raise LookupError(f"Run {run_id} does not exist")
        return status

    async def transition_status(
        self,
        run_id: int,
        new_status: str,
        valid_from: list[str],
        *,
        error_message: str | None = None,
        commit: bool = True,
    ) -> WorkflowRun:
        allowed = [status for status in VALID_FROM.get(new_status, ()) if status in valid_from]
        now = datetime.now(UTC)
        values: dict[str, object] = {"status": new_status, "updated_at": now}
        if new_status == "running":
            values["started_at"] = func.coalesce(WorkflowRun.started_at, now)
        if new_status in {"completed", "failed", "cancelled"}:
            values["completed_at"] = now
        if new_status == "failed":
            values["error_message"] = error_message or "Workflow run failed"

        statement = (
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.status.in_(allowed))
            .values(**values)
            .returning(WorkflowRun)
            .execution_options(populate_existing=True)
        )
        try:
            run = (await self.session.scalars(statement)).one_or_none()
            if run is None:
                raise InvalidStatusTransitionError(
                    run_id, await self.get_status(run_id), new_status
                )
            if commit:
                await self.session.commit()
        except Exception:
            if commit:
                await self.session.rollback()
            raise
        return run
