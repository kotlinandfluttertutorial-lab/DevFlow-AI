"""Deterministic mock workflow execution and approval-gated orchestration."""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import undefer

from devflow_api.executors.base import Executor
from devflow_api.executors.registry import ExecutorRegistry
from devflow_api.models import ApprovalRequest, Workflow, WorkflowRun, WorkflowStepRun
from devflow_api.repositories._workflow_state import TERMINAL_RUNS, is_real_failure, run_steps
from devflow_api.repositories._workflow_transaction import immediate_transaction
from devflow_api.repositories.approval import ApprovalRepository, AuditLogRepository
from devflow_api.repositories.workflow import WorkflowRepository, WorkflowRunRepository
from devflow_api.repositories.workflow_status import derive_and_persist_run_status
from devflow_api.repositories.workflow_step_run import WorkflowStepRunRepository
from devflow_api.schemas.executor import (
    CancellationHandle,
    ExecutionContext,
    ExecutionMetadata,
    StepExecutionResult,
    StructuredError,
)
from devflow_api.schemas.workflow_template import (
    StepDefinition,
    WorkflowTemplate,
    canonical_hash,
    canonical_json,
    parse_workflow_template,
)
from devflow_api.services.approval import ApprovalService
from devflow_api.services.recovery import RecoveryService as RecoveryService
from devflow_api.services.workflow import WorkflowService
from devflow_api.services.workflow_cancellation import (
    ACTIVE_EXECUTIONS,
    cancel_workflow_run,
    signal_run_cancellation,
)
from devflow_core.config import Settings, get_settings
from devflow_core.exceptions import (
    ApprovalNotConsumableError,
    ExecutorSelectionError,
    OperationNotPermitted,
    PausedWorkflowError,
    RunNotStartableError,
    StepDispatchConflict,
)


@dataclass(frozen=True)
class DispatchPlan:
    run_id: int
    step_id: int
    definition: StepDefinition
    inputs: dict[str, JsonValue]
    input_digest: str
    approval_id: int | None
    executor: Executor


def approval_service(session: AsyncSession, settings: Settings | None = None) -> ApprovalService:
    return ApprovalService(
        ApprovalRepository(session, settings),
        WorkflowRunRepository(session),
        AuditLogRepository(session),
    )


def resolve_inputs(
    value: JsonValue, definition: StepDefinition, steps: dict[str, WorkflowStepRun]
) -> JsonValue:
    if isinstance(value, dict):
        return {key: resolve_inputs(item, definition, steps) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_inputs(item, definition, steps) for item in value]
    if isinstance(value, str) and value.startswith("$steps"):
        for key in sorted(definition.depends_on, key=len, reverse=True):
            prefix = f"$steps.{key}."
            if value.startswith(prefix):
                source = steps[key]
                outputs = json.loads(source.output_json or "{}")
                name = value[len(prefix) :]
                if (
                    source.status != "SUCCEEDED"
                    or not isinstance(outputs, dict)
                    or name not in outputs
                ):
                    raise ValueError("Referenced step output is unavailable")
                resolved: JsonValue = outputs[name]
                return resolved
        raise ValueError("Malformed or undeclared step output reference")
    return value


class WorkflowOrchestrator:
    def __init__(
        self,
        session: AsyncSession,
        registry: ExecutorRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._sessions = async_sessionmaker(session.bind, expire_on_commit=False)
        self.registry = registry or ExecutorRegistry()
        self.settings = settings or get_settings()

    async def _get_run(self, run_id: int) -> WorkflowRun:
        async with self._sessions() as session:
            run = await WorkflowRunRepository(session).get(run_id)
            if run is None:
                raise LookupError(f"Run {run_id} does not exist")
            return run

    async def create_run(self, workflow_id: int, actor: str | None = None) -> WorkflowRun:
        async with self._sessions() as session:
            async with immediate_transaction(session):
                workflow = await session.scalar(
                    select(Workflow)
                    .where(Workflow.id == workflow_id)
                    .options(undefer(Workflow.definition_json))
                )
                if workflow is None:
                    raise LookupError(f"Workflow {workflow_id} does not exist")
                if workflow.status == "paused":
                    raise PausedWorkflowError("A paused workflow cannot start a run")
                repo = WorkflowRunRepository(session)
                template = (
                    parse_workflow_template(workflow.definition_json)
                    if workflow.definition_json
                    else None
                )
                if template is None:
                    run = await repo.create({"workflow_id": workflow_id}, commit=False)
                else:
                    run = await repo.create_with_snapshot(workflow_id, template, commit=False)
                    for step in await run_steps(session, run.id):
                        await AuditLogRepository(session).log_event(
                            "step_created",
                            "system",
                            run.id,
                            {
                                "step_id": step.id,
                                "step_key": step.step_key,
                                "step_version": step.step_version,
                            },
                            commit=False,
                        )
                await AuditLogRepository(session).log_event(
                    "run_created",
                    "system",
                    run.id,
                    {
                        "actor_id": self.settings.LOCAL_OPERATOR_ID,
                        "template_version": run.template_version,
                        "template_hash": run.template_hash,
                        "step_count": len(template.steps) if template else 0,
                    },
                    commit=False,
                )
            return run

    async def _start(self, run_id: int) -> None:
        async with self._sessions() as session:
            async with immediate_transaction(session):
                repo = WorkflowRunRepository(session)
                status = await repo.get_status(run_id)
                if status in TERMINAL_RUNS:
                    raise RunNotStartableError("Terminal runs cannot be started")
                if status == "pending":
                    await WorkflowService(
                        WorkflowRepository(session), repo, AuditLogRepository(session)
                    ).transition_run(
                        run_id, "running", "system", {"actor_id": self.settings.LOCAL_OPERATOR_ID}
                    )

    async def start_or_resume(self, run_id: int, actor: str | None = None) -> WorkflowRun:
        await self._start(run_id)
        while True:
            async with self._sessions() as session:
                await approval_service(session, self.settings).reconcile_expired(run_id)
            plan = await self._advance(run_id)
            if plan is None:
                return await self._get_run(run_id)
            if not await self._dispatch_and_execute(plan):
                return await self._get_run(run_id)

    def _pre_dispatch(self, definition: StepDefinition, inputs: dict[str, JsonValue]) -> Executor:
        executor = self.registry.select(definition.executor_id, definition.tool_selection)
        working_directory = (definition.tool_selection.options or {}).get("working_directory")
        if (
            working_directory is not None
            and working_directory not in self.settings.APPROVED_WORKING_DIRECTORIES
        ):
            raise ExecutorSelectionError("WORKING_DIRECTORY_NOT_APPROVED")
        if definition.timeout_s > executor.timeout_policy.max_s:
            raise ExecutorSelectionError("TIMEOUT_POLICY_EXCEEDED")
        try:
            executor.input_schema.model_validate(inputs)
        except (ValueError, TypeError) as exc:
            raise ExecutorSelectionError("EXECUTOR_INPUT_INVALID") from exc
        return executor

    async def _fail_ready(
        self,
        session: AsyncSession,
        step: WorkflowStepRun,
        code: str,
        *,
        category: Literal["input_resolution", "executor"] = "executor",
    ) -> None:
        error = StructuredError(
            code=code,
            category=category,
            message="Workflow step validation failed",
            attempt=step.attempt,
        )
        await WorkflowStepRunRepository(session).transition_cas(
            step.id,
            "FAILED",
            from_status=step.status,
            values={"error_json": error.model_dump_json(), "result_status": "failed"},
            commit=False,
        )
        await AuditLogRepository(session).log_event(
            "step_failed",
            "system",
            step.workflow_run_id,
            {
                "step_key": step.step_key,
                "error_code": code,
                "error_category": category,
                "attempt": step.attempt,
            },
            commit=False,
        )

    async def _advance(self, run_id: int) -> DispatchPlan | None:
        async with self._sessions() as session:
            async with immediate_transaction(session):
                run = await WorkflowRunRepository(session).get(run_id)
                if run is None:
                    raise LookupError(f"Run {run_id} does not exist")
                if run.status in TERMINAL_RUNS:
                    return None
                repo = WorkflowStepRunRepository(session)
                rows = await run_steps(session, run_id)
                if any(is_real_failure(row) for row in rows):
                    await repo.settle_to_failed(run_id, commit=False)
                    await derive_and_persist_run_status(run_id, session)
                    return None
                if run.template_snapshot_json is None:
                    if run.status != "waiting_approval":
                        await derive_and_persist_run_status(run_id, session)
                    return None
                template = WorkflowTemplate.model_validate_json(run.template_snapshot_json)
                by_key = {row.step_key: row for row in rows}
                for definition in template.steps:
                    step = by_key[definition.key]
                    if step.status != "PENDING" or not all(
                        by_key[key].status == "SUCCEEDED" for key in definition.depends_on
                    ):
                        continue
                    try:
                        inputs = resolve_inputs(definition.inputs or {}, definition, by_key)
                        if not isinstance(inputs, dict):
                            raise ValueError("Step inputs must resolve to an object")
                    except (ValueError, KeyError, TypeError):
                        await self._fail_ready(
                            session, step, "INPUT_RESOLUTION_FAILED", category="input_resolution"
                        )
                        await repo.settle_to_failed(run_id, commit=False)
                        await derive_and_persist_run_status(run_id, session)
                        return None
                    await repo.transition_cas(
                        step.id,
                        "READY",
                        from_status="PENDING",
                        values={
                            "inputs_json": canonical_json(inputs),
                            "input_digest": canonical_hash(inputs),
                        },
                        commit=False,
                    )
                    await AuditLogRepository(session).log_event(
                        "step_ready",
                        "system",
                        run_id,
                        {"step_key": step.step_key, "input_digest": step.input_digest},
                        commit=False,
                    )
                selected: DispatchPlan | None = None
                for definition in template.steps:
                    step = by_key[definition.key]
                    if step.status != "READY":
                        continue
                    inputs = json.loads(step.inputs_json or "{}")
                    try:
                        executor = self._pre_dispatch(definition, inputs)
                        if canonical_hash(inputs) != step.input_digest:
                            raise ExecutorSelectionError("INPUT_DIGEST_MISMATCH")
                    except ExecutorSelectionError as exc:
                        await self._fail_ready(session, step, exc.code)
                        await repo.settle_to_failed(run_id, commit=False)
                        await derive_and_persist_run_status(run_id, session)
                        return None
                    approval_id = None
                    if definition.approvable:
                        approval_id = await session.scalar(
                            select(ApprovalRequest.id)
                            .where(
                                ApprovalRequest.workflow_run_id == run_id,
                                ApprovalRequest.workflow_step_run_id == step.id,
                                ApprovalRequest.operation == definition.protected_operation,
                                ApprovalRequest.parameters_hash == step.input_digest,
                                ApprovalRequest.actor_id == self.settings.LOCAL_OPERATOR_ID,
                                ApprovalRequest.status == "approved",
                                ApprovalRequest.consumed_at.is_(None),
                                ApprovalRequest.authorization_expires_at > datetime.now(UTC),
                            )
                            .order_by(ApprovalRequest.id.desc())
                            .limit(1)
                        )
                        if approval_id is None:
                            await approval_service(session, self.settings).request_bound_approval(
                                run_id, step.id, commit=False
                            )
                            continue
                    if selected is None and not any(row.status == "RUNNING" for row in rows):
                        selected = DispatchPlan(
                            run_id,
                            step.id,
                            definition,
                            inputs,
                            step.input_digest or "",
                            approval_id,
                            executor,
                        )
                await derive_and_persist_run_status(run_id, session)
                return selected

    async def _dispatch_and_execute(self, plan: DispatchPlan) -> bool:
        owner = f"worker:{uuid4()}"
        try:
            async with self._sessions() as session:
                repo = WorkflowStepRunRepository(session)
                if plan.definition.approvable:
                    if plan.approval_id is None or plan.definition.protected_operation is None:
                        raise OperationNotPermitted("Protected dispatch requires authorization")
                    row = await repo.dispatch_protected_txn(
                        plan.step_id,
                        owner,
                        approval_id=plan.approval_id,
                        operation=plan.definition.protected_operation,
                        parameters_hash=plan.input_digest,
                        actor_id=self.settings.LOCAL_OPERATOR_ID,
                        lease_ttl_s=self.settings.STEP_LEASE_TTL_SECONDS,
                        concurrency_limit=self.settings.RUN_CONCURRENCY,
                        expected_input_digest=plan.input_digest,
                    )
                else:
                    row = await repo.dispatch_cas(
                        plan.step_id,
                        owner,
                        self.settings.STEP_LEASE_TTL_SECONDS,
                        concurrency_limit=self.settings.RUN_CONCURRENCY,
                        expected_input_digest=plan.input_digest,
                    )
                attempt = row.attempt
        except ApprovalNotConsumableError:
            async with self._sessions() as session:
                try:
                    await approval_service(session, self.settings).request_bound_approval(
                        plan.run_id, plan.step_id
                    )
                except OperationNotPermitted:
                    pass
            return False
        except (StepDispatchConflict, OperationNotPermitted):
            return False
        handle = CancellationHandle()
        async with self._sessions() as session:
            active_key = (id(session.bind), plan.run_id, plan.step_id, attempt)
            ACTIVE_EXECUTIONS[active_key] = handle
        try:
            result = await self._execute(plan, owner, attempt, handle, plan.executor)
            await self._persist(plan, owner, attempt, result)
        finally:
            ACTIVE_EXECUTIONS.pop(active_key, None)
        return True

    async def _heartbeat(
        self, plan: DispatchPlan, owner: str, attempt: int, handle: CancellationHandle
    ) -> None:
        interval = min(
            self.settings.STEP_HEARTBEAT_INTERVAL_SECONDS, self.settings.STEP_LEASE_TTL_SECONDS / 3
        )
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._sessions() as session:
                    renewed = await WorkflowStepRunRepository(session).renew_lease(
                        plan.step_id, owner, attempt, self.settings.STEP_LEASE_TTL_SECONDS
                    )
            except SQLAlchemyError:
                renewed = False
            if not renewed:
                await handle.cancel()
                return

    def _execution_error(
        self,
        executor: Executor,
        attempt: int,
        started_at: datetime,
        code: str,
        category: Literal["executor", "timeout", "cancelled"],
        *,
        retryable: bool = False,
    ) -> StepExecutionResult:
        return StepExecutionResult(
            outcome="cancelled" if category == "cancelled" else "failed",
            error=StructuredError(
                code=code,
                category=category,
                message="Execution did not succeed",
                retryable=retryable,
                attempt=attempt,
            ),
            metadata=ExecutionMetadata(
                executor_id=executor.identifier.id,
                executor_version=executor.identifier.version,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            ),
        )

    async def _execute(
        self,
        plan: DispatchPlan,
        owner: str,
        attempt: int,
        handle: CancellationHandle,
        executor: Executor,
    ) -> StepExecutionResult:
        started_at = datetime.now(UTC)
        heartbeat = asyncio.create_task(self._heartbeat(plan, owner, attempt, handle))
        try:
            # Verify ownership after dispatch commits and before invoking the executor.
            async with self._sessions() as session:
                step = await WorkflowStepRunRepository(session).get(plan.step_id)
                if step is None or (step.status, step.lease_owner, step.attempt) != (
                    "RUNNING",
                    owner,
                    attempt,
                ):
                    await handle.cancel()
            if handle.cancelled:
                return self._execution_error(
                    executor, attempt, started_at, "CANCELLED", "cancelled"
                )
            directory = (plan.definition.tool_selection.options or {}).get("working_directory")
            context = ExecutionContext(
                run_id=plan.run_id,
                step_run_id=plan.step_id,
                step_key=plan.definition.key,
                step_version=plan.definition.version,
                inputs=plan.inputs,
                tool_selection=plan.definition.tool_selection.model_copy(deep=True),
                working_directory=directory if isinstance(directory, str) else None,
                env_allowlist={},
                timeout_s=plan.definition.timeout_s,
                cancellation=handle,
                actor_id=self.settings.LOCAL_OPERATOR_ID,
                idempotency_key=f"{plan.run_id}:{plan.definition.key}:{attempt}",
                input_digest=plan.input_digest,
                attempt=attempt,
            )
            result = await asyncio.wait_for(
                executor.execute(context), timeout=plan.definition.timeout_s
            )
            result = StepExecutionResult.model_validate(result.model_dump(mode="python"))
            if (
                result.metadata.executor_id != executor.identifier.id
                or result.metadata.executor_version != executor.identifier.version
            ):
                raise ValueError("Execution metadata does not match selected executor")
            if handle.cancelled:
                return self._execution_error(
                    executor, attempt, started_at, "CANCELLED", "cancelled"
                )
            if result.outcome == "succeeded":
                executor.output_schema.model_validate(result.output)
                if not set(plan.definition.expected_outputs or []) <= (result.output or {}).keys():
                    raise ValueError("Expected step outputs are missing")
            return result
        except TimeoutError:
            return self._execution_error(
                executor,
                attempt,
                started_at,
                "STEP_TIMEOUT",
                "timeout",
                retryable=plan.definition.retry_policy.safe
                and plan.definition.retry_policy.idempotent,
            )
        except asyncio.CancelledError:
            await handle.cancel()
            return self._execution_error(executor, attempt, started_at, "CANCELLED", "cancelled")
        except (ValidationError, ValueError, TypeError, AttributeError):
            return self._execution_error(
                executor, attempt, started_at, "EXECUTOR_RESULT_INVALID", "executor"
            )
        except Exception:
            return self._execution_error(
                executor, attempt, started_at, "EXECUTOR_FAILED", "executor"
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _persist(
        self, plan: DispatchPlan, owner: str, attempt: int, result: StepExecutionResult
    ) -> None:
        async with self._sessions() as session:
            async with immediate_transaction(session):
                repo = WorkflowStepRunRepository(session)
                rows = await run_steps(session, plan.run_id)
                failure_stop = any(is_real_failure(row) for row in rows)
                policy = plan.definition.retry_policy
                retry = (
                    result.outcome == "failed"
                    and result.error is not None
                    and result.error.retryable
                    and policy.safe
                    and policy.idempotent
                    and attempt < policy.max_attempts
                    and not failure_stop
                )
                target = {
                    "succeeded": "SUCCEEDED",
                    "failed": "FAILED",
                    "cancelled": "CANCELLED",
                    "indeterminate": "FAILED",
                }[result.outcome]
                if retry:
                    target = "WAITING_APPROVAL" if plan.definition.approvable else "READY"
                values: dict[str, object] = {
                    "result_status": result.outcome,
                    "error_json": result.error.model_copy(
                        update={"attempt": attempt}
                    ).model_dump_json()
                    if result.error
                    else None,
                }
                if result.outcome == "succeeded":
                    values.update(
                        output_json=canonical_json(result.output),
                        artifact_refs_json=canonical_json(
                            [ref.model_dump(mode="json") for ref in result.artifact_refs]
                        ),
                    )
                if not await repo.persist_result_cas(
                    plan.step_id,
                    target,
                    lease_owner=owner,
                    attempt=attempt,
                    values=values,
                    commit=False,
                ):
                    return
                event = (
                    "step_retry_scheduled"
                    if retry
                    else {
                        "succeeded": "step_succeeded",
                        "failed": "step_failed",
                        "cancelled": "step_cancelled",
                        "indeterminate": "step_failed",
                    }[result.outcome]
                )
                await AuditLogRepository(session).log_event(
                    event,
                    "system",
                    plan.run_id,
                    {
                        "step_key": plan.definition.key,
                        "step_version": plan.definition.version,
                        "attempt": attempt,
                        "next_attempt": attempt + 1 if retry else None,
                        "result_status": result.outcome,
                        "reason": "run_failure_stop" if failure_stop else None,
                        "error_code": result.error.code if result.error else None,
                        "error_category": result.error.category if result.error else None,
                    },
                    commit=False,
                )
                if retry and plan.definition.approvable:
                    await approval_service(session, self.settings).request_bound_approval(
                        plan.run_id, plan.step_id, commit=False
                    )
                if target == "FAILED":
                    await repo.settle_to_failed(plan.run_id, commit=False)
                if target == "CANCELLED":
                    await cancel_workflow_run(
                        plan.run_id, session, self.settings.LOCAL_OPERATOR_ID, commit=False
                    )
                else:
                    await derive_and_persist_run_status(plan.run_id, session)

    async def cancel(self, run_id: int, actor: str | None = None) -> WorkflowRun:
        async with self._sessions() as session:
            run = await cancel_workflow_run(run_id, session, self.settings.LOCAL_OPERATOR_ID)
            await signal_run_cancellation(session, run_id)
            return run

    async def on_approval_resolved(
        self, approval_id: int, decision: Literal["approved", "rejected"], actor: str | None = None
    ) -> WorkflowRun:
        async with self._sessions() as session:
            _, run = await approval_service(session, self.settings).resolve(
                approval_id, decision, self.settings.LOCAL_OPERATOR_ID
            )
        if decision == "approved" and run.status not in TERMINAL_RUNS:
            return await self.start_or_resume(run.id)
        return run
