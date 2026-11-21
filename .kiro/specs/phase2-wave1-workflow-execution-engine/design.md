# DevFlow AI — Phase 2 Wave 1: Workflow Execution Engine Design

**Version:** 2.1.4
**Status:** Final edge-case corrections — awaiting ChatGPT review
**Scope:** Phase 2 Wave 1 — Workflow Execution Engine
**Branch:** `feature/phase2-wave1-workflow-execution-engine`
**Base commit:** `2d077ecb9f96ff767160e6c1564b2c6f8d4e0a9e` (Phase 1 v1.5.9)
**Last Updated:** 2026-11-18
**Companion:** `requirements.md` (EARS requirements + acceptance criteria), `tasks.md` (implementation breakdown).

**Version history:**
- v2.0.0–v2.1.1 — See `docs/reports/phase2-wave1-spec-resolution.md`.
- v2.1.2 — 2026-11-18 — Codex blocker resolution (F16, F17).
- v2.1.3 — 2026-11-18 — Final spec corrections (F18, F19).
- v2.1.4 — 2026-11-18 — Final edge-case corrections (CA, CB). **§5.9** algorithm corrected: `is_failure_stop` derived from persisted step state inside the transaction (not a caller flag); atomic two-step `waiting_approval → running → failed` within one transaction when rule 2 fires and `current == 'waiting_approval'` (CB). **§7.5** `expire_and_re_gate` corrected: pre-check validates `expires_at > now` before returning an existing pending approval; `IntegrityError` catch narrowed to `UniqueConstraintViolation` only; session rollback semantics after `UniqueConstraintViolation` explicitly defined (CA). See `docs/reports/phase2-wave1-v2.1.4-final-resolution.md`.

---

## 1. Design Principles

Wave 1 extends Phase 1 without structural change. The Phase 0/1 layering is preserved:

```
Streamlit UI → APIClient (httpx) → FastAPI routers → Domain services → Repositories → SQLite
                                                   → WorkflowOrchestrator → Executors (in-process)
                                                   → Adapter registry (unchanged)
```

Twelve rules govern Wave 1:

1. **Phase 0/1 contracts are immutable where possible.** `workflow_runs.status` and `approval_requests.status` CHECK constraints are not changed. Extensions are additive columns and one new table.
2. **The eight-state lifecycle is step-level.** `WorkflowRun` keeps Phase 1's six run states, derived from step states.
3. **The orchestrator owns state; executors do not.** Executors receive an immutable context and return a pure result; the orchestrator persists, transitions, leases, consumes, and audits. No executor imports repositories/services/models. Schema types live in `schemas/`; executors import them, never the reverse.
4. **Approvals are consumed before the protected executor runs.** An approval authorizes exactly one operation, one artifact digest, one step, one actor, once, before its authorization expires. Consumption is a CAS `UPDATE` with `consumed_at` in the **same transaction** as lease acquisition; the executor is invoked only after that transaction commits. Consumed approvals are never restored.
5. **Retries are transient, not terminal.** A retryable failure transitions `RUNNING → READY` (non-approvable) or `RUNNING → WAITING_APPROVAL` (approvable, re-gate, fresh authorization) and never passes through `FAILED`. `FAILED` is terminal.
6. **SQLite writes are serialised via the database, not the application.** `BEGIN IMMEDIATE` + `busy_timeout` + CAS `rowcount` checks.
7. **Recovery is idempotent, conservative, and non-dispatching.** Restart recovery reconciles stale state and decides retry-or-fail; it does NOT auto-dispatch; resumed execution requires an explicit `start`.
8. **Stale completions are fenced.** Every post-dispatch transition fences on `lease_owner` + `attempt`; a late return from a superseded dispatch is discarded and cannot re-consume an approval.
9. **Failure-stop is deterministic and settles all remaining steps.** When a step terminally fails, no new dispatch occurs; in-flight steps finish; all remaining non-terminal non-running steps are settled to `FAILED` (`skipped`) via existing transitions; the run reaches `failed` only after the policy has settled everything. No ninth step status.
10. **Honest isolation boundary.** Env-var scoping and path allowlists are application-level; the design explicitly states they are not OS sandboxing.
11. **Wave 1 ships one executor: the in-process mock.** No subprocess, shell, filesystem, or network from executors. No real coding-agent execution. Executor security guards are mandatory.
12. **Phase 1 compatibility is verified against actual code.** Run `waiting_approval` transitions go through `ApprovalService`; legacy `NULL`-template runs preserved; service signatures preserved.

**Wave 1 execution boundary:** `WorkflowOrchestrator` reads the validated `WorkflowTemplate` from the run's immutable snapshot, materialises `WorkflowStepRun` rows, and drives them through the eight-state lifecycle via `WorkflowOrchestrator.start_or_resume`, dispatching to registered `Executor`s, pausing for `ApprovalRequest` gates (consumed atomically before the protected executor runs), and stopping deterministically on failure.

---

## 2. Architecture Diagram (Wave 1 additions)

```
  Developer Machine — 127.0.0.1 only
  ┌────────────────────────────────────────────────────────────────────────────┐
  │  Browser :8501                              CLI / Postman :8000             │
  │  ┌───▼─────────────────────┐       ┌────────────────▼────────────────────┐  │
  │  │ devflow-ui (Streamlit)   │       │ devflow-api (FastAPI/Uvicorn)       │  │
  │  │ 6_Workflows (extended)  │──────▶│ Routers (/api/v1/): workflows,      │  │
  │  └──────────────────────────┘       │   workflow-runs (+start/cancel/audit/ │  │
  │                                      │   steps), approvals (Phase 1 ext.)  │  │
  │                                      │ Services: WorkflowService (reused),  │  │
  │                                      │   ApprovalService (ext),            │  │
  │                                      │   WorkflowOrchestrator (NEW),        │  │
  │                                      │   RecoveryService (NEW), Registry    │  │
  │                                      │ Repositories: RunRepository (ext),   │  │
  │                                      │   StepRunRepository (NEW),           │  │
  │                                      │   ApprovalRequestRepository (ext),  │  │
  │                                      │   AuditLogRepository (Phase 1)     │  │
  │                                      │ Executors: Executor Protocol,       │  │
  │                                      │   MockExecutor (only one)          │  │
  │                                      │ Schemas: workflow_template.py,       │  │
  │                                      │   executor.py, workflow.py          │  │
  │                                      │ SQLAlchemy async + aiosqlite        │  │
  │                                      └──────────────────┬──────────────────┘  │
  │                                              ┌─────────▼──────────┐          │
  │                                              │ devflow.db (SQLite)│          │
  │                                              │ + workflow_step_runs│          │
  │                                              │ + approval_requests  │          │
  │                                              │   (extended cols)    │          │
  │                                              │ + workflow_runs       │          │
  │                                              │   (snapshot cols)     │          │
  │                                              └────────────────────┘          │
  └────────────────────────────────────────────────────────────────────────────┘
  External: NONE in Wave 1 (mock executor only)
```

New packages/modules (proposed paths):
- `src/devflow_api/orchestration.py` — `WorkflowOrchestrator`, `RecoveryService`.
- `src/devflow_api/executors/__init__.py`, `base.py` (`Executor` Protocol only), `registry.py`, `mock.py`.
- `src/devflow_api/models/workflow_step_run.py` — `WorkflowStepRun`.
- `src/devflow_api/schemas/workflow_template.py`, `schemas/executor.py`, `schemas/workflow.py`.
- `src/devflow_api/repositories/workflow_step_run.py` — `WorkflowStepRunRepository` (CAS, lease, consume, fence, settle).
- `alembic/versions/008_add_workflow_execution_engine.py` — migration 008.

---

## 3. New Database Schema (migration 008)

### 3.0 Migration chain

Verified Alembic head: `007_add_github_confirmations`. Migration 008 chains from it:

```
007_add_github_confirmations (verified HEAD)
  └── 008_add_workflow_execution_engine  ← Wave 1 (planned)
```

| Revision ID | `down_revision` | Wave | Changes |
|---|---|---|---|
| `"007_add_github_confirmations"` | `"006_add_mcp_approvals"` | Phase 1 | github_confirmations (existing) |
| `"008_add_workflow_execution_engine"` | `"007_add_github_confirmations"` | Phase 2 W1 | NEW `workflow_step_runs`; ALTER `approval_requests` (additive cols + indexes); ALTER `workflow_runs` (snapshot cols) |

**Implementer note:** re-confirm the Alembic head immediately before authoring migration 008.

### 3.1 `workflow_step_runs` (migration 008 — NEW table)

```sql
CREATE TABLE workflow_step_runs (
    id                  INTEGER  PRIMARY KEY AUTOINCREMENT,
    workflow_run_id     INTEGER  NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_key            TEXT     NOT NULL,
    step_version        INTEGER  NOT NULL,
    executor_id         TEXT     NOT NULL,
    tool_selection_json TEXT,
    depends_on_json     TEXT     NOT NULL DEFAULT '[]',
    inputs_json         TEXT,                              -- resolved at PENDING->READY (NULL until then)
    input_digest        TEXT,
    idempotency_key     TEXT,                              -- f"{run_id}:{step_key}:{attempt}"; set on dispatch (NULL until RUNNING)
    status              TEXT     NOT NULL DEFAULT 'PENDING'
                                 CHECK (status IN (
                                     'PENDING','READY','RUNNING','WAITING_APPROVAL',
                                     'SUCCEEDED','FAILED','CANCELLED','INTERRUPTED')),
    attempt             INTEGER  NOT NULL DEFAULT 0,       -- executions attempted; incremented on READY->RUNNING
    result_status       TEXT,                              -- succeeded|failed|cancelled|indeterminate
    output_json         TEXT,
    artifact_refs_json  TEXT,
    error_json          TEXT,
    lease_owner         TEXT,
    lease_expires_at    DATETIME,
    ready_at            DATETIME,
    dispatched_at       DATETIME,
    started_at          DATETIME,
    completed_at        DATETIME,
    created_at          DATETIME NOT NULL,
    updated_at          DATETIME NOT NULL,
    UNIQUE (workflow_run_id, step_key)
);
CREATE INDEX idx_step_runs_run_id       ON workflow_step_runs(workflow_run_id);
CREATE INDEX idx_step_runs_status       ON workflow_step_runs(status);
CREATE INDEX idx_step_runs_lease_expiry ON workflow_step_runs(lease_expires_at);
```

- `status` CHECK: exactly the eight values. Terminal: `SUCCEEDED`, `FAILED`, `CANCELLED`. Non-terminal recoverable: `INTERRUPTED`.
- `attempt` = executions attempted so far (0 before first dispatch); incremented atomically on `READY → RUNNING`; NOT on retry/recovery (only on the subsequent dispatch).
- `idempotency_key` = `f"{run_id}:{step_key}:{attempt}"` (post-increment); set on dispatch; NULL until first dispatch.
- `lease_owner` + `lease_expires_at` implement leases / duplicate-dispatch prevention / stale detection / fencing.

**SQLAlchemy model (proposed):**
```python
# src/devflow_api/models/workflow_step_run.py
class WorkflowStepRun(TimestampMixin, Base):
    __tablename__ = "workflow_step_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','READY','RUNNING','WAITING_APPROVAL',"
            "'SUCCEEDED','FAILED','CANCELLED','INTERRUPTED')"
        ),
        UniqueConstraint("workflow_run_id", "step_key", name="uq_step_run_key"),
        Index("idx_step_runs_status", "status"),
        Index("idx_step_runs_lease_expiry", "lease_expires_at"),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(Text)
    step_version: Mapped[int] = mapped_column(Integer)
    executor_id: Mapped[str] = mapped_column(Text)
    tool_selection_json: Mapped[str | None] = mapped_column(Text)
    depends_on_json: Mapped[str] = mapped_column(Text, server_default="[]")
    inputs_json: Mapped[str | None] = mapped_column(Text)
    input_digest: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    result_status: Mapped[str | None] = mapped_column(Text)
    output_json: Mapped[str | None] = mapped_column(Text)
    artifact_refs_json: Mapped[str | None] = mapped_column(Text)
    error_json: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

### 3.2 `workflow_runs` additive extension (immutable template snapshot)

Migration 008 adds the snapshot columns to `workflow_runs`; the `status` CHECK is NOT changed:

```sql
ALTER TABLE workflow_runs ADD COLUMN template_snapshot_json TEXT;  -- full validated WorkflowTemplate JSON; NULL for legacy runs
ALTER TABLE workflow_runs ADD COLUMN template_version      INTEGER; -- NULL for legacy runs
ALTER TABLE workflow_runs ADD COLUMN template_hash         TEXT;    -- SHA-256 hex; NULL for legacy runs
CREATE INDEX idx_workflow_runs_template_hash ON workflow_runs(template_hash);
```

- Phase 1 and legacy runs have `NULL` for all three. The orchestrator executes from `template_snapshot_json`, never the live `definition_json` (REQ-E1.8).

### 3.3 `approval_requests` additive extension

Migration 008 adds columns/indexes; the `status` CHECK is NOT changed:

```sql
ALTER TABLE approval_requests ADD COLUMN operation              TEXT;
ALTER TABLE approval_requests ADD COLUMN parameters_hash       TEXT;
ALTER TABLE approval_requests ADD COLUMN actor_id              TEXT;
ALTER TABLE approval_requests ADD COLUMN workflow_step_run_id  INTEGER REFERENCES workflow_step_runs(id) ON DELETE SET NULL;
ALTER TABLE approval_requests ADD COLUMN consumed_at           DATETIME;
ALTER TABLE approval_requests ADD COLUMN expires_at            DATETIME;  -- pending TTL: requested_at + APPROVAL_TTL_SECONDS (NULL = no pending expiry for Phase 1)
ALTER TABLE approval_requests ADD COLUMN authorization_expires_at DATETIME; -- approved validity: resolved_at + APPROVAL_AUTHORIZATION_VALIDITY_SECONDS (NULL = no bound for Phase 1)
CREATE INDEX idx_approval_operation            ON approval_requests(operation);
CREATE INDEX idx_approval_consumed_at          ON approval_requests(consumed_at);
CREATE INDEX idx_approval_step_run_id          ON approval_requests(workflow_step_run_id);
CREATE INDEX idx_approval_expires_at            ON approval_requests(expires_at);
CREATE INDEX idx_approval_authz_expires_at     ON approval_requests(authorization_expires_at);
-- Partial unique index for Phase 2 approval uniqueness (F19): prevents duplicate pending approvals
-- per gated step. Excludes Phase 1 rows (operation IS NULL) so legacy behavior is unaffected.
CREATE UNIQUE INDEX uidx_approval_step_pending
    ON approval_requests(workflow_step_run_id)
    WHERE status = 'pending' AND operation IS NOT NULL;
```

**No `consumed` status value.** Single-use via `consumed_at`; approved validity via `authorization_expires_at`; pending TTL via `expires_at`. An approval is consumable iff `status='approved' AND consumed_at IS NULL AND now < authorization_expires_at`.

**Deletion semantics (no NULL-to-NULL bypass):** `workflow_run_id` remains `ON DELETE CASCADE`; `workflow_step_run_id` is `ON DELETE SET NULL`. Run deletion deletes approvals (via `workflow_run_id`) before the step FK matters, so `SET NULL` never creates an orphaned consumable approval. No code path deletes a single `WorkflowStepRun` without the run (guard test). No trigger required.

### 3.4 Run ↔ step state derivation (no `workflow_runs.status` change)

`workflow_runs.status` keeps Phase 1's six values. The orchestrator derives it and persists via Phase 1 mechanisms (§5.8):

| Run status (Phase 1) | Derivation from `workflow_step_runs` |
|---|---|
| `pending` | Run created; no `start_or_resume` yet (legacy zero-step run also starts `pending`) |
| `running` | After start, while any step is `PENDING`/`READY`/`RUNNING`/`INTERRUPTED` |
| `waiting_approval` | Any step is `WAITING_APPROVAL` (and run not in failure-stop settling) |
| `completed` | All steps `SUCCEEDED` (legacy zero-step run vacuously satisfies this on start) |
| `failed` | Any step terminally `FAILED` (real failure) AND the failure-stop policy (§5.5) has settled all remaining steps (all terminal) AND none cancelled-by-cancel |
| `cancelled` | Run cancelled |

**Terminal derivation rule:** the run reaches `completed`/`failed`/`cancelled` only when **all steps are terminal**. A `FAILED` step engages the failure-stop policy (§5.5): no new dispatch, in-flight steps finish, remaining non-terminal non-running steps settled to `FAILED` (`skipped`); the run reaches `failed` once the policy has settled everything. With default concurrency 1, a `FAILED` step implies no other in-flight step.

---

## 4. Workflow Template Model (Pydantic v2)

`Workflow.definition_json` (Phase 1, `deferred=True, deferred_raiseload=True`) holds a validated `WorkflowTemplate` when present. The `Workflow` model is not modified.

```python
# src/devflow_api/schemas/workflow_template.py
ApprovalOperation = Literal["specification_approval", "implementation_authorization",
                            "pr_creation_approval", "merge_approval"]

class ToolSelection(BaseModel):
    executor_id: str
    tool: str | None = None
    options: dict[str, Any] | None = None

class RetryPolicy(BaseModel):
    safe: bool = False
    idempotent: bool = False
    max_attempts: int = 1   # total executions incl. the first

class StepDefinition(BaseModel):
    key: str
    version: int
    title: str
    description: str = ""
    depends_on: list[str] = []
    executor_id: str
    tool_selection: ToolSelection
    inputs: dict[str, Any] | None = None
    expected_outputs: list[str] | None = None
    timeout_s: int = 60
    retry_policy: RetryPolicy = RetryPolicy()
    approvable: bool = False
    protected_operation: ApprovalOperation | None = None  # required iff approvable

class TemplateDefaults(BaseModel):
    executor_id: str = "mock"
    timeout_s: int = 60
    retry_policy: RetryPolicy = RetryPolicy()

class WorkflowTemplate(BaseModel):
    template_version: int
    defaults: TemplateDefaults
    steps: list[StepDefinition]   # non-empty, unique keys, acyclic depends_on

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowTemplate":
        # unique keys, non-empty, depends_on references exist, acyclic, approvable -> protected_operation set
        ...
```

**Validation:** non-empty `steps`; unique `key`; `depends_on` references exist; acyclic; `approvable` ⇒ `protected_operation` set. `executor_id`/capability checked at dispatch.

**Input reference resolution (REQ-E1.4):** `inputs` may contain `"$steps.<key>.<output_name>"`; resolved at `PENDING → READY`. Unresolved → `PENDING → FAILED` (`INPUT_RESOLUTION_FAILED`).

**Immutable snapshot (REQ-E1.8):** at run creation, the validated `WorkflowTemplate` is serialised to `workflow_runs.template_snapshot_json`; `template_version` and `template_hash = canonical_hash(...)` recorded. The orchestrator parses the snapshot (not the live `definition_json`).

---

## 5. Workflow Orchestration Service

`WorkflowOrchestrator` (new, `src/devflow_api/orchestration.py`) drives runs. Depends on the repositories, `AuditLogRepository`, `ExecutorRegistry`, `WorkflowService`/`ApprovalService` (Phase 1, reused per §5.8), and `Settings`.

### 5.1 Public surface

```python
class WorkflowOrchestrator:
    async def create_run(self, workflow_id: int, actor: str) -> WorkflowRunResponse: ...
    async def start_or_resume(self, run_id: int, actor: str) -> WorkflowRunResponse: ...
    async def cancel(self, run_id: int, actor: str) -> WorkflowRunResponse: ...
    async def on_approval_resolved(self, approval_id: int, decision: str, actor: str) -> WorkflowRunResponse: ...
    # recover() lives on RecoveryService (§9)
```

### 5.2 `create_run` (REQ-E2.1)

```
create_run(workflow_id, actor):
    wf = load Workflow(workflow_id)            # 404 if missing
    raw = wf.definition_json
    if raw is None or raw == "":
        # legacy run (Phase 1 backward compatible): run only, no steps, no snapshot
        BEGIN IMMEDIATE
        insert WorkflowRun(status="pending", template_snapshot_json=NULL, ...)
        audit run_created(actor, template_version=NULL, template_hash=NULL, step_count=0)
        commit
        return run                              # steps == []
    template = WorkflowTemplate.model_validate_json(raw)   # 422 INVALID_WORKFLOW_TEMPLATE
    snapshot = template.model_dump_json(...)
    thash = canonical_hash(snapshot)
    BEGIN IMMEDIATE
    insert WorkflowRun(status="pending", template_snapshot_json=snapshot,
                       template_version=template.template_version, template_hash=thash)
    for step in template.steps:
        insert WorkflowStepRun(... status="PENDING", attempt=0, idempotency_key=NULL ...)
    audit run_created(actor, template_version, template_hash, step_count=len(steps))
    commit                                      # all-or-nothing
    return run
```

### 5.3 `start_or_resume` (REQ-E2.2)

```
start_or_resume(run_id, actor):
    run = load run; steps = list steps for run
    if run.status in {"completed","failed","cancelled"}: raise 409 RUN_NOT_STARTABLE
    if run.status == "pending": WorkflowService.transition_run(run_id, "running", actor)
    failure_stop = any step terminally FAILED (real)   # set when a step failed
    loop until no progress:
        # (a) failure-stop settle (§5.5) takes priority if engaged:
        if failure_stop:
            settle_non_running_steps(run)            # §5.5; transitions PENDING/READY/WAITING_APPROVAL/INTERRUPTED -> FAILED (skipped)
            allow in-flight RUNNING steps to finish (their persist handlers run)
            if all steps terminal: WorkflowService.transition_run(run_id,"failed",actor, {error_message}); return
            return                                   # run stays running until in-flight finish
        # (b) resolve + ready
        for s in steps where s.status=="PENDING" and all deps SUCCEEDED:
            try: inputs = resolve_inputs(s, deps)
            except ResolutionError:
                CAS PENDING->FAILED (WHERE id=? AND status='PENDING')   # no lease fence
                error_json={code:"INPUT_RESOLUTION_FAILED", category:"input_resolution", retryable:false}
                audit step_failed; failure_stop=True; continue
            CAS PENDING->READY (WHERE id=? AND status='PENDING'); set ready_at, inputs_json, input_digest
            audit step_ready
        # (c) gate or dispatch each READY step (bounded by RUN_CONCURRENCY)
        for s in steps where s.status=="READY" (up to concurrency):
            if s.approvable:
                approval = find consumable approval for s
                    (status='approved', consumed_at IS NULL, now < authorization_expires_at,
                     operation=s.protected_operation, workflow_step_run_id=s.id, actor_id=LOCAL_OPERATOR_ID)
                if approval is None:
                    READY->WAITING_APPROVAL (CAS); request_bound_approval(...); audit approval_requested
                    re-derive run running->waiting_approval via ApprovalService (§5.8)
                    continue
                else:
                    dispatch_protected(s, executor, approval)   # §5.4: atomic lease+consume, then execute
            else:
                # pre-dispatch checks -> READY->FAILED on error
                executor = registry.get(s.executor_id)
                if executor is None: CAS READY->FAILED EXECUTOR_NOT_FOUND; failure_stop=True; continue
                if s.tool_selection.working_directory not None and not in APPROVED_WORKING_DIRECTORIES:
                    CAS READY->FAILED WORKING_DIRECTORY_NOT_APPROVED; failure_stop=True; continue
                if not executor.supports(s.tool_selection): CAS READY->FAILED CAPABILITY_MISMATCH; failure_stop=True; continue
                dispatch(s, executor)             # §5.4: atomic lease, then execute
        # (d) after SUCCEEDED steps, re-evaluate dependents (loop)
        re-derive run status per §3.4
    # post-loop: any WAITING_APPROVAL -> run waiting_approval; elif all SUCCEEDED -> run completed
```

### 5.4 Dispatch & result handling (REQ-E2.3, E3.7, E4.6, E4.11)

**Non-approvable dispatch:**
```
dispatch(step, executor):
    lease_owner = f"worker:{uuid4()}"
    next_attempt = step.attempt + 1
    idem = f"{run_id}:{step_key}:{next_attempt}"
    BEGIN IMMEDIATE
       rows = UPDATE step SET status='RUNNING', lease_owner=?, lease_expires_at=now+LEASE_TTL,
                       dispatched_at=now, started_at=now, attempt=next_attempt, idempotency_key=idem
                WHERE id=? AND status='READY'        # CAS
       if rows != 1: rollback; raise StepDispatchConflict
    commit
    context = ExecutionContext(...)
    result = await run_executor(step, executor, context)   # outside DB txn
    persist_result(step, next_attempt, lease_owner, result)  # fenced
```

**Approvable (protected) dispatch — consume BEFORE execute (F14):**
```
dispatch_protected(step, executor, approval):
    lease_owner = f"worker:{uuid4()}"
    next_attempt = step.attempt + 1
    idem = f"{run_id}:{step_key}:{next_attempt}"
    BEGIN IMMEDIATE
       # (1) acquire lease + transition READY->RUNNING + increment attempt (CAS)
       rows = UPDATE step SET status='RUNNING', lease_owner=?, lease_expires_at=now+LEASE_TTL,
                       dispatched_at=now, started_at=now, attempt=next_attempt, idempotency_key=idem
                WHERE id=? AND status='READY'
       if rows != 1: rollback; raise StepDispatchConflict
       # (2) consume the approval atomically in the SAME transaction, BEFORE execute
       consumed = UPDATE approval_requests SET consumed_at=now
                  WHERE id=? AND status='approved' AND consumed_at IS NULL
                    AND (authorization_expires_at IS NULL OR now < authorization_expires_at)
                    AND operation=? AND parameters_hash=? AND workflow_step_run_id=? AND actor_id=?
       if consumed != 1:
           rollback                                # step stays READY, no lease, attempt unchanged — UNEXECUTED
           audit approval_not_consumable(reason)
           return ("re-gate", step)                # caller re-gates READY->WAITING_APPROVAL (§7.4)
    commit
    # (3) executor invoked ONLY after commit, OUTSIDE the DB transaction
    context = ExecutionContext(...)
    result = await run_executor(step, executor, context)
    persist_result(step, next_attempt, lease_owner, result)   # fenced
```

**`run_executor` (outside DB txn):**
```
run_executor(step, executor, context):
    try:
        return await asyncio.wait_for(executor.execute(context), timeout=context.timeout_s)
    except TimeoutError:
        return StepExecutionResult(outcome="failed", error=StructuredError(category="timeout", retryable=policy.safe, ...))
    except CancelledError:
        return StepExecutionResult(outcome="cancelled", error=StructuredError(category="cancelled", retryable=False, ...))
```

**`persist_result` (fenced CAS; REQ-E4.11):**
```
persist_result(step, attempt, lease_owner, result):
    # fence on lease_owner + attempt so a stale completion cannot mutate
    if result.outcome == "succeeded":
        validate result.output against executor.output_schema; on failure -> outcome="failed", category="executor"
        rows = UPDATE step SET status='SUCCEEDED', result_status='succeeded',
                        output_json=?, artifact_refs_json=?, completed_at=now,
                        lease_owner=NULL, lease_expires_at=NULL
                 WHERE id=? AND status='RUNNING' AND lease_owner=? AND attempt=?
        if rows != 1: audit step_stale_completion_discarded(prior_lease_owner, prior_attempt=attempt); return
        audit step_succeeded(step_key, step_version, result_status, attempt)
    elif result.outcome == "failed":
        maybe_retry(step, attempt, lease_owner, result)        # §5.6
    elif result.outcome == "cancelled":
        rows = UPDATE step SET status='CANCELLED', result_status='cancelled', error_json=?, completed_at=now,
                        lease_owner=NULL, lease_expires_at=NULL
                 WHERE id=? AND status='RUNNING' AND lease_owner=? AND attempt=?
        if rows == 1: audit step_cancelled
        else: audit step_stale_completion_discarded
        # NOTE: consumed_at on any approval stays set (never restored) — REQ-E3.7.4
    elif result.outcome == "indeterminate":
        rows = UPDATE step SET status='FAILED', result_status='indeterminate',
                        error_json={category:"executor", retryable:false, ...}, completed_at=now,
                        lease_owner=NULL, lease_expires_at=NULL
                 WHERE id=? AND status='RUNNING' AND lease_owner=? AND attempt=?
        if rows == 1: audit step_failed(indeterminate; not retried); failure_stop=True
        else: audit step_stale_completion_discarded
        # consumed_at stays set (not restored)
```

### 5.5 Stop safely — failure-stop policy (REQ-E2.6, E4.12)

When a step transitions to a real terminal `FAILED` (executor failure / timeout / indeterminate / pre-dispatch error / input-resolution failure), the orchestrator engages the deterministic failure-stop policy:

```
engage_failure_stop(run, failed_step):
    # (a) no new READY->RUNNING dispatch (start_or_resume checks failure_stop and skips dispatch)
    # (b) in-flight RUNNING steps are allowed to finish (their persist_result handlers run);
    #     a retryable failure during failure-stop is suppressed -> RUNNING->FAILED (run_failure_stop), NOT retry
    # (c) settle every remaining non-terminal NON-running step:
    for s in steps where s.status in (PENDING, READY, WAITING_APPROVAL, INTERRUPTED):
        reason = "STEP_BLOCKED_BY_RUN_FAILURE"
        if s.status == "WAITING_APPROVAL":
            CAS WAITING_APPROVAL->FAILED (WHERE id=? AND status='WAITING_APPROVAL')   # NEW transition (no new status)
            expire its pending approval(s): UPDATE approval_requests SET status='expired' WHERE workflow_step_run_id=s.id AND status='pending'
        elif s.status == "PENDING":
            CAS PENDING->FAILED (WHERE id=? AND status='PENDING')
        elif s.status == "READY":
            CAS READY->FAILED (WHERE id=? AND status='READY')
        elif s.status == "INTERRUPTED":
            CAS INTERRUPTED->FAILED (WHERE id=? AND status='INTERRUPTED')
        set error_json={code:reason, category:"skipped", retryable:false}, completed_at=now
        audit step_failed(step_key, code=reason, category="skipped", attempt)
    # (d) after all steps terminal (including in-flight that finished), run -> failed
    if all steps terminal:
        WorkflowService.transition_run(run_id, "failed", actor, {error_message: failed_step.error_message})
```

- **Idempotent:** re-entering failure-stop does not re-settle already-terminal steps (CAS `WHERE status=<expected>` is a no-op once terminal).
- **No new dispatch:** `start_or_resume` checks `failure_stop` before dispatching; under failure-stop it only settles and lets in-flight steps finish.
- **No ninth status:** settle reuses existing `PENDING → FAILED`, `READY → FAILED`, `INTERRUPTED → FAILED` transitions plus the new `WAITING_APPROVAL → FAILED` transition (between existing states).
- **Run → failed only after settle:** the run reaches `failed` once all steps are terminal (in-flight finished + blocked settled).

### 5.6 Bounded retries (REQ-E4.7)

```
maybe_retry(step, attempt, lease_owner, result):
    policy = step.retry_policy
    in_failure_stop = run in failure-stop (another step already FAILED)
    retryable = (result.error.retryable and policy.safe and policy.idempotent and attempt < policy.max_attempts
                 and not in_failure_stop)
    if retryable:
        # transient: RUNNING -> READY (non-approvable) or RUNNING -> WAITING_APPROVAL (approvable, fresh authz)
        target = "WAITING_APPROVAL" if step.approvable else "READY"
        rows = UPDATE step SET status=target, lease_owner=NULL, lease_expires_at=NULL
                 WHERE id=? AND status='RUNNING' AND lease_owner=? AND attempt=?   # fenced
        if rows == 1:
            if step.approvable:
                request_bound_approval(...)   # fresh approval (consumed one is spent, not restored)
            audit step_retry_scheduled(step_key, next_attempt=attempt+1, reason=result.error)
        else:
            audit step_stale_completion_discarded; return
        # loop re-dispatches (READY->RUNNING increments attempt; approvable re-gate -> approved -> dispatch_protected consumes fresh)
    else:
        # terminal FAILED (non-retryable / budget exhausted / indeterminate / failure-stop)
        rows = UPDATE step SET status='FAILED', result_status='failed', error_json=?, completed_at=now,
                        lease_owner=NULL, lease_expires_at=NULL
                 WHERE id=? AND status='RUNNING' AND lease_owner=? AND attempt=?
        if rows == 1:
            reason = "run_failure_stop" if in_failure_stop else (result.error.code or "exhausted")
            audit step_failed(step_key, error_code, error_category, attempt, reason)
            engage_failure_stop(run, step)        # §5.5
        else:
            audit step_stale_completion_discarded
```

`indeterminate` outcomes bypass `maybe_retry` and go straight to terminal `FAILED` in `persist_result` (never retried). Under failure-stop, `retryable` is forced false (retry suppressed → `RUNNING → FAILED`, reason `run_failure_stop`).

### 5.7 Cancel (REQ-E2.7)

```
cancel(run_id, actor):
    BEGIN IMMEDIATE
    for s in steps where s.status in (PENDING, READY, RUNNING, WAITING_APPROVAL, INTERRUPTED):
        if s.status == "RUNNING":
            UPDATE step SET status='CANCELLED', completed_at=now, lease_owner=NULL, lease_expires_at=NULL
             WHERE id=? AND status='RUNNING' AND lease_owner=? AND attempt=?   # fenced
        else:
            UPDATE step SET status='CANCELLED', completed_at=now
             WHERE id=? AND status=?   # status in (PENDING, READY, WAITING_APPROVAL, INTERRUPTED)
    signal cancellation to in-flight executor calls via CancellationHandle
    run_repo.transition_status(run_id, "cancelled", from_states=["pending","running","waiting_approval"])
    audit run_cancelled(actor, cancelled_step_count)
    commit
    # idempotent on terminal runs (no mutation; HTTP 200)
    # NOTE: consumed_at on any approval stays set (never restored) — REQ-E2.7.4
```

### 5.8 Run transitions — Phase 1 service compatibility (REQ-E2.9)

| Run transition | Mechanism | Phase 1 basis |
|---|---|---|
| `pending → running` | `WorkflowService.transition_run(run_id, "running", actor)` | `WORKFLOW_TRANSITIONS["pending"]={"running"}` |
| `running → completed` | `derive_and_persist_run_status` → `WorkflowService.transition_run(run_id, "completed", actor)` | `WORKFLOW_TRANSITIONS["running"]` |
| `running → failed` | `derive_and_persist_run_status` → `WorkflowService.transition_run(run_id, "failed", actor, ...)` | `WORKFLOW_TRANSITIONS["running"]` |
| `running → waiting_approval` | `ApprovalService.request_bound_approval(...)` → `run_repo.transition_status(run_id, "waiting_approval", ["running"])` — **first gate only**; subsequent gates while already `waiting_approval` skip the run transition (idempotent) | Phase 1 `request_approval` |
| `waiting_approval → running` | `derive_and_persist_run_status` → `run_repo.transition_status(run_id, "running", ["waiting_approval"])` — only called when no step is `WAITING_APPROVAL` | Phase 1 `resolve` |
| `waiting_approval → cancelled` | `ApprovalService.resolve` (reject) → `run_repo.transition_status(run_id, "cancelled", ["waiting_approval"])`, or cancel → `derive_and_persist_run_status` → cancel path | Phase 1 `resolve` |
| `* → cancelled` (user cancel) | `run_repo.transition_status(run_id, "cancelled", ["pending","running","waiting_approval"])` | `transition_status` from-set |

The orchestrator never introduces a run state outside Phase 1's six values.

**Multi-gate note (F17):** when two independent steps both reach approval gates, the second `request_bound_approval` call finds the run already `waiting_approval`. The `running → waiting_approval` transition requires `from_state = ["running"]` per `VALID_FROM`; calling it again with `from_state = ["waiting_approval"]` is not in `VALID_FROM` and would fail. The design handles this by **skipping the run transition for the second and subsequent gates** — only the first gate uses `request_bound_approval`'s `transition_status` call; subsequent gates transition only the step (`READY → WAITING_APPROVAL`) and call `derive_and_persist_run_status` to confirm the run is still `waiting_approval`. `VALID_FROM` is not violated because `waiting_approval → waiting_approval` is never issued as a `transition_status` call; the derivation simply leaves the run in `waiting_approval`.

---

### 5.9 Run status derivation — `derive_and_persist_run_status` (REQ-E2.10, F17, F18, CB)

This helper replaces the previous pattern of calling `transition_status` directly at specific points. It is the single authority for computing and persisting run status after any step state change.

```python
async def derive_and_persist_run_status(run_id: int, session: AsyncSession) -> str:
    """
    BEGIN IMMEDIATE:
      current_run_status = SELECT status FROM workflow_runs WHERE id = run_id
      if current_run_status in ('completed', 'failed', 'cancelled'):
          return current_run_status   # terminal; no-op

      step_statuses = SELECT status, result_status, error_json
                        FROM workflow_step_runs WHERE workflow_run_id = run_id

      # CB: derive is_failure_stop from persisted step state, not from caller flag.
      is_failure_stop = any(
          s.status == 'FAILED'
          and s.result_status not in (None,)      # real failure
          and 'skipped' not in (s.error_json or '') # not a settle-only failure
          for s in step_statuses
      )

      all_terminal = all(s.status in ('SUCCEEDED','FAILED','CANCELLED') for s in step_statuses)
      target = _compute_precedence(step_statuses, is_failure_stop, all_terminal)

      if target == current_run_status:
          return target   # no-op

      # CB: special case — waiting_approval → failed requires two steps.
      if target == 'failed' and current_run_status == 'waiting_approval' and is_failure_stop and all_terminal:
          # Two-step: waiting_approval → running (without commit) → failed (without commit)
          run_repo.transition_status(run_id, 'running', ['waiting_approval'], commit=False)
          run_repo.transition_status(run_id, 'failed', ['running'], commit=False)
      else:
          valid_from = VALID_FROM.get(target, ())
          if current_run_status not in valid_from:
              raise InvalidStatusTransitionError(run_id, current_run_status, target)
          run_repo.transition_status(run_id, target, [current_run_status], commit=False)

    commit
    return target
    """
```

**Status-precedence table** (applied in order; first matching rule wins):

| Priority | Condition | Derived run status | Transition(s) issued |
|---|---|---|---|
| 1 | Run is `cancelled` (terminal; no-op) | `cancelled` | (none) |
| 2 | `is_failure_stop` AND all steps terminal | `failed` | `running → failed` ✓; or atomic two-step `waiting_approval → running → failed` if `current == waiting_approval` (CB) |
| 3 | `is_failure_stop` AND any step non-terminal | `running` | `waiting_approval → running` ✓ or no-op |
| 4 | (NOT `is_failure_stop`) AND any step `WAITING_APPROVAL` | `waiting_approval` | `running → waiting_approval` ✓ or no-op |
| 5 | Any step `READY` or `RUNNING` or `INTERRUPTED` | `running` | `waiting_approval → running` ✓ or no-op |
| 6 | All steps `SUCCEEDED` | `completed` | `running → completed` ✓ |
| 7 | All steps `PENDING` (zero-step or unstarted) | `pending` | (no transition needed) |

**CB — `is_failure_stop` derived from persisted state.** The flag is computed inside the transaction from step rows. This prevents `ApprovalService.resolve` (which is unaware of failure-stop) from calling `derive` and incorrectly computing `waiting_approval` for a run where failure-stop is already engaged. Any call to `derive` will read the same persisted state and reach the same conclusion.

**CB — Atomic two-step `waiting_approval → running → failed`.** When rule 2 fires and `current_run_status == 'waiting_approval'`, two `transition_status` calls are made with `commit=False` before the single final commit. This guarantees the transition is atomic: no other caller sees the intermediate `running` state. Both transitions are legal per `VALID_FROM` (`waiting_approval → running`: `VALID_FROM["running"] = ("pending","waiting_approval")`; `running → failed`: `VALID_FROM["failed"] = ("running",)`).

**Concurrent safety (CB).** Two concurrent calls both reaching rule 2 with `current == 'waiting_approval'`: the loser's first `transition_status` CAS (`waiting_approval → running`) gets `rowcount == 0` (the winner already moved the run). The loser catches `InvalidStatusTransitionError`, re-reads the run, finds it `failed`, and returns. No loop needed.

Executors are isolated from state and persistence. The `Executor` Protocol lives in `src/devflow_api/executors/base.py`; all contract Pydantic types live in `src/devflow_api/schemas/executor.py`.

```python
# src/devflow_api/schemas/executor.py
class ExecutorIdentifier(BaseModel): id: str; version: int
class Capability(BaseModel): operation: str; tool: str | None = None
class TimeoutPolicy(BaseModel): default_s: int; max_s: int
class CancellationHandle(BaseModel):
    cancelled: bool
    async def cancel(self) -> None: ...
    async def wait(self) -> None: ...
class ArtifactRef(BaseModel): name: str; digest: str; uri: str | None = None
class StructuredError(BaseModel):
    code: str
    category: str   # executor | timeout | cancelled | input_resolution | internal | skipped
    message: str
    retryable: bool
class ExecutionMetadata(BaseModel):
    executor_id: str; executor_version: int; started_at: datetime; finished_at: datetime; logs: list[str] = []
class ExecutionContext(BaseModel):
    run_id: int; step_run_id: int; step_key: str; step_version: int
    inputs: dict[str, Any]; tool_selection: ToolSelection
    working_directory: str | None; env_allowlist: dict[str, str]
    timeout_s: int; cancellation: CancellationHandle
    actor_id: str; idempotency_key: str; input_digest: str; attempt: int
    model_config = {"frozen": True}
class StepExecutionResult(BaseModel):
    outcome: str  # succeeded | failed | cancelled | indeterminate
    output: dict[str, Any] | None = None
    artifact_refs: list[ArtifactRef] = []
    error: StructuredError | None = None
    metadata: ExecutionMetadata
```

```python
# src/devflow_api/executors/base.py
class Executor(Protocol):
    identifier: ExecutorIdentifier
    capabilities: list[Capability]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    timeout_policy: TimeoutPolicy
    async def execute(self, context: ExecutionContext) -> StepExecutionResult: ...
    def supports(self, tool_selection: ToolSelection) -> bool: ...
```

**Invariants (mandatory guards):** no `schemas/*` imports `devflow_api.executors`; executors do not import repositories/services/models; no `os.environ`; no subprocess/shell; no writes outside `context.working_directory`. The orchestrator validates `result.output` against `executor.output_schema` before persisting success. The executor is invoked only after the dispatch transaction (lease + consume) commits.

---

## 7. Approval Binding, Validity, Pre-Execution Consumption, Re-gating

### 7.1 Canonical digest (REQ-E3.2)

```python
def canonical_hash(payload: object) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```
Same function at issuance and consumption. Server-side identity (later waves) from server config, not the client.

### 7.2 `consume_atomic` — BEFORE execution, in the dispatch transaction (REQ-E3.3, E3.7)

```python
# invoked inside the SAME BEGIN IMMEDIATE transaction that acquired the lease (§5.4 dispatch_protected)
async def consume_atomic(self, approval_id, operation, parameters_hash,
                         workflow_step_run_id, actor_id) -> bool:
    now = datetime.now(UTC)
    result = await conn.execute(text("""
        UPDATE approval_requests
           SET consumed_at = :now
         WHERE id = :id
           AND status = 'approved'
           AND consumed_at IS NULL
           AND (authorization_expires_at IS NULL OR :now < authorization_expires_at)
           AND operation = :op
           AND parameters_hash = :ph
           AND workflow_step_run_id = :srid
           AND actor_id = :actor
    """), {...})
    return result.rowcount == 1   # caller rolls back the whole dispatch txn if False
```

- The predicate validates **all seven**: operation, parameter digest, step binding, actor, status (`approved`), consumption marker (`consumed_at IS NULL`), authorization validity (`now < authorization_expires_at`).
- `rowcount == 1` → consumed; the dispatch transaction commits and the executor is invoked (outside the txn).
- `rowcount == 0` → the dispatch transaction rolls back (step stays `READY`, no lease, `attempt` unchanged — **unexecuted**); audit `approval_not_consumable` with the mismatch reason; the step re-gates (§7.4). The approval row is not mutated.
- `authorization_expires_at IS NULL` keeps Phase 1 approvals consumable (backward compatible).
- **No restore:** `consumed_at`, once set, is never set back to `NULL` by any code path (cancellation, failure, timeout, indeterminate, stale completion).

### 7.3 Approval lifecycle (REQ-E3.4, E3.6)

```
pending   ──approve (< expires_at)──▶ approved  (set authorization_expires_at = resolved_at + VALIDITY)
   │                                     │
   │reject                               │ consume BEFORE execute (< authorization_expires_at, consumed_at IS NULL)
   ▼                                     ▼
rejected                              consumed (consumed_at set; status stays "approved"; NEVER restored)
   │
   │expire (expires_at <= now, pending only)   OR failure-stop settle expires pending approvals
   ▼
expired  (cannot be resolved; cannot be consumed)
```
- `pending` → `approved`/`rejected` (resolve; reject if `now > expires_at` → 409 `APPROVAL_EXPIRED`).
- `pending` → `expired` (TTL sweep on `expires_at <= now`; also expired by failure-stop settle for `WAITING_APPROVAL` steps).
- **Expiry response code (F16):** `APPROVAL_EXPIRED` is returned for a Phase 2 approval whenever the pending TTL has elapsed, **whether or not the sweep has already set status to `expired`**. Pre-sweep: `status == 'pending' AND now >= expires_at` → 409 `APPROVAL_EXPIRED`. Post-sweep: `status == 'expired' AND operation IS NOT NULL` (Phase 2) → 409 `APPROVAL_EXPIRED`. Phase 1 approvals (`expires_at IS NULL`) always return `APPROVAL_ALREADY_RESOLVED` for non-pending status. This ensures Codex and callers get the same response regardless of when the TTL sweep runs.
- `approved` → consumed (`consumed_at` set; status unchanged; consumable while `now < authorization_expires_at` and `consumed_at IS NULL`); consumed is terminal for authorization (never restored).
- An `approved` authorization past `authorization_expires_at` is not consumable → the step re-gates (§7.4).

### 7.4 Re-gating (REQ-E2.4, E3.6, E3.7)

An approvable `READY` step re-gates (`READY → WAITING_APPROVAL`) with a fresh `ApprovalRequest` whenever it has no consumable approval:
- none approved yet (initial gate);
- the approved one expired (`now >= authorization_expires_at`);
- the approved one was already consumed (by a prior dispatch);
- the atomic dispatch-and-consume transaction failed authorization (consume `rowcount == 0` → rollback → unexecuted → re-gate).

The step is NOT dispatched and NOT failed for an expired/consumed authorization — it waits for a fresh human decision.

**Re-gating from `WAITING_APPROVAL` (expiry during wait, F16):** when a step is `WAITING_APPROVAL` and its pending approval expires before the human resolves it, the step cannot go through `READY` (it is not `READY`; the step status is `WAITING_APPROVAL`). Instead, `expire_and_re_gate` (§7.5) handles this in one atomic transaction: expire the old approval, create a new one, leave the step `WAITING_APPROVAL`. This avoids the need for any intermediate step transition and does not require a `VALID_FROM` run-transition call.

### 7.5 `expire_and_re_gate` (REQ-E3.8, F16, F19, CA)

```python
async def expire_and_re_gate(
    step_run_id: int,
    new_approval_data: dict,
    run_id: int,
    session: AsyncSession,
) -> ApprovalRequest:
    """
    Guard: refuse (OperationNotPermitted) if run is cancelled or failure-stop engaged.

    Retry up to 3 times on SQLITE_BUSY (10 ms, 20 ms, 40 ms backoff).

    BEGIN IMMEDIATE:
      now = datetime.now(UTC)

      # (1) CA: pre-check with expiry validation — existing pending must be in the future.
      existing = SELECT * FROM approval_requests
                  WHERE workflow_step_run_id = step_run_id
                    AND status = 'pending'
                    AND operation IS NOT NULL   -- Phase 2 only
                    AND expires_at > :now        -- CA: must be strictly in the future
      if existing:
          audit approval_re_gate_noop(step_run_id, approval_id=existing.id)
          commit
          return existing

      # (2) Expire any old pending approval (pre-sweep: status='pending'; post-sweep: no-op).
      expire_rows = UPDATE approval_requests SET status = 'expired'
                     WHERE workflow_step_run_id = step_run_id AND status = 'pending'
      # rowcount == 0 (post-sweep or CA-expired) or 1 (pre-sweep).

      # (3) Insert the new approval.
      #     uidx_approval_step_pending enforces uniqueness at the DB level.
      try:
          new_approval = INSERT approval_requests (..., status='pending', expires_at=now+TTL)
      except UniqueConstraintViolation:  # CA: narrowed — ONLY unique index violations
          # Roll back the session and reset state before any further DB work (CA).
          await session.rollback()
          # Re-open BEGIN IMMEDIATE to re-read the winner's row.
          async with session.begin():
              existing = SELECT * FROM approval_requests
                          WHERE workflow_step_run_id = step_run_id
                            AND status = 'pending'
                            AND operation IS NOT NULL
                            AND expires_at > :now    # CA: verify it is still valid
          if existing:
              return existing  # return winner's row; audit as re_gate_noop (outside the txn)
          # Extremely rare: winner's approval also expired between insert and re-read.
          # Raise DatabaseBusyError to let the caller retry the whole reconciliation pass.
          raise DatabaseBusyError("concurrent re-gate conflict; retry required")
      except IntegrityError:  # CA: all other IntegrityError subclasses re-raise unchanged
          await session.rollback()
          raise

      # (4) Audit
      if expire_rows == 1:
          INSERT workflow_audit_log (approval_expired)
      INSERT workflow_audit_log (approval_re_gated, approval_id=new_approval.id)
    commit
    return new_approval
    """
```

**Design notes (CA, F19):**
- **CA — Stale pending reuse prevented.** Step (1) includes `AND expires_at > :now`. A pending approval that is itself expired (`expires_at <= now`) is not returned as a reusable replacement; it falls through to step (2) where it is expired and replaced.
- **CA — Narrowed `IntegrityError` catch.** Only `UniqueConstraintViolation` (the specific SQLAlchemy/sqlite3 error class for `UNIQUE` constraint failures) is caught as a concurrency conflict. `ForeignKeyViolation`, `CheckConstraintViolation`, and all other subclasses of `IntegrityError` re-raise unchanged.
- **CA — Session rollback semantics.** After catching `UniqueConstraintViolation`, `await session.rollback()` is called before any further database work. SQLAlchemy's async session enters an invalid state after an unhandled integrity error; the rollback resets it. The re-read uses a fresh `session.begin()` context.
- **F19 — Partial unique index.** `uidx_approval_step_pending` on `approval_requests(workflow_step_run_id) WHERE status = 'pending' AND operation IS NOT NULL` enforces uniqueness at the database level. Phase 1 rows (`operation IS NULL`) are excluded.
- The step status stays `WAITING_APPROVAL` throughout; no step CAS is issued; no run status change is needed.
- `SQLITE_BUSY` on `BEGIN IMMEDIATE` is retried with bounded exponential backoff (up to 3 attempts); on exhaustion, `DatabaseBusyError` propagates.
- **Guard.** If the run is `cancelled` or failure-stop is engaged, `expire_and_re_gate` raises `OperationNotPermitted` before touching the database.

### 7.6 `request_bound_approval` (REQ-E2.4, new; preserves Phase 1 `request_approval`)

```python
async def request_bound_approval(self, run_id, step_run_id, step_key, operation,
                                 parameters_hash, actor_id, action_description, payload):
    async with transaction(self._session):
        run_repo.transition_status(run_id, "waiting_approval", ["running"], commit=False)  # Phase 1 pattern
        approval = approval_repo.create({
            "workflow_run_id": run_id, "workflow_step_run_id": step_run_id,
            "step_name": step_key, "action_description": action_description,
            "payload_json": json.dumps(payload, allow_nan=False) if payload else None,
            "operation": operation, "parameters_hash": parameters_hash, "actor_id": actor_id,
            "expires_at": now + APPROVAL_TTL_SECONDS,
        }, commit=False)
        step_repo.transition_cas(step_run_id, "WAITING_APPROVAL", from_status="READY", commit=False)
        audit_repo.log_event("approval_requested", "system", run_id,
            {"approval_id": approval.id, "step_name": step_key, "operation": operation,
             "parameters_hash": parameters_hash}, commit=False)
    return approval
```
Phase 1 `request_approval(...)` is preserved unchanged; `request_bound_approval` is the additive Phase 2 path.

### 7.7 Extended `resolve` (REQ-E2.5)

Phase 1 `resolve(approval_id, decision, resolved_by)` is extended additively. The expiry check for Phase 2 approvals precedes the `already_resolved` check (F16):

```python
# resolve order of checks (Phase 2 approval has operation IS NOT NULL):
if approval.status == 'pending' and approval.expires_at and now >= approval.expires_at:
    raise APPROVAL_EXPIRED          # pre-sweep: still 'pending' but past TTL
if approval.status == 'expired' and approval.operation is not None:
    raise APPROVAL_EXPIRED          # post-sweep: sweep already set 'expired'
if approval.status != 'pending':
    raise APPROVAL_ALREADY_RESOLVED  # already approved/rejected/consumed (Phase 1 or Phase 2)
# ... proceed with resolve
```

On `approved`: set `status = 'approved'`, `resolved_at`, `resolved_by`, `authorization_expires_at = resolved_at + VALIDITY`. For Phase 2 gate: step `WAITING_APPROVAL → READY` (CAS). Call `derive_and_persist_run_status` — the run transitions to `running` only if no other `WAITING_APPROVAL` steps remain; otherwise stays `waiting_approval`.

On `rejected`: step `WAITING_APPROVAL → CANCELLED` (CAS). Call `derive_and_persist_run_status` — the run cancels.

### 7.8 Replay, stale-completion & no-restore defense (REQ-E6.5, E4.11, E3.7)

- **Replay:** consume is a CAS; a second use gets `rowcount == 0` (and rolls back the dispatch).
- **Stale completion:** consume happens at dispatch (inside the lease+consume txn). A stale completion from a superseded dispatch: its persist CAS fences on `lease_owner`+`attempt` and fails (`rowcount == 0`) → discarded; it never reaches a second consume. (The approval was consumed by the original dispatch's transaction; a stale completion cannot re-consume.)
- **No restore:** `consumed_at` is never set back to `NULL`. Cancellation (`RUNNING → CANCELLED`), failure (`RUNNING → FAILED`), timeout, and indeterminate all leave `consumed_at` set. A protected-op retry re-gates for a fresh approval rather than reusing the consumed one (REQ-E4.7, E3.7.5).
- **Cross-binding:** operation/digest/step/actor bindings prevent authorizing a different action/step/actor.

---

## 8. Step State-Transition Specification

### 8.1 States and meaning

| State | Meaning | Terminal? |
|---|---|---|
| `PENDING` | Created; dependencies not yet all `SUCCEEDED`; inputs not resolved. | no |
| `READY` | Dependencies `SUCCEEDED`; inputs resolved; eligible for dispatch or approval gate. | no |
| `RUNNING` | Dispatched; lease held; (for approvable, approval already consumed at dispatch). | no |
| `WAITING_APPROVAL` | Approval gate reached (initial or re-gate); paused for human decision. | no |
| `SUCCEEDED` | Executor success; outputs persisted. | yes |
| `FAILED` | Terminal: non-retryable, budget exhausted, pre-dispatch error, input-resolution failure, indeterminate, or failure-stop settle (`skipped`) / retry-suppression (`run_failure_stop`). | yes |
| `CANCELLED` | Run cancelled (or approval rejected). | yes |
| `INTERRUPTED` | Process death / lease expired; pending recovery decision. | no (recoverable) |

### 8.2 Transition map (authoritative)

```
PENDING  ──deps SUCCEEDED + inputs resolved──▶ READY
PENDING  ──input resolution failure──▶ FAILED
PENDING  ──failure-stop settle──▶ FAILED (skipped)
PENDING  ──run cancel──▶ CANCELLED
READY    ──dispatch (CAS, lease, attempt++; approval consumed in same txn if approvable)──▶ RUNNING
READY    ──approvable gate / re-gate──▶ WAITING_APPROVAL
READY    ──pre-dispatch error (executor/dir/capability)──▶ FAILED
READY    ──failure-stop settle──▶ FAILED (skipped)
READY    ──run cancel──▶ CANCELLED
RUNNING  ──executor success (fenced)──▶ SUCCEEDED
RUNNING  ──retryable failure, safe+idempotent+attempt<max, NON-approvable──▶ READY        (transient; NOT FAILED)
RUNNING  ──retryable failure, approvable──▶ WAITING_APPROVAL        (re-gate, fresh authorization; NOT FAILED)
RUNNING  ──terminal failure (non-retryable / attempt>=max / indeterminate)──▶ FAILED
RUNNING  ──retryable failure under failure-stop──▶ FAILED (run_failure_stop; retry suppressed)
RUNNING  ──recovery: lease expired (process death)──▶ INTERRUPTED
RUNNING  ──run cancel (fenced)──▶ CANCELLED
WAITING_APPROVAL ──approved──▶ READY
WAITING_APPROVAL ──rejected / run cancel──▶ CANCELLED
WAITING_APPROVAL ──failure-stop settle──▶ FAILED (skipped)
WAITING_APPROVAL ──pending approval expired → expire_and_re_gate──▶ WAITING_APPROVAL   [self-transition; step status unchanged; new approval created atomically; run status unchanged]
INTERRUPTED ──recovery: safe + attempt<max (run not in failure-stop)──▶ READY   (no auto-dispatch; awaits start)
INTERRUPTED ──recovery: unsafe / attempt>=max──▶ FAILED
INTERRUPTED ──failure-stop settle──▶ FAILED (skipped)
INTERRUPTED ──run cancel──▶ CANCELLED
SUCCEEDED / FAILED / CANCELLED ──► (terminal; no outgoing transitions)
```

Every transition is a CAS inside `BEGIN IMMEDIATE`. Post-dispatch transitions (`RUNNING → *`) fence on `lease_owner` + `attempt` (REQ-E4.11). For an approvable step, the `READY → RUNNING` CAS and the approval consume are in **one transaction** (REQ-E3.7). Invalid transitions are rejected (`INVALID_STEP_TRANSITION`), audited as `step_transition_rejected`, and leave no mutation. **A retry never passes through `FAILED`**; `FAILED` is terminal. **No ninth step status** is introduced — failure-stop settle reuses existing transitions to `FAILED` plus the new `WAITING_APPROVAL → FAILED` and `RUNNING → WAITING_APPROVAL` transitions (all between existing states).

### 8.3 Run derivation

`workflow_runs.status` (Phase 1 six values) is derived per §3.4 and persisted via Phase 1 mechanisms (§5.8), preserving `run_status_changed` audit. Terminal run states are reached only when all steps are terminal (failure-stop settled).

---

## 9. Recovery

### 9.1 Startup recovery (`RecoveryService.recover`, REQ-E4.3)

```
recover():
    # Phase 1 — interrupt stale RUNNING (automatic reconciliation)
    BEGIN IMMEDIATE
    rows = SELECT id, lease_owner, lease_expires_at, attempt, step_key FROM workflow_step_runs WHERE status='RUNNING'
    for r in rows:
        if r.lease_expires_at is None or r.lease_expires_at <= now:
            rows2 = UPDATE workflow_step_runs SET status='INTERRUPTED', lease_owner=NULL, lease_expires_at=NULL
                    WHERE id=r.id AND status='RUNNING' AND lease_owner=r.lease_owner AND attempt=r.attempt  # fenced
            if rows2 == 1: audit step_interrupted(prior_lease_owner, prior_lease_expires_at)
    commit
    # Phase 2 — decide retry-or-fail for each INTERRUPTED (idempotent; CAS on status='INTERRUPTED')
    rows = SELECT ... FROM workflow_step_runs WHERE status='INTERRUPTED'
    in_failure_stop = run has any terminally FAILED step
    for r in rows:
        policy = step.retry_policy
        if in_failure_stop or not (policy.safe and policy.idempotent and r.attempt < policy.max_attempts):
            UPDATE ... SET status='FAILED', error_json={category:"internal" or "skipped", retryable:false}, completed_at=now
                 WHERE id=r.id AND status='INTERRUPTED'
            audit step_recovery_decided(decision="fail", reason=("failure_stop" or "unsafe_or_budget_exhausted"), attempt=r.attempt)
        else:
            UPDATE ... SET status='READY' WHERE id=r.id AND status='INTERRUPTED'
            audit step_recovery_decided(decision="retry", attempt=r.attempt)
    re-derive affected runs (do NOT dispatch)
    return count_interrupted
```

- A `RUNNING` step with a still-valid lease is NOT interrupted.
- Idempotent (CAS `WHERE status='INTERRUPTED'` is a no-op once decided).
- **No auto-dispatch:** recovered `READY` steps wait for explicit `start_or_resume`.
- If the run is in failure-stop, `INTERRUPTED → FAILED` (`skipped`).

### 9.2 Stale detection (lease model, REQ-E4.10)

- `STEP_LEASE_TTL_SECONDS` (60); heartbeat every `STEP_HEARTBEAT_INTERVAL_SECONDS` (15).
- Renewal: `UPDATE ... SET lease_expires_at=? WHERE id=? AND status='RUNNING' AND lease_owner=? AND attempt=?` (fenced).
- A cancelled run's `RUNNING` step rejects renewal (cancel set it to `CANCELLED`).
- Stale detection feeds §9.1.

### 9.3 Timeout vs interruption (REQ-E5.3)

- **Timeout** (while process alive): `outcome="failed"`, `category="timeout"` → `maybe_retry` (`RUNNING → READY`/`→ WAITING_APPROVAL`) or terminal `RUNNING → FAILED`. Never `INTERRUPTED`. For an approvable step, the consumed approval stays consumed (not restored).
- **Process death** (no completion, process restarted): lease expires; recovery → `RUNNING → INTERRUPTED`. Only this path reaches `INTERRUPTED`.

---

## 10. SQLite-Safe Transactional Patterns

1. **`BEGIN IMMEDIATE`** for every write transaction (run creation, dispatch+consume, persist, cancel, recovery, settle). Acquires the write lock up-front.
2. **`busy_timeout`** (e.g. 5000 ms) so writers wait briefly for the lock.
3. **CAS** `UPDATE ... WHERE id=? AND status=?` (and `lease_owner`/`attempt` for post-dispatch) with `rowcount` checks; `rowcount != expected` → conflict/no-mutation, raise (`StepDispatchConflict`, `INVALID_STEP_TRANSITION`, `APPROVAL_NOT_CONSUMABLE`) or discard stale (`step_stale_completion_discarded`).
4. **Dispatch+consume atomicity (F14):** the lease CAS and the approval consume execute in **one `BEGIN IMMEDIATE` transaction**; a failed consume rolls back both (step unexecuted). The executor call is OUTSIDE this transaction.
5. **Fencing (REQ-E4.11):** post-dispatch transitions fence on `lease_owner` + `attempt`.
6. **Short transactions:** the executor call is NEVER inside a DB transaction. Dispatch: `BEGIN IMMEDIATE` → lease CAS (+ consume CAS if approvable) → commit → run executor (no DB lock) → `BEGIN IMMEDIATE` → fenced persist CAS + audit → commit.
7. **Retry on `SQLITE_BUSY`:** bounded retries with backoff (rare with concurrency=1).
8. **Write serialisation is a database guarantee** (SQLite file lock via `BEGIN IMMEDIATE`), not an application/pool claim.
9. **Timestamps:** `datetime.now(UTC)` exclusively; `UTCDateTime`.
10. **Audit is append-only:** one `WorkflowAuditLog` row per transition, in the same transaction as the state change.

---

## 11. Mock Executor

`MockExecutor` (`src/devflow_api/executors/mock.py`) is the only registered executor in Wave 1.

```python
class MockExecutor:
    identifier = ExecutorIdentifier(id="mock", version=1)
    capabilities = [Capability(operation="noop")]
    input_schema = BaseModel
    output_schema = MockOutput  # {echo: dict, marker: str}
    timeout_policy = TimeoutPolicy(default_s=30, max_s=300)

    async def execute(self, context: ExecutionContext) -> StepExecutionResult:
        if context.cancellation.cancelled:
            return StepExecutionResult(outcome="cancelled", error=StructuredError(code="cancelled", category="cancelled", message="", retryable=False), metadata=...)
        opts = context.tool_selection.options or {}
        await asyncio.sleep(opts.get("sleep_s", 0))
        mode = opts.get("outcome", "succeeded")
        if mode == "failed":
            return StepExecutionResult(outcome="failed", error=StructuredError(code=opts.get("code","mock_fail"), category="executor", message="", retryable=True), metadata=...)
        if mode == "indeterminate":
            return StepExecutionResult(outcome="indeterminate", error=StructuredError(code="mock_indeterminate", category="executor", message="", retryable=False), metadata=...)
        output = {"echo": context.inputs, "marker": "mock-v1"}
        return StepExecutionResult(outcome="succeeded", output=output, artifact_refs=[ArtifactRef(name="mock-artifact", digest=canonical_hash({"mock": True}))], metadata=ExecutionMetadata(executor_id="mock", executor_version=1, ...))
```

- No fs/network/subprocess; `working_directory=None`; `env_allowlist={}`.
- Deterministic: identical inputs → identical `output_json`/`artifact_refs` (except `mode`-driven failure/indeterminate).
- Test modes via `tool_selection.options`: `outcome` ∈ {`succeeded`,`failed`,`indeterminate`,`cancelled`}, `sleep_s`, `fail_after_attempt`.
- `safe=true`, `idempotent=true` (retries exercisable).

---

## 12. Security & Threat Model

### 12.1 Trust boundary

- **Local single-user.** `actor_id` = `Settings.LOCAL_OPERATOR_ID` (server-side, never request body). No multi-user authentication; none claimed.
- **Loopback only.** All services bind `127.0.0.1`.

### 12.2 Threat model (Stride-style, scoped to Wave 1)

| Threat | Mitigation |
|---|---|
| **Spoofing** | `actor_id` from `Settings.LOCAL_OPERATOR_ID`, never request body; loopback-only. |
| **Tampering** | Fenced CAS with `rowcount` checks; audit append-only; SQLite local. |
| **Repudiation** | Every transition/approval event audited with actor + timestamp + digest + attempt + lease_owner. |
| **Information disclosure** | `RedactSecretsProcessor`; digests not raw secrets; `payload_json` redaction. |
| **Denial of service** | Per-step `timeout_s`; `RUN_CONCURRENCY=1`; lease TTL; mock executor in-process/bounded. |
| **Elevation of privilege** | Executors cannot access repositories/services/models; no subprocess/shell; working-dir allowlist; explicit env. **Explicitly NOT OS-sandboxed** — §12.4. |
| **Replay** (reuse an approval) | Single-use `consume_atomic` CAS; digest + operation + step + actor binding; pending TTL; approved validity. |
| **Execution-before-authorization** (F14) | Approval consumed atomically with lease acquisition BEFORE the executor is invoked; failed consume rolls back (unexecuted); executor runs only after commit. |
| **Stale authorization / no-restore** (F14) | `consumed_at` never set back to `NULL`; cancellation/failure/timeout/indeterminate never restore; protected-op retries re-gate for fresh authorization. |
| **Stale completion** | Lease/attempt fencing (REQ-E4.11); stale results discarded; cannot re-consume. |
| **Cancellation race** | Cancel → step `CANCELLED`; in-flight persist CAS fences on `status='RUNNING'` and fails; approval not restored. |
| **Blocked steps never settle (F15)** | Failure-stop policy settles all remaining non-terminal non-running steps to `FAILED` (`skipped`); run reaches `failed` deterministically. |

### 12.3 What is and is not isolated (honest statement)

**Isolated (application-level):** API (loopback); working dirs (application allowlist; mock none); env (explicit allowlist; no parent inheritance — contractual); credentials (never in logs; digests not raw); approvals (digest-bound, single-use, validity-bounded, consumed-before-execute, replay-/stale-/no-restore-protected).

**NOT isolated (must not be claimed):** filesystem (no OS jail); process (no OS isolation; in-process in Wave 1); network (no egress filtering); resources (no OS limits beyond asyncio timeouts).

### 12.4 Prerequisites before real coding-agent / shell execution (REQ-E6.8)

Before any real coding-agent (Codex/Kiro) or shell execution is enabled in a later wave, the following MUST exist (out of scope for Wave 1): (1) OS-level process isolation; (2) OS-enforced filesystem allowlist; (3) network egress control (default deny); (4) resource limits; (5) credential vault; (6) executor capability review gate. Wave 1 gates these behind an explicit future wave.

### 12.5 Audit integrity posture (REQ-E6.7)

`WorkflowAuditLog` is append-only (Phase 1). Not cryptographically signed/tamper-evident; a future wave may add chained hashes. No false claim of cryptographic integrity.

---

## 13. API Contracts

Base path `/api/v1`; JSON; ISO 8601 UTC; Phase 1 error shape `{"error": "...", "code": "..."}`.

### 13.1 Endpoints (new + extended)

| Method | Path | Status | Notes |
|---|---|---|---|
| POST | `/workflows/{id}/runs` | 201 / 422 | Extended (REQ-E7.1): valid template → run + steps; non-empty invalid → 422; `NULL`/empty → legacy run (201, zero steps). |
| GET | `/workflow-runs/{run_id}` | 200 / 404 | Extended (REQ-E7.4): adds `steps`; preserves `approvals`/`pending_approvals`. |
| POST | `/workflow-runs/{run_id}/start` | 200 / 409 | NEW (REQ-E7.2): `start_or_resume`. 409 `RUN_NOT_STARTABLE` for terminal runs. |
| POST | `/workflow-runs/{run_id}/cancel` | 200 | NEW (REQ-E7.3): idempotent cancel. |
| GET | `/workflow-runs/{run_id}/steps` | 200 / 404 | NEW: ordered step list. |
| GET | `/workflow-runs/{run_id}/steps/{step_id}` | 200 / 404 | NEW (REQ-E7.4): step detail. |
| GET | `/workflow-runs/{run_id}/audit` | 200 | NEW (REQ-E7.5): ordered audit; `limit`/`offset`. |
| GET | `/approvals?status=pending` | 200 | Phase 1 (preserved). |
| GET | `/approvals/{id}` | 200 / 404 | Phase 1 (preserved). |
| POST | `/approvals/{id}/resolve` | 200 / 409 | Phase 1 (extended, REQ-E7.6): 409 `APPROVAL_ALREADY_RESOLVED` / `APPROVAL_EXPIRED`. Binding/validity verified at pre-execution consume. |

No `/merge` endpoint (Phase 1 exclusion preserved). No endpoint performs external writes in Wave 1.

### 13.2 Schemas (new)

```python
# src/devflow_api/schemas/workflow.py
class StepRunResponse(BaseModel):
    id: int; step_key: str; step_version: int; executor_id: str; status: str; attempt: int
    result_status: str | None; tool_selection: ToolSelection | None
    ready_at: datetime | None; started_at: datetime | None; completed_at: datetime | None
    output: dict[str, Any] | None; artifact_refs: list[ArtifactRef] = []; error: StructuredError | None

class WorkflowRunResponse(BaseModel):   # extends Phase 1
    # ... Phase 1 fields
    steps: list[StepRunResponse] = []
    approvals: list[ApprovalResponse] = []          # Phase 1 W1.5.4
    pending_approvals: list[ApprovalResponse] = []  # Phase 1 W1.5.4

class ResolveApprovalRequest(BaseModel):
    decision: str; resolved_by: str | None = None
```

### 13.3 New audit event types (additive)

`run_created`, `run_status_changed` (Phase 1), `step_created`, `step_ready`, `step_dispatched`, `step_succeeded`, `step_failed` (incl. `skipped`/`run_failure_stop`), `step_retry_scheduled`, `step_interrupted`, `step_recovery_decided`, `step_stale_completion_discarded`, `step_transition_rejected`, `approval_requested` (Phase 1, extended), `approval_resolved` (Phase 1), `approval_expired`, `approval_not_consumable`, `run_cancelled`.

---

## 14. Streamlit Minimal Contracts (REQ-E7.7)

`6_Workflows.py` extended: run detail + ordered step list; "Start/Resume" → `POST .../start`; "Cancel" → `POST .../cancel`; approval affordance for `WAITING_APPROVAL` steps → `POST /approvals/{id}/resolve`; "Backend unavailable" notice; no fabricated data; `AppTest` with mocked `APIClient`; `mypy --strict` passes. Full dashboard deferred to Wave 5.

---

## 15. Test Matrix (reference)

See `requirements.md` REQ-E8.1 for the authoritative named-test set, including the v2.1.1 additions: pre-execution consumption (`test_protected_executor_not_invoked_until_approval_consumed`, `test_consume_and_lease_acquired_in_one_transaction`, `test_failed_consume_leaves_step_unexecuted_and_re_gates`, the seven `test_consume_validation_rejects_*`, `test_concurrent_consume_only_one_dispatch_proceeds`, the four `test_consumed_approval_not_restored_on_*`), failure-stop settle (`test_failed_linear_workflow_settles_blocked_steps`, `test_failed_diamond_dependency_settles_blocked_step`, `test_independent_in_flight_step_finishes_before_run_failed`, `test_no_new_dispatch_after_failure_stop`, `test_waiting_approval_step_settled_and_approval_expired_on_run_failure`, `test_run_reaches_failed_only_after_all_steps_settled`, `test_settle_uses_skipped_category_no_ninth_status`), retry-suppression under failure-stop, approvable retry re-gate, and no-restore-on-cancel/timeout/indeterminate. REQ-E8.2/E8.3 for quality gates and migration round-trip.

---

## 16. Architecture Decision Records (proposed)

- **ADR-0005 — Step-level eight-state lifecycle** (rich lifecycle on `workflow_step_runs`; run keeps Phase 1 six states).
- **ADR-0006 — Approval consumption via `consumed_at` and validity via `authorization_expires_at`** (no `consumed`/validity status; Phase 1 CHECK unchanged).
- **ADR-0007 — Orchestrator-owns-state executor interface; contract types in `schemas/`**.
- **ADR-0008 — Honest isolation boundary** (application-level only; OS-level prerequisites listed).
- **ADR-0009 — Immutable template snapshot per run**.
- **ADR-0010 — Lease/attempt fencing; retries transient `RUNNING→READY`/`→WAITING_APPROVAL`, never via `FAILED`**.
- **ADR-0011 — Pre-execution approval consumption (F14)** (consume atomically with lease acquisition, before the executor runs; no restore; fresh authorization for protected-op retries).
- **ADR-0012 — Deterministic failure-stop settle policy (F15)** (settle all remaining non-terminal non-running steps to `FAILED` `skipped`; run → `failed` only after settle; no ninth status).

(Existing ADRs 0001–0004 unchanged.)

---

## 17. Backward Compatibility

- `workflow_runs.status` CHECK unchanged (six values); snapshot columns additive (nullable).
- `approval_requests.status` CHECK unchanged (four values; no `consumed`/validity status); new columns nullable; `expires_at`/`authorization_expires_at` NULL ⇒ no expiry (Phase 1 preserved).
- `WorkflowAuditLog` unchanged; new event types additive.
- Phase 1 endpoints preserved; new endpoints added; `WorkflowRunResponse.steps` default `[]`; `approvals`/`pending_approvals` identical (W1.5.4a–c).
- Phase 1 services preserved: `WorkflowService.create_run`/`transition_run`, `ApprovalService.request_approval`/`resolve` signatures unchanged; `request_bound_approval` (new) and `resolve` (additive) extend without breaking Phase 1 callers.
- Legacy `NULL`/empty-template runs preserved (201 pending, zero steps); Phase 1 tests pass unmodified.
- Migrations 001–007 unchanged; migration 008 chains from 007 and is reversible.

---

## 18. Open Issues & Decisions for ChatGPT Approval

Resolved defaults (applied): pending TTL 3600s; approved authorization validity 3600s (separate column); concurrency 1; lease 60s; heartbeat 15s; recovery auto-reconcile + explicit start; executor guards mandatory; immutable template snapshot per run; pre-execution approval consumption (F14); deterministic failure-stop settle (F15).

Remaining (implementation-time):
1. **Migration revision `008`** is derived from the verified head `007`; the implementer MUST re-confirm the head immediately before authoring and renumber if Phase 1 advanced.
2. **`attempt` representation:** `attempt` = executions attempted (0-based, incremented on dispatch); `max_attempts` = total executions incl. the first. Pinned by named tests.
3. **Consumed approval + discarded stale result edge:** if a protected dispatch consumes its approval, executes an external side effect, but its completion is later discarded as stale (superseded by a re-dispatch after process death), the approval is spent and the external side effect is indeterminate. Wave 1 (mock, no external writes) is unaffected; later waves with real external writes must treat this as the indeterminate-external-write case (fresh approval + reconciliation), documented as a remaining risk.
4. **Settle target state choice:** blocked steps are settled to `FAILED` (`skipped`) rather than `CANCELLED` (cancel is reserved for user cancel/approval-reject). Confirm this is preferred.
5. **Approved-authorization re-gating UX:** an approved authorization that expires while a step waits forces a fresh human decision. Confirm preferred over auto-failing.

These are documented, not silently resolved.
