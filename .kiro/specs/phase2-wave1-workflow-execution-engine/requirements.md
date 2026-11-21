# DevFlow AI — Phase 2 Wave 1: Workflow Execution Engine Requirements

**Version:** 2.1.4
**Status:** Final edge-case corrections — awaiting ChatGPT review
**Scope:** Phase 2 Wave 1 — Workflow Execution Engine
**Branch:** `feature/phase2-wave1-workflow-execution-engine`
**Base commit:** `2d077ecb9f96ff767160e6c1564b2c6f8d4e0a9e` (Phase 1 v1.5.9, PR #13 merged)
**Last Updated:** 2026-11-18
**Supersedes:** v2.1.3. Extends `.kiro/specs/phase1-functional-orchestration/` (v1.5.9); does not modify it.

**Version history:**
- v2.0.0–v2.1.1 — See `docs/reports/phase2-wave1-spec-resolution.md`.
- v2.1.2 — 2026-11-18 — Codex blocker resolution (F16, F17). See `docs/reports/phase2-wave1-codex-blocker-resolution.md`.
- v2.1.3 — 2026-11-18 — Final spec corrections (F18, F19). See `docs/reports/phase2-wave1-final-spec-corrections.md`.
- v2.1.4 — 2026-11-18 — Final edge-case corrections (CA, CB). **(CA — `expire_and_re_gate` stale-pending reuse):** the pre-check in step (1) MUST verify `expires_at > now` before returning an existing pending approval; a pending approval with `expires_at <= now` is treated as expired and must be replaced, not reused; `IntegrityError` catch narrowed to `UniqueConstraintViolation` only — other `IntegrityError` subclasses re-raise unchanged; SQLAlchemy transaction rollback and session-state semantics after `IntegrityError` explicitly defined (rollback required before any further DB work). **(CB — `waiting_approval → failed` direct-transition gap):** `derive_and_persist_run_status` corrected: when `current_run_status == 'waiting_approval'` and all steps are terminal under failure-stop (rule 2 fires), the algorithm must execute an **atomic two-step** `waiting_approval → running → failed` inside the same `BEGIN IMMEDIATE` transaction (two consecutive `transition_status` calls without an intervening commit) rather than raising `InvalidStatusTransitionError`; this closes the race window where the last `WAITING_APPROVAL` step is settled and all steps become terminal before the run has transitioned to `running`; `is_failure_stop` is derived from persisted step state (not a caller flag) to prevent concurrent approval resolution from bypassing failure-stop. 9 new named tests added.

---

## Overview

Phase 2 builds the end-to-end DevFlow AI workflow: a user describes a feature, DevFlow creates a workflow run, an AI provider generates specifications, Kiro verifies them through guided handoff, the AI reviews the Kiro report, the user approves, Codex CLI implements, the AI reviews the implementation, the user approves PR creation, the GitHub adapter creates and monitors the PR, and the user separately approves merging. Every operation is auditable. PR merging is never automatic.

**Wave 1 scope — Workflow Execution Engine only.** Wave 1 delivers the core engine that turns a workflow definition into durable, recoverable, approval-gated step executions. Wave 1 deliberately ships **no real coding-agent execution** and **no Kiro, Codex, OpenAI, Ollama, or GitHub integration**; a single safe mock/no-op executor validates the engine end-to-end. The six-wave Phase 2 roadmap is in `docs/planning/phase2-roadmap.md`.

Wave 1 reuses the Phase 1 persistence and authorization foundation without duplicating it:

| Phase 1 model (reused) | File | Wave 1 use |
|---|---|---|
| `Workflow` | `src/devflow_api/models/workflow.py` | Definition container; `definition_json` holds the validated `WorkflowTemplate` (ordered steps, dependencies, I/O, tool selection, versioning). No column added to `workflows`. |
| `WorkflowRun` | `src/devflow_api/models/workflow.py` | Run instance; Phase 1 six-state run status **unchanged** and derived from step states for backward compatibility. Extended additively with the immutable template snapshot columns (REQ-E1.8). |
| `ApprovalRequest` | `src/devflow_api/models/approval.py` | Human approval gate, **extended additively** with operation + parameter-digest + actor + step-run binding + single-use `consumed_at` + pending `expires_at` + approved `authorization_expires_at`. The Phase 1 `status` CHECK constraint is **not changed**. |
| `WorkflowAuditLog` | `src/devflow_api/models/audit.py` | Append-only audit; reused unchanged for every execution transition. |

Wave 1 adds **one new table** (`workflow_step_runs`), **extends** `approval_requests` additively, and **extends** `workflow_runs` additively (template snapshot), via migration 008 (planned; not authored in this specification task).

**Phase 1 service compatibility (reconciled against actual code):**
- `WorkflowService.WORKFLOW_TRANSITIONS = {"pending": {"running"}, "running": {"completed", "failed", "cancelled"}}`. `waiting_approval` is **not** in this map; Phase 1 reaches `waiting_approval` only through `ApprovalService.request_approval`/`resolve` via the lower-level `WorkflowRunRepository.transition_status`. The orchestrator follows the same split (REQ-E2.9).
- `ApprovalService.request_approval(run_id, step_name, action_description, payload)` and `resolve(approval_id, decision, resolved_by)` are preserved; Wave 1 adds `request_bound_approval(...)` and extends `resolve` additively to also transition the gated step when the approval has a `workflow_step_run_id` (REQ-E2.5, E3.1).
- `WorkflowService.create_run(workflow_id)` (Phase 1) is preserved unchanged; Wave 1's atomic run+step creation is a new orchestrator path that does **not** call it (to keep step creation atomic), and reuses the repositories and `AuditLogRepository` (REQ-E2.1).

**Phase 1 boundary carried into Wave 1:** the application remains local-first, single-user, loopback-only, free-core. The trust boundary is the local operator (`Settings.LOCAL_OPERATOR_ID`, default `'local_operator'`); Wave 1 does not introduce multi-user authentication.

### Global Constraints (apply to all requirements below)

- The application MUST remain local-first with free core functionality.
- All development services MUST bind exclusively to `127.0.0.1` on the host interface.
- The codebase MUST NOT contain Android, React, TypeScript, or Vite artefacts.
- No hardcoded credentials or secrets MUST appear in source control at any time.
- Architecture boundaries from Phase 0/1 (UI → API → domain services → repositories → SQLite; services → adapters) MUST be preserved. Schema types live under `src/devflow_api/schemas/`; executors import schemas, never the reverse (REQ-E2.8).
- Phase 0 and Phase 1 specification files MUST NOT be modified by Wave 1 implementation. Phase 1 production code is extended additively; Phase 1 tests continue to pass (legacy `NULL`-template runs preserved; `WorkflowService.create_run` unchanged; CHECK constraints unchanged).
- `datetime.utcnow()` MUST NOT appear in any Phase 2 code; use `datetime.now(UTC)` exclusively.
- All new timestamp columns MUST use the `UTCDateTime` TypeDecorator from `src/devflow_api/models/base.py`.
- Every consequential or external action MUST require an explicit, digest-bound, single-use, validity-bounded approval, **consumed atomically before the protected executor is invoked** (REQ-E3.3, E3.7). Approval presence is authorization; credential presence is not.
- No unrestricted shell/command execution MUST be introduced in Wave 1. Only the mock/no-op executor (REQ-E5.x) ships. Executor security guards are **mandatory** (REQ-E5.4, E6.x).
- Environment-variable scoping and working-directory allowlists are Python-level controls; they are NOT OS-level sandboxing and MUST NOT be described as such (REQ-E6.x).

### Resolved Defaults (applied throughout)

| Setting | Default | Where |
|---|---|---|
| `Settings.APPROVAL_TTL_SECONDS` (pending approval TTL) | 3600 | REQ-E3.4 |
| `Settings.APPROVAL_AUTHORIZATION_VALIDITY_SECONDS` (approved authorization validity) | 3600 | REQ-E3.6 |
| `Settings.RUN_CONCURRENCY` (per-run step dispatch concurrency) | 1 | REQ-E2.2 |
| `Settings.STEP_LEASE_TTL_SECONDS` | 60 | REQ-E4.6 |
| `Settings.STEP_HEARTBEAT_INTERVAL_SECONDS` | 15 | REQ-E4.10 |
| Recovery | automatic state reconciliation; explicit user start for resumed execution | REQ-E4.3 |
| Executor security guards | mandatory (hard gate, not advisory) | REQ-E5.4 |
| Template per run | immutable full execution snapshot | REQ-E1.8 |

### Out of Scope for Wave 1

| Capability | Deferral reason |
|---|---|
| Real coding-agent execution (Codex CLI, Kiro, Ollama, OpenAI) | Wave 1 ships the engine + a mock executor only; integrations are Waves 2–3 |
| Unrestricted shell / arbitrary command execution | A separate safety concern; deferred past Phase 2 until OS-level isolation exists (REQ-E6.8) |
| Autonomous PR creation / merge | PR creation is Wave 4; merge is never automatic |
| Full Streamlit workflow dashboard | Wave 5; Wave 1 adds only minimal run/step observation (REQ-E7.x) |
| Multi-user access control | Local single-user tool; explicit trust boundary (REQ-E6.x) |
| OS-level process sandboxing (seccomp, namespaces, job objects) | Required before real coding-agent execution; documented as a prerequisite in REQ-E6.8, not delivered in Wave 1 |
| Changing the Phase 1 `workflow_runs.status` enum | Backward compatibility; the eight-state lifecycle lives on step runs (REQ-E4.1) |
| Renaming or restructuring Phase 1 models | Wave 1 extends additively only |

---

## Area E1 — Workflow Definition and Execution Model

**Scope note (maps to task W1.1):** the durable structure that turns a `Workflow` into ordered, executable steps with dependencies, inputs/outputs, tool selection, versioning, and durable execution state.

### REQ-E1.1
WHEN a workflow is defined for Phase 2 execution,
THEN the `Workflow.definition_json` column MUST hold a validated `WorkflowTemplate` (Pydantic v2 model) containing `template_version: int`, `steps: list[StepDefinition]` (ordered, non-empty, unique `key`), and `defaults: TemplateDefaults` (default executor id, default timeout seconds, default retry policy). The template MUST be validated when a run is created from it (REQ-E2.1); invalid templates MUST be rejected with HTTP 422 and `code = "INVALID_WORKFLOW_TEMPLATE"`. `Workflow` create/update MUST NOT validate `definition_json` (Phase 1 behavior: stored as-is).

**Acceptance Criteria:**
E1.1.1 — `WorkflowTemplate` is a Pydantic v2 model in `src/devflow_api/schemas/workflow_template.py` and is importable.
E1.1.2 — `StepDefinition` carries: `key: str`, `version: int`, `title: str`, `description: str`, `depends_on: list[str]` (step keys), `executor_id: str`, `tool_selection: ToolSelection`, `inputs: dict[str, Any] | None`, `expected_outputs: list[str] | None`, `timeout_s: int`, `retry_policy: RetryPolicy`, `approvable: bool`, `protected_operation: str | None`.
E1.1.3 — A template with a duplicate step `key`, an empty `steps` list, a `depends_on` referencing a non-existent key, or a cycle in `depends_on` MUST be rejected with HTTP 422 `INVALID_WORKFLOW_TEMPLATE` at run creation.
E1.1.4 — `mypy --strict src/devflow_api/schemas/workflow_template.py` exits with code 0.
E1.1.5 — The existing `Workflow` SQLAlchemy model is NOT modified; no column is added to `workflows`. `definition_json` remains `deferred=True, deferred_raiseload=True`.

### REQ-E1.2
WHEN a workflow run is created for execution,
THEN a `WorkflowRun` MUST be persisted with Phase 1 status `pending`, and one `WorkflowStepRun` row MUST be created per `StepDefinition` in `WorkflowTemplate.steps`, each in step status `PENDING`, with `step_key`, `step_version`, `executor_id`, `tool_selection_json`, `depends_on_json`, `workflow_run_id` (FK CASCADE), `attempt = 0` (executions attempted so far; incremented on dispatch, REQ-E4.6), and `created_at`/`updated_at` set to `datetime.now(UTC)`.

**Acceptance Criteria:**
E1.2.1 — `POST /api/v1/workflows/{id}/runs` (Phase 1 endpoint, extended) creates the run AND all step rows in one transaction; partial creation MUST roll back entirely.
E1.2.2 — Step row count equals `len(WorkflowTemplate.steps)`; order matches template order; `step_key` values are unique within the run.
E1.2.3 — `WorkflowStepRun` model is importable from `devflow_api.models.workflow_step_run` (new file) and `mypy --strict` passes for it.
E1.2.4 — Re-running run creation against an unchanged template produces a deterministic set of step keys/versions (enables reproducibility tests).

### REQ-E1.3
WHEN steps declare dependencies,
THEN the orchestrator MUST compute step readiness from `depends_on`: a step is eligible to become `READY` only when every dependency's `WorkflowStepRun.status == "SUCCEEDED"`. A step whose dependency `FAILED` or `CANCELLED` MUST NOT become `READY`; its disposition is determined by the failure-stop policy (REQ-E2.6, E4.12).

**Acceptance Criteria:**
E1.3.1 — A two-step template (`a → b`) where `a` fails leaves `b` settled to `FAILED` (`skipped`) and the run reaches `failed` (REQ-E4.12).
E1.3.2 — Diamond dependency (`a → {b,c} → d`) gates `d` until both `b` and `c` are `SUCCEEDED`; if `b` fails, `d` is settled `FAILED` (`skipped`).
E1.3.3 — A dependency declared in `depends_on` that has no matching step row fails template validation (REQ-E1.1.3), not at run time.

### REQ-E1.4
WHEN step inputs and outputs are managed,
THEN each `WorkflowStepRun` MUST persist `input_json` when it becomes `READY` (resolved inputs) and `output_json` + `artifact_refs_json` at success; step outputs MUST be referenceable as inputs to downstream steps by `"$steps.<key>.<output_name>"` in the template `inputs` mapping, resolved by the orchestrator at the `PENDING → READY` transition (no implicit global namespace).

**Acceptance Criteria:**
E1.4.1 — A downstream step whose `inputs` references `"$steps.a.spec_doc"` resolves to the `output_json["spec_doc"]` of step `a` only when `a` is `SUCCEEDED`; otherwise the downstream step cannot become `READY`.
E1.4.2 — A missing or malformed output reference MUST fail the step at the `PENDING → READY` transition with `code = "INPUT_RESOLUTION_FAILED"`, transitioning `PENDING → FAILED` (REQ-E4.2). It never reaches `READY`.
E1.4.3 — `output_json` and `artifact_refs_json` are `None` until the step `SUCCEEDED`; they are never written for non-terminal or `FAILED` steps except `error_json` (REQ-E4.5).

### REQ-E1.5
WHEN tool selection and versioning are recorded,
THEN every `WorkflowStepRun` MUST persist `tool_selection_json` (the `ToolSelection` snapshot from the run's immutable template snapshot, REQ-E1.8) and `step_version` (the `StepDefinition.version`) at creation, so an audit reader can reconstruct which tool/version executed even if the live `Workflow.definition_json` later changes. `WorkflowTemplate.template_version` and `WorkflowStepRun.step_version` form the durable versioning record.

**Acceptance Criteria:**
E1.5.1 — Editing the `Workflow.definition_json` after a run is created does NOT alter any existing `WorkflowStepRun.tool_selection_json` or `step_version`, and does NOT alter the run's template snapshot (REQ-E1.8).
E1.5.2 — `GET .../steps/{step_id}` returns `step_version` and `tool_selection` (read-only projection).
E1.5.3 — A run created from `template_version=2` records `template_version=2` on the run; bumping the live template does not retroactively change existing runs.

### REQ-E1.6
WHEN durable execution state and timestamps are required,
THEN `WorkflowStepRun` MUST carry UTC timestamps `ready_at`, `dispatched_at`, `started_at`, `completed_at` (all nullable, `UTCDateTime`), plus `lease_owner: str | None`, `lease_expires_at: UTCDateTime | None`, `attempt: int` (default 0; executions attempted so far), `status` (eight-state, REQ-E4.1), `result_status: str | None` (executor outcome), and `error_json: str | None`. All state MUST persist to SQLite and survive process restart.

**Acceptance Criteria:**
E1.6.1 — All timestamp columns use `UTCDateTime`; no `datetime.utcnow()` anywhere in the new code.
E1.6.2 — After a simulated restart (new process, same DB), step rows and their timestamps are unchanged.
E1.6.3 — `mypy --strict` passes for the new model.

### REQ-E1.7
WHEN state transitions occur,
THEN the orchestrator MUST validate each transition against the allowed-transition map (REQ-E4.2) using a compare-and-set `UPDATE ... WHERE id = ? AND status = <expected_from>` (plus `lease_owner`/`attempt` fencing for post-dispatch transitions, REQ-E4.11) inside a `BEGIN IMMEDIATE` transaction, and MUST reject invalid transitions with `code = "INVALID_STEP_TRANSITION"` and no row mutation.

**Acceptance Criteria:**
E1.7.1 — A `PENDING → RUNNING` transition (skipping `READY`) is rejected; the row stays `PENDING` and an audit entry `step_transition_rejected` is written.
E1.7.2 — A valid `READY → RUNNING` CAS update sets `lease_owner`, `lease_expires_at`, `dispatched_at`, `started_at`, and increments `attempt` atomically (and, for an approvable step, consumes its approval in the same transaction, REQ-E3.7).
E1.7.3 — A concurrent `READY → RUNNING` attempt by two workers: exactly one CAS update affects one row (`rowcount == 1`); the other gets `rowcount == 0` and raises `StepDispatchConflict` (duplicate-dispatch prevention, REQ-E4.6).

### REQ-E1.8
WHEN a run is created from a valid `WorkflowTemplate`,
THEN the orchestrator MUST persist an **immutable full execution snapshot** of the template on the `WorkflowRun`: `template_snapshot_json` (the full validated `WorkflowTemplate` JSON), `template_version` (INTEGER), and `template_hash` (TEXT, SHA-256 of the canonical template). All three are nullable for Phase 1 / legacy runs. The orchestrator MUST execute from the snapshot, NEVER from the live `Workflow.definition_json`. Editing the live workflow after run creation MUST NOT affect any existing run or its steps. A run created from a `NULL`/empty `definition_json` is a **legacy run** (pending, zero step rows, no snapshot) and is preserved for backward compatibility with Phase 1 (REQ-E2.1).

**Acceptance Criteria:**
E1.8.1 — Migration 008 adds `template_snapshot_json`, `template_version`, `template_hash` (all nullable) to `workflow_runs`; Phase 1 rows have `NULL` and remain valid.
E1.8.2 — After `create_run` from a valid template, mutating `Workflow.definition_json` does not change `workflow_runs.template_snapshot_json`/`template_hash`/`template_version` or any step row.
E1.8.3 — `template_hash` equals `canonical_hash(template_snapshot_json)` and is recorded on the run; `run_created` audit carries `template_version` and `template_hash`.
E1.8.4 — A run created from `NULL`/empty `definition_json` has zero step rows and `template_snapshot_json IS NULL`; it remains `pending` until started, then vacuously completes (REQ-E4.4 derivation: zero steps ⇒ all-terminal ⇒ `completed`).

---

## Area E2 — Workflow Orchestration Service

**Scope note (maps to task W1.2):** a Python orchestration service that drives runs from creation through terminal state, pausing for human approval and stopping safely on failure.

### REQ-E2.1
WHEN a workflow run is created,
THEN `WorkflowOrchestrator.create_run(workflow_id, actor)` MUST: load the `Workflow`; if `definition_json` is `NULL`/empty, create a **legacy run** (status `pending`, no step rows, no snapshot, audit `run_created`) and return 201; otherwise parse and validate `definition_json` as `WorkflowTemplate` (422 `INVALID_WORKFLOW_TEMPLATE` on failure), then in one atomic transaction persist the `WorkflowRun` (status `pending`) with the immutable template snapshot and all `WorkflowStepRun` rows, and write `run_created` with `actor` from `Settings.LOCAL_OPERATOR_ID`. `WorkflowService.create_run` (Phase 1) is preserved unchanged for Phase 1 callers; the orchestrator does NOT call it.

**Acceptance Criteria:**
E2.1.1 — `create_run` is atomic (all-or-nothing); a failure mid-creation leaves no partial run/step rows.
E2.1.2 — `run_created` audit entry carries `template_version`, `template_hash`, `step_count`, `actor_id`.
E2.1.3 — `actor_id` equals `Settings.LOCAL_OPERATOR_ID` and is never read from the request body.
E2.1.4 — A workflow with `NULL`/empty `definition_json` produces a legacy run (201, `status="pending"`, zero steps, `template_snapshot_json` `null`) — preserving Phase 1 router behavior and tests.
E2.1.5 — A workflow whose `definition_json` is non-empty but not a valid `WorkflowTemplate` returns 422 `INVALID_WORKFLOW_TEMPLATE`.

### REQ-E2.2
WHEN eligible execution is started or resumed,
THEN `WorkflowOrchestrator.start_or_resume(run_id, actor)` MUST first transition the run `pending → running` if it is `pending` (via Phase 1 `WorkflowService.transition_run`), then repeatedly: (a) for each `PENDING` step whose dependencies are all `SUCCEEDED`, resolve inputs and transition `PENDING → READY`; failed input resolution transitions `PENDING → FAILED` (`INPUT_RESOLUTION_FAILED`); (b) for each `READY` step (bounded by `Settings.RUN_CONCURRENCY`, default 1): if the run is in failure-stop (REQ-E4.12), do NOT dispatch — the settle policy handles it; otherwise, if `approvable`, find a consumable approval; if one exists, **dispatch the protected step by atomically acquiring the lease AND consuming the approval in one transaction, then invoke the executor outside the transaction** (REQ-E3.7); if no consumable approval exists, re-gate `READY → WAITING_APPROVAL` (REQ-E2.4); if not `approvable`, dispatch `READY → RUNNING` after pre-dispatch checks (executor registered, working directory approved, capability match), else transition `READY → FAILED` for the specific pre-dispatch error (`EXECUTOR_NOT_FOUND` / `WORKING_DIRECTORY_NOT_APPROVED` / `CAPABILITY_MISMATCH`); (c) after each `SUCCEEDED` step, re-evaluate dependents. Resuming a terminal run returns 409 `RUN_NOT_STARTABLE`.

**Acceptance Criteria:**
E2.2.1 — A run with two independent root steps dispatches both (subject to the concurrency limit); a run with a linear chain dispatches only the first.
E2.2.2 — A step with `approvable == true` and a consumable approval reaches `RUNNING` only after the approval is consumed in the same transaction as lease acquisition; the protected executor is invoked only after that transaction commits (REQ-E3.7).
E2.2.3 — A step whose `executor_id` is not registered transitions `READY → FAILED` with `code = "EXECUTOR_NOT_FOUND"`; no dispatch is attempted.
E2.2.4 — The run's Phase 1 `status` is derived to `running` while any step is `READY`/`RUNNING`/`INTERRUPTED`, `waiting_approval` while any step is `WAITING_APPROVAL`, and to a terminal Phase 1 state only when ALL steps are terminal (REQ-E4.4). Under failure-stop (REQ-E4.12), no new dispatch occurs and blocked steps are settled.

### REQ-E2.3
WHEN a step succeeds,
THEN the orchestrator MUST persist the executor's `StepExecutionResult` (`output_json`, `artifact_refs_json`, `result_status`) to the step row via a fenced CAS `RUNNING → SUCCEEDED` (fenced on `lease_owner`/`attempt`, REQ-E4.11), set `completed_at`, release the lease, write `step_succeeded`, and re-evaluate dependents for `READY` eligibility. No executor writes to the database directly.

**Acceptance Criteria:**
E2.3.1 — After the terminal step `SUCCEEDED` (and all steps terminal), the run transitions to Phase 1 `completed` and `completed_at` is set on the run.
E2.3.2 — `output_json`/`artifact_refs_json` are persisted only on `SUCCEEDED`; the executor return value is the sole source.
E2.3.3 — An audit `step_succeeded` entry is written with `step_key`, `step_version`, `result_status`, `attempt`.

### REQ-E2.4
WHEN an approval is required (initial gate or re-gate),
THEN the orchestrator MUST transition the gated step `READY → WAITING_APPROVAL`, create an `ApprovalRequest` bound to `workflow_run_id`, `workflow_step_run_id`, `operation` (`protected_operation`), `parameters_hash` (SHA-256 of the canonical artifact/parameters), `actor_id` (`Settings.LOCAL_OPERATOR_ID`), `expires_at` (`requested_at + APPROVAL_TTL_SECONDS`), and write `approval_requested`. The run is derived to `waiting_approval` via `derive_and_persist_run_status` (REQ-E2.10, REQ-E2.9). **Re-gating** occurs when: a step is `READY` and `approvable` but has no consumable approval (none approved, or the approved one has expired (`now >= authorization_expires_at`) or was already consumed), OR the atomic dispatch-and-consume transaction failed authorization (REQ-E3.7 rolls back, leaving the step unexecuted). **Re-gating from `WAITING_APPROVAL`** (after approval expiry detected during reconciliation) is handled by `expire_and_re_gate` (REQ-E3.8): the step stays `WAITING_APPROVAL`, the old approval is atomically expired and a new approval record created in the same `BEGIN IMMEDIATE` transaction; there is NO intermediate `READY` state and no run status change required for this path. In all initial-gate cases the step transitions `READY → WAITING_APPROVAL`. Cancellation and failure-stop prevent re-gating (REQ-E3.8.5, REQ-E3.8.6).

**Acceptance Criteria:**
E2.4.1 — The `ApprovalRequest` carries `operation`, `parameters_hash`, `actor_id`, `workflow_step_run_id`, `expires_at` (all non-null for Phase 2 gates).
E2.4.2 — `approval_requested` audit entry includes `operation`, `parameters_hash`, `step_key`.
E2.4.3 — While a step is `WAITING_APPROVAL`, the orchestrator dispatches no further protected action for that step.
E2.4.4 — A step whose prior approved authorization expired (or was consumed) before dispatch re-gates via `expire_and_re_gate` or the initial-gate path as appropriate.
E2.4.5 — A step whose atomic dispatch-and-consume transaction failed authorization (consume `rowcount == 0`) is left unexecuted (`READY`, lease not acquired) and re-gated `READY → WAITING_APPROVAL` with a fresh approval; the failed consume writes `approval_not_consumable`.
E2.4.6 — Re-gating from `WAITING_APPROVAL` (after expiry detected during reconciliation) is handled without passing through `READY`: the old approval is expired and the new one created atomically, the step stays `WAITING_APPROVAL`, and `approval_re_gated` is audited (REQ-E3.8).

### REQ-E2.5
WHEN an approval is resolved,
THEN `ApprovalService.resolve` (Phase 1, extended) MUST verify the approval is `pending` and NOT past `expires_at`. If `status != 'pending'` (already approved/rejected/expired/consumed), return HTTP 409 `APPROVAL_ALREADY_RESOLVED`. If `status == 'pending'` AND `now >= expires_at` (Phase 2 approval with `expires_at` set), return HTTP 409 `APPROVAL_EXPIRED` — this error code takes precedence over `APPROVAL_ALREADY_RESOLVED` for Phase 2 pending approvals that have expired whether or not the TTL sweep has already run. For Phase 1 approvals (`expires_at IS NULL`), `APPROVAL_ALREADY_RESOLVED` applies as before. On valid approval: set `status` to `approved`/`rejected`, `resolved_at`/`resolved_by`, and for a Phase 2 gate (`workflow_step_run_id` present) on `approved` set `authorization_expires_at` and transition step `WAITING_APPROVAL → READY`; on `rejected` transition step `→ CANCELLED`; re-derive run status via `derive_and_persist_run_status` (REQ-E2.10).

**Acceptance Criteria:**
E2.5.1 — `approved` resumes the step to `READY`; re-derived run status may remain `waiting_approval` if other gates are still pending.
E2.5.2 — Resolving an already-resolved (approved/rejected/expired) approval returns HTTP 409 `APPROVAL_ALREADY_RESOLVED` for Phase 1 approvals and for Phase 2 approvals whose status is not `pending`.
E2.5.3 — Resolving a Phase 2 `pending` approval past `expires_at` returns HTTP 409 `APPROVAL_EXPIRED` **regardless of whether the TTL sweep has already set status to `expired`** — both pre-sweep and post-sweep return this code. (Pre-sweep: `status == 'pending'` AND `now >= expires_at`. Post-sweep: `status == 'expired'` → falls under `APPROVAL_ALREADY_RESOLVED` for Phase 1; for Phase 2 the resolver must check the status and return `APPROVAL_EXPIRED` if `status == 'expired'` and `operation IS NOT NULL`.)
E2.5.4 — On `approved`, `authorization_expires_at` is set; the approval is consumable only before that time (REQ-E3.3, E3.6).
E2.5.5 — After `approved`, `derive_and_persist_run_status` is called; if no other `WAITING_APPROVAL` steps remain, the run transitions to `running`; if other gates remain, the run stays `waiting_approval`.

### REQ-E2.6
WHEN a step fails terminally,
THEN the orchestrator MUST transition the step `RUNNING → FAILED` (fenced CAS; REQ-E4.11), persist `error_json`, set `completed_at`, release the lease, write `step_failed`, and engage the **failure-stop policy** (REQ-E4.12): no new `READY → RUNNING` dispatch; in-flight `RUNNING` steps are allowed to finish; every remaining non-terminal non-running step (`PENDING`/`READY`/`WAITING_APPROVAL`/`INTERRUPTED`) is settled to `FAILED` (`category = "skipped"`, reason `STEP_BLOCKED_BY_RUN_FAILURE`), with pending approvals for settled `WAITING_APPROVAL` steps expired; the run transitions to Phase 1 `failed` only once the stop policy has settled all remaining steps (all steps terminal). **Retry is NOT a `FAILED` transition:** a retryable failure transitions `RUNNING → READY` (non-approvable) or `RUNNING → WAITING_APPROVAL` (approvable, re-gate, fresh authorization; REQ-E4.7) and never passes through `FAILED`. Ambiguous external writes / `indeterminate` outcomes are never retried automatically and transition `RUNNING → FAILED` (REQ-E6.6).

**Acceptance Criteria:**
E2.6.1 — A terminally-failing step (with no other in-flight steps) sets the run `failed` and `error_message` (after settle).
E2.6.2 — Dependents of a `FAILED` step are settled to `FAILED` (`skipped`), never `READY`.
E2.6.3 — `step_failed` audit entry carries `step_key`, `error_code`, `error_category`, `attempt`.

### REQ-E2.7
WHEN a run is cancelled,
THEN `WorkflowOrchestrator.cancel(run_id, actor)` MUST transition every non-terminal step (`PENDING`/`READY`/`RUNNING`/`WAITING_APPROVAL`/`INTERRUPTED`) to `CANCELLED` (fenced CAS where a lease is held), request cooperative cancellation of in-flight executor calls, transition the run to Phase 1 `cancelled` (from any non-terminal run state via `run_repo.transition_status` with the appropriate from-set, REQ-E2.9), and write `run_cancelled` with `actor`. Terminal steps are not mutated. Cancelling an already-terminal run is idempotent (HTTP 200, no mutation).

**Acceptance Criteria:**
E2.7.1 — Cancelling a run with one `RUNNING` and one `PENDING` step sets both to `CANCELLED` and the run to `cancelled`.
E2.7.2 — Already-`SUCCEEDED` steps remain `SUCCEEDED`.
E2.7.3 — `run_cancelled` audit entry includes `actor_id`, `cancelled_step_count`.
E2.7.4 — Cancellation does NOT restore any consumed approval: an approval consumed at dispatch (REQ-E3.7) before the cancel stays consumed (`consumed_at` never set back to `NULL`); the cancelled protected action is not executed (the step goes to `CANCELLED`).

### REQ-E2.8
WHEN the orchestrator interacts with executors,
THEN the interface MUST be a one-way call `Executor.execute(context: ExecutionContext) -> StepExecutionResult` where the executor receives an immutable `ExecutionContext` (no DB handle, no run-state mutation capability) and returns a pure result; the orchestrator owns all persistence, state transitions, leases, and audit. Executors MUST NOT import or call repositories, services, or models. All Pydantic contract types live in `src/devflow_api/schemas/executor.py`; the `Executor` Protocol lives in `src/devflow_api/executors/base.py` and imports those types. No `schemas/*` module MAY import from `devflow_api.executors` (no layering inversion).

**Acceptance Criteria:**
E2.8.1 — The `Executor` Protocol is in `src/devflow_api/executors/base.py`; contract Pydantic models are in `src/devflow_api/schemas/executor.py`; `mypy --strict` passes for both packages.
E2.8.2 — No module under `src/devflow_api/schemas/` imports `devflow_api.executors` (enforced by an import-graph test).
E2.8.3 — No executor module imports `devflow_api.repositories`, `devflow_api.services`, or `devflow_api.models` (enforced by an import-graph/AST test).

### REQ-E2.9
WHEN the orchestrator transitions the run,
THEN it MUST reuse Phase 1 services compatibly. The Phase 1 `WorkflowRunRepository.VALID_FROM` map is:
```
"running":          ("pending", "waiting_approval")
"waiting_approval": ("running",)
"completed":        ("running",)
"failed":           ("running",)
"cancelled":        ("running", "waiting_approval")
```
The orchestrator uses this map only for the initial run start (`pending → running`) and for `start_or_resume`'s first gate (`running → waiting_approval`). **Subsequent gates while the run is already `waiting_approval` do NOT need a run transition**: the step transitions `READY → WAITING_APPROVAL` and `derive_and_persist_run_status` (REQ-E2.10) leaves the run `waiting_approval` (idempotent). When an approval is `approved` and other gates remain, `derive_and_persist_run_status` leaves the run `waiting_approval` (not `running`) — the run only returns to `running` when no step is `WAITING_APPROVAL`. `run_repo.transition_status` for `waiting_approval → running` is called only by `derive_and_persist_run_status` and only when it confirms no remaining `WAITING_APPROVAL` steps. Cancel uses `transition_status` from any non-terminal run state (Phase 1 from-set covers all cases). The orchestrator never introduces run states outside the Phase 1 six-value enum.

**Acceptance Criteria:**
E2.9.1 — Run transitions used by the orchestrator are exactly those Phase 1 already supports; no new run state value is introduced.
E2.9.2 — `pending → running` is issued once at the start of the first `start_or_resume`.
E2.9.3 — When two independent steps simultaneously reach gates, the run stays `waiting_approval`; re-deriving after each approval approval-or-rejection leaves the run correctly `waiting_approval` (gates remain) or `running` (all gates cleared).
E2.9.4 — `derive_and_persist_run_status` reads all step states in one transaction and calls `transition_status` only for the winning target status per the precedence table (REQ-E2.10); it never issues a transition that conflicts with `VALID_FROM`.

### REQ-E2.10
WHEN run status must be derived and persisted after any step state change,
THEN `derive_and_persist_run_status(run_id, session)` (the `is_failure_stop` flag is **derived from persisted step state** inside the transaction, not passed by the caller — CB) MUST, inside a single `BEGIN IMMEDIATE` transaction: (1) read the current run status and all step statuses; (2) compute `is_failure_stop = any step has terminal FAILED with result_status != 'skipped'` from the persisted rows; (3) compute the target run status using the precedence table below; (4) if target ≠ current, apply the transition(s) using the rules described; (5) if already in a terminal state (`completed`, `failed`, `cancelled`), return immediately (no-op). The `is_failure_stop` flag is computed from persisted state, not from caller-supplied context, to prevent concurrent callers (e.g. a concurrent approval resolve) from supplying an incorrect flag. The precedence table (highest wins):

| Priority | Condition | Run status | Notes |
|---|---|---|---|
| 1 | Run is `cancelled` (detected as terminal; no-op) | `cancelled` | (terminal; no transition issued) |
| 2 | `is_failure_stop` AND all steps terminal | `failed` | See two-step transition rule (CB) below |
| 3 | `is_failure_stop` AND any step non-terminal | `running` | `waiting_approval → running` ✓ (VALID_FROM: `"running":("pending","waiting_approval")`) |
| 4 | (NOT `is_failure_stop`) AND any step `WAITING_APPROVAL` | `waiting_approval` | Outside failure-stop only |
| 5 | Any step `READY` or `RUNNING` or `INTERRUPTED` | `running` | Active work |
| 6 | All steps `SUCCEEDED` | `completed` | `running → completed` ✓ |
| 7 | All steps `PENDING` (zero-step or unstarted) | `pending` | No transition needed |

**Two-step transition rule (CB):** when rule 2 fires (`is_failure_stop AND all steps terminal`) and `current_run_status == 'waiting_approval'`, the derivation MUST execute **two consecutive `transition_status` calls inside the same `BEGIN IMMEDIATE` transaction without an intervening commit**: first `waiting_approval → running` (legal: `VALID_FROM["running"] = ("pending","waiting_approval")`), then `running → failed` (legal: `VALID_FROM["failed"] = ("running",)`). Both calls use `commit=False`. The transaction commits once after both. This guarantees that the `waiting_approval → failed` path (which is not a direct legal transition) is always covered atomically. If the first call fails (concurrent winner already moved the run), catch the error, re-read, and proceed accordingly.

**Failure-stop flag derivation (CB):** `is_failure_stop` is computed inside the transaction from `SELECT EXISTS(SELECT 1 FROM workflow_step_runs WHERE workflow_run_id = run_id AND status = 'FAILED' AND (result_status IS NULL OR result_status != 'indeterminate') AND error_json NOT LIKE '%"category": "skipped"%')`. This prevents a concurrent `ApprovalService.resolve` call (which does not know about failure-stop) from passing `is_failure_stop=False` and incorrectly computing `waiting_approval` for a run that is already in failure-stop.

**Illegal transition handling (F18, CB):** if `current_run_status NOT in VALID_FROM[target]` and the two-step rule does not apply, `InvalidStatusTransitionError` MUST be raised. The two-step rule applies only when `target == 'failed'` and `current_run_status == 'waiting_approval'` and `is_failure_stop`. All other illegal transitions are programming errors and must raise.

**Acceptance Criteria:**
E2.10.1 — After one of two independent gated steps is approved (no failure-stop), `derive_and_persist_run_status` leaves the run `waiting_approval` (priority 4 fires).
E2.10.2 — After both gated steps are approved and no other gates remain (no failure-stop), `derive_and_persist_run_status` transitions the run to `running` (priority 5 fires).
E2.10.3 — Calling `derive_and_persist_run_status` on a completed/failed/cancelled run is a no-op (idempotent).
E2.10.4 — When failure-stop is engaged and a `WAITING_APPROVAL` step and a `RUNNING` step both exist: `derive_and_persist_run_status` derives `running` (priority 3; not `waiting_approval`); the run stays `running` until all steps are terminal.
E2.10.5 — When failure-stop is engaged and the `WAITING_APPROVAL` step is settled while a `RUNNING` step remains: derive fires again and stays `running` (priority 3 → 5; `RUNNING` step active).
E2.10.6 — When failure-stop is engaged and **all** steps are terminal and `current_run_status == 'running'`: derive transitions to `failed` (priority 2; `running → failed` legal; one `transition_status` call).
E2.10.7 — When failure-stop is engaged and **all** steps are terminal and `current_run_status == 'waiting_approval'` **(CB edge case)**: derive executes the two-step `waiting_approval → running → failed` atomically in one transaction; the run reaches `failed` and intermediate `running` state is never committed as a separate visible state.
E2.10.8 — An illegal transition that does not match the two-step rule (e.g. `failed → running`) raises `InvalidStatusTransitionError`, not a silent no-op.
E2.10.9 — The derivation transaction uses `BEGIN IMMEDIATE`; two concurrent calls computing the same target: the loser's first `transition_status` CAS fails and raises, which the caller catches, re-reads, and verifies the run is already `failed`.
E2.10.10 — `is_failure_stop` is derived from persisted step state inside the transaction; a concurrent `ApprovalService.resolve` call cannot supply an incorrect flag and cannot bypass failure-stop (CB).

---

## Area E3 — Approval Checkpoints

**Scope note (maps to task W1.3):** durable, digest-bound, single-use, validity-bounded approval gates that do not auto-authorize later or unrelated actions, consumed atomically before the protected executor runs.

### REQ-E3.1
WHEN a Phase 2 approval checkpoint is defined,
THEN the four protected operations MUST be enumerated as `ApprovalOperation` literals: `specification_approval`, `implementation_authorization`, `pr_creation_approval`, `merge_approval`. An `ApprovalRequest` created for a Phase 2 gate MUST set `operation` to one of these, `parameters_hash` to the SHA-256 of the canonical artifact/parameters, `actor_id` to `Settings.LOCAL_OPERATOR_ID`, `workflow_step_run_id` to the gated step, and `expires_at` to `requested_at + APPROVAL_TTL_SECONDS`. Phase 1 approval requests (no `operation`) remain valid and unchanged (backward compatibility).

**Acceptance Criteria:**
E3.1.1 — `operation`, `parameters_hash`, `actor_id`, `workflow_step_run_id`, `consumed_at`, `expires_at`, `authorization_expires_at` columns are added to `approval_requests` by migration 008; all are nullable for backward compatibility with Phase 1 rows.
E3.1.2 — The `approval_requests.status` CHECK constraint remains exactly `('pending','approved','rejected','expired')` — **no `consumed` status is added**; single-use semantics via `consumed_at` (REQ-E3.3) and approved validity via `authorization_expires_at` (REQ-E3.6).
E3.1.3 — A Phase 2 gate created without `operation` or `parameters_hash` is rejected with HTTP 422 `INVALID_APPROVAL_BINDING`.
E3.1.4 — Existing Phase 1 approval rows continue to resolve without the new columns; Phase 1 approval tests pass unmodified.

### REQ-E3.2
WHEN an approval is requested at a checkpoint,
THEN the artifact or parameter set being approved MUST be canonicalised identically at issuance and at consumption: `json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)` then SHA-256 hex, with documented normalization rules (strip surrounding whitespace, preserve internal content, no key reordering beyond `sort_keys`). Server-side identity (where applicable in later waves) comes from server config, never the client.

**Acceptance Criteria:**
E3.2.1 — Issuing and consuming the same artifact yields identical `parameters_hash`.
E3.2.2 — A parameter added/removed/changed between issuance and consumption changes `parameters_hash` and consumption is rejected (REQ-E3.3).
E3.2.3 — The canonical function is a single shared implementation used at both issuance and consumption.

### REQ-E3.3
WHEN a protected action's dispatch consumes an approval,
THEN `ApprovalRequest.consume_atomic(...)` MUST run **before** the protected executor is invoked, inside the **same `BEGIN IMMEDIATE` transaction** that acquires the lease, increments `attempt`, and transitions the step `READY → RUNNING` (REQ-E3.7). The consume is a CAS `UPDATE approval_requests SET consumed_at = now WHERE id = ? AND status = 'approved' AND consumed_at IS NULL AND (authorization_expires_at IS NULL OR now < authorization_expires_at) AND operation = ? AND parameters_hash = ? AND workflow_step_run_id = ? AND actor_id = ?` requiring `rowcount == 1`. The validation predicate checks ALL of: **operation, parameter digest, step binding, actor, status (`approved`), consumption marker (`consumed_at IS NULL`), and authorization validity (`now < authorization_expires_at`)**. If the consume yields `rowcount == 0`, the entire dispatch transaction rolls back (the step stays `READY`, no lease, `attempt` unchanged — **unexecuted**), an `approval_not_consumable` audit entry names the mismatch reason, and the step is re-gated (REQ-E2.4). If `rowcount == 1`, the transaction commits and only then is the executor invoked (outside the DB transaction).

**Acceptance Criteria:**
E3.3.1 — A second consume of the same approval returns `rowcount == 0` (replay rejected); the dispatch transaction rolls back and the step is unexecuted.
E3.3.2 — An approval whose `parameters_hash` differs from the action's canonical hash is rejected (dispatch rolls back).
E3.3.3 — An approval bound to step `a` cannot be consumed to authorize step `b` (step-run binding enforced).
E3.3.4 — An approval bound to `operation = "pr_creation_approval"` cannot authorize `implementation_authorization`.
E3.3.5 — `actor_id` mismatch is rejected (dispatch rolls back).
E3.3.6 — A `pending` (not yet resolved) approval cannot be consumed.
E3.3.7 — An `expired` approval cannot be consumed; `expired` approvals remain visible in the run's `approvals` projection (Phase 1 W1.5.4 preserved).
E3.3.8 — An `approved` approval whose `authorization_expires_at <= now` cannot be consumed (dispatch rolls back; re-gate).
E3.3.9 — Two concurrent dispatches attempting to consume the same approval: exactly one's consume `rowcount == 1` (and that dispatch proceeds); the other's `rowcount == 0` (rolls back, unexecuted, re-gate).
E3.3.10 — On a failed consume, the step's `status`, `lease_owner`, `lease_expires_at`, and `attempt` are unchanged from before the dispatch (the transaction rolled back).

### REQ-E3.4
WHEN pending approval lifetime is managed,
THEN a `pending` approval MUST expire after `Settings.APPROVAL_TTL_SECONDS` (default 3600): the expiry sweep sets `status = "expired"` for `pending` approvals whose `expires_at <= now` and writes `approval_expired`. A resolved (`approved`/`rejected`) approval is no longer pending-expirable. The local single-user trust boundary is explicit: `actor_id` is the local operator only; Wave 1 does not authenticate multiple users and MUST NOT claim to.

**Acceptance Criteria:**
E3.4.1 — A `pending` approval older than the TTL (`expires_at <= now`) is expired by the sweep and cannot be resolved to `approved` (409 `APPROVAL_EXPIRED`).
E3.4.2 — An `approved` approval is not expired by the pending-TTL sweep (governed by authorization validity, REQ-E3.6).
E3.4.3 — The spec and API responses MUST NOT claim multi-user authentication; `actor_id` is the local operator identity.

### REQ-E3.5
WHEN run/step deletion interacts with approvals,
THEN the existing Phase 1 `approval_requests.workflow_run_id` `ON DELETE CASCADE` is preserved; deleting a run deletes its approvals. The new `approval_requests.workflow_step_run_id` FK uses `ON DELETE SET NULL`; because step rows are only deleted via run CASCADE (which deletes approvals first via `workflow_run_id`), the `SET NULL` path never creates an orphaned, still-consumable approval.

**Acceptance Criteria:**
E3.5.1 — Deleting a run deletes its `ApprovalRequest` rows (cascade via `workflow_run_id`) and its `WorkflowStepRun` rows.
E3.5.2 — No code path deletes a single `WorkflowStepRun` without deleting the run; the repository exposes no such method (guard test).
E3.5.3 — An approval bound to a deleted run is gone; there is no NULL-to-NULL bypass (approvals CASCADE on the run FK).

### REQ-E3.6
WHEN approved authorization validity is managed,
THEN an `approved` approval MUST carry `authorization_expires_at = resolved_at + Settings.APPROVAL_AUTHORIZATION_VALIDITY_SECONDS` (default 3600), enforced **separately** from the `status` CHECK constraint (status stays `approved`; no new status value). An `approved` approval is consumable only while `now < authorization_expires_at` and `consumed_at IS NULL` (REQ-E3.3). If the authorization expires before the protected action dispatches, the gated step re-gates (`READY → WAITING_APPROVAL`) with a fresh approval (REQ-E2.4); an expired authorization is never consumed and never restored. `authorization_expires_at` is `NULL` for Phase 1 rows (no validity bound; backward compatible).

**Acceptance Criteria:**
E3.6.1 — An `approved` approval past `authorization_expires_at` cannot be consumed (`rowcount == 0`; dispatch rolls back; re-gate).
E3.6.2 — A gated step whose approved authorization expired before dispatch re-gates to `WAITING_APPROVAL` with a fresh approval.
E3.6.3 — Phase 1 `approved` rows (`authorization_expires_at IS NULL`) remain consumable per Phase 1 semantics (no regression).
E3.6.4 — The `status` CHECK constraint is unchanged; validity is enforced via the column and the consume predicate only.

### REQ-E3.7
WHEN an approvable (protected) step is dispatched,
THEN the protected executor MUST NOT be invoked before its approval is atomically consumed. Lease acquisition, `attempt` increment, the `READY → RUNNING` state transition, and approval consumption MUST be coordinated in **one atomic `BEGIN IMMEDIATE` transaction**; the executor is invoked **only after** that transaction commits, and the executor call is **outside** the database transaction (REQ-E2.8, §10). A failed authorization transaction MUST leave the step unexecuted: the step stays `READY` (no lease, `attempt` unchanged), the approval is not mutated, and the orchestrator re-gates the step (REQ-E2.4). Cancellation, failure, timeout, and `indeterminate` outcomes after a successful consume MUST NOT restore the consumed approval (`consumed_at` is never set back to `NULL`); the consumed authorization is spent. Stale completions from a superseded dispatch cannot overwrite the current step result (fencing, REQ-E4.11) and cannot consume a second time (the approval was already consumed at that dispatch's transaction). Future retries of a protected operation require fresh authorization: an approvable step's retryable failure transitions `RUNNING → WAITING_APPROVAL` (re-gate, fresh approval) rather than `RUNNING → READY` (REQ-E4.7).

**Acceptance Criteria:**
E3.7.1 — For an approvable step, `executor.execute` is never called before `consume_atomic` returns `rowcount == 1` and the dispatch transaction commits (asserted by a test that fails the consume and asserts no executor invocation).
E3.7.2 — The lease CAS and the consume CAS execute in one transaction; on consume failure both roll back (step `READY`, no lease, `attempt` unchanged).
E3.7.3 — A failed consume leaves the step unexecuted and writes `approval_not_consumable`; the step re-gates.
E3.7.4 — After a successful consume + execute that ends in `failed`/`cancelled`/`timeout`/`indeterminate`, the approval's `consumed_at` remains set (never restored); no code path sets `consumed_at` back to `NULL`.
E3.7.5 — An approvable step's retryable failure re-gates (`RUNNING → WAITING_APPROVAL`, fresh approval), not `RUNNING → READY`.
E3.7.6 — A stale completion (superseded lease/attempt) does not re-consume any approval and does not overwrite the current result.

### REQ-E3.8
WHEN a `WAITING_APPROVAL` step's pending approval has expired (detected during reconciliation, resolve, or `start_or_resume`),
THEN `ApprovalRequestRepository.expire_and_re_gate(step_run_id, new_approval_data)` MUST, inside a single `BEGIN IMMEDIATE` transaction, **atomically guarantee uniqueness** of the resulting pending approval: (1) check whether a `pending` Phase 2 approval for this step already exists AND its `expires_at > now` (strictly in the future) — a pending approval with `expires_at <= now` MUST NOT be reused and MUST be treated as expired; (2) if a valid (not-yet-expired) pending approval exists, return it without creating a duplicate; (3) if no valid pending approval exists, expire any old `pending` or already-`expired` approval records for this step (UPDATE `status = 'expired' WHERE status = 'pending'`; `expired` rows require no action), then insert the new `ApprovalRequest`; this insert is protected by the partial unique index `uidx_approval_step_pending`; (4) on a `UniqueConstraintViolation` (concurrent insert) ONLY — other `IntegrityError` subclasses re-raise unchanged, the transaction is rolled back and the SQLAlchemy session is explicitly reset before any further DB work (see CA) — catch the violation, perform a session-rollback, re-open `BEGIN IMMEDIATE`, re-read the existing pending approval, verify `expires_at > now`, and return it; (5) write audit entries (`approval_expired` if expiry was performed; `approval_re_gated` with the new approval id; `approval_re_gate_noop` if an existing valid pending was returned) inside the same transaction. `SQLITE_BUSY` is retried with bounded exponential backoff (max 3 attempts). Cancellation and failure-stop prevent re-gating: if the run is `cancelled` or failure-stop is engaged, `expire_and_re_gate` MUST NOT create a new approval and MUST NOT be called.

**Acceptance Criteria:**
E3.8.1 — Pre-sweep expiry: a `WAITING_APPROVAL` step whose `pending` approval has `expires_at <= now` (sweep not yet run) is re-gated atomically — old approval expired, new created with the unique index preventing duplicates, step stays `WAITING_APPROVAL`.
E3.8.2 — Post-sweep expiry: a `WAITING_APPROVAL` step whose approval status is already `expired` (sweep already ran) is re-gated atomically — only new approval created (no double-expire), step stays `WAITING_APPROVAL`.
E3.8.3 — Re-gate is idempotent: if called twice concurrently, **exactly one** `pending` approval with `expires_at > now` exists for the step after both calls complete; the second call returns the existing valid pending approval (not a new one); no duplicate pending approvals are created.
E3.8.4 — `approval_expired` and `approval_re_gated` audit entries are written inside the same transaction; re-using an existing valid pending approval writes only an informational `approval_re_gate_noop` audit entry.
E3.8.5 — Re-gating is refused (raises `OperationNotPermitted`) when the run is in `cancelled` state.
E3.8.6 — Re-gating is refused when failure-stop is engaged (another step is terminally `FAILED`); the step will be settled to `FAILED(skipped)` by the failure-stop policy instead.
E3.8.7 — The partial unique index `uidx_approval_step_pending` on `approval_requests(workflow_step_run_id) WHERE status = 'pending' AND operation IS NOT NULL` MUST be created by migration 008 and dropped by its `downgrade()`. Phase 1 approval rows (`operation IS NULL`) are excluded from this index; legacy behavior is unaffected.
E3.8.8 — After `expire_and_re_gate`, `POST /approvals/{old_id}/resolve` returns HTTP 409 `APPROVAL_EXPIRED` (old approval is `expired`; the new approval has a different `id`).
E3.8.9 — `SQLITE_BUSY` during the `BEGIN IMMEDIATE` is retried up to 3 times with exponential backoff (10 ms, 20 ms, 40 ms); on exhaustion, `DatabaseBusyError` is raised and re-gating is not performed (caller may retry the entire reconciliation pass).
E3.8.10 — A `pending` approval found during the pre-check with `expires_at <= now` is treated as expired and NOT returned as a reusable replacement; it is expired in step (3) and a new approval is created (CA).
E3.8.11 — Only `UniqueConstraintViolation` is caught as a concurrency conflict; a `ForeignKeyViolation`, `CheckConstraintViolation`, or any other `IntegrityError` subclass propagates unchanged (CA).
E3.8.12 — After catching `UniqueConstraintViolation`, the SQLAlchemy session is explicitly rolled back and reset before any further database operations (CA); no partially-committed state remains in the session.

**Scope note (maps to task W1.4):** the eight-state step lifecycle, valid transitions, terminal states, restart recovery, duplicate-dispatch prevention, leases, timeouts, cancellation, bounded retries, idempotency, stale detection, fencing, failure-stop settle, and audit — all SQLite-safe.

### REQ-E4.1
WHEN step execution state is modelled,
THEN `WorkflowStepRun.status` MUST be constrained to exactly eight values: `PENDING`, `READY`, `RUNNING`, `WAITING_APPROVAL`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `INTERRUPTED`. Terminal states are `SUCCEEDED`, `FAILED`, `CANCELLED`. `INTERRUPTED` is non-terminal and recoverable. The run-level `WorkflowRun.status` keeps the Phase 1 six values unchanged and is derived from step states.

**Acceptance Criteria:**
E4.1.1 — The `workflow_step_runs.status` CHECK constraint lists exactly the eight values.
E4.1.2 — The `workflow_runs.status` CHECK constraint is unchanged from migration 003 (six values); migration 008 does not alter it.
E4.1.3 — `INTERRUPTED` is documented as non-terminal and recoverable (REQ-E4.8).
E4.1.4 — No ninth step status is introduced by the failure-stop settle policy (REQ-E4.12 reuses existing transitions to `FAILED`).

### REQ-E4.2
WHEN transitions are validated,
THEN the allowed transition map MUST be:

| From → To | Allowed | Trigger |
|---|---|---|
| PENDING → READY | yes | dependencies SUCCEEDED + inputs resolved (orchestrator) |
| PENDING → FAILED | yes | input-resolution failure (`INPUT_RESOLUTION_FAILED`), OR failure-stop settle (`skipped`, REQ-E4.12) |
| PENDING → CANCELLED | yes | run cancel |
| READY → RUNNING | yes | dispatch (CAS, lease acquired, `attempt` incremented; approval consumed in same txn if approvable, REQ-E3.7) |
| READY → WAITING_APPROVAL | yes | approvable gate reached, OR re-gate after expired/consumed/failed authorization |
| READY → FAILED | yes | pre-dispatch error (`EXECUTOR_NOT_FOUND`/`WORKING_DIRECTORY_NOT_APPROVED`/`CAPABILITY_MISMATCH`), OR failure-stop settle (`skipped`) |
| READY → CANCELLED | yes | run cancel |
| RUNNING → SUCCEEDED | yes | executor success (fenced CAS) |
| RUNNING → READY | yes | retryable failure, safe + idempotent + `attempt < max_attempts`, **non-approvable** (transient; NOT terminal) |
| RUNNING → WAITING_APPROVAL | yes | retryable failure, **approvable** (re-gate, fresh authorization; REQ-E3.7) |
| RUNNING → FAILED | yes | terminal: non-retryable, OR `attempt >= max_attempts`, OR `indeterminate`, OR failure-stop retry-suppression (`run_failure_stop`, REQ-E4.12) |
| RUNNING → INTERRUPTED | yes | recovery: lease expired while still `RUNNING` (process death) |
| RUNNING → CANCELLED | yes | run cancel (fenced) |
| WAITING_APPROVAL → READY | yes | approval approved |
| WAITING_APPROVAL → CANCELLED | yes | approval rejected / run cancel |
| WAITING_APPROVAL → FAILED | yes | failure-stop settle (`skipped`, REQ-E4.12) |
| WAITING_APPROVAL → WAITING_APPROVAL | yes | approval expired → re-gate (`expire_and_re_gate`, REQ-E3.8); step status unchanged; new approval record created atomically; run status unchanged |
| INTERRUPTED → READY | yes | recovery: safe + `attempt < max_attempts` (no auto-dispatch; awaits explicit start) |
| INTERRUPTED → FAILED | yes | recovery: unsafe / `attempt >= max_attempts`, OR failure-stop settle (`skipped`) |
| INTERRUPTED → CANCELLED | yes | run cancel |
| SUCCEEDED / FAILED / CANCELLED → * | no | terminal |

All other transitions MUST be rejected with `INVALID_STEP_TRANSITION`. A retry (`RUNNING → READY` or `RUNNING → WAITING_APPROVAL`) is a distinct, non-terminal transition and MUST NOT pass through `FAILED` (REQ-E4.7).

**Acceptance Criteria:**
E4.2.1 — Each allowed transition is exercised by a named test; each disallowed transition is exercised by a negative test that asserts no row mutation and an audit `step_transition_rejected`.
E4.2.2 — `PENDING → RUNNING` (skipping READY) is rejected.
E4.2.3 — `SUCCEEDED → RUNNING` (reviving a terminal step) is rejected.
E4.2.4 — A retryable failure transitions `RUNNING → READY` (non-approvable) or `RUNNING → WAITING_APPROVAL` (approvable), never through `FAILED`; `FAILED` is reached only on exhaustion/non-retryable/indeterminate/failure-stop.

### REQ-E4.3
WHEN the application restarts,
THEN `RecoveryService.recover()` MUST run at startup and perform **automatic state reconciliation**: scan `workflow_step_runs` for rows with `status = "RUNNING"` whose `lease_expires_at <= now` (or `IS NULL`), transition each to `INTERRUPTED` (fenced CAS) and write `step_interrupted`; then for each `INTERRUPTED` step decide `INTERRUPTED → READY` (safe + idempotent + `attempt < max_attempts`) or `INTERRUPTED → FAILED` (otherwise), writing `step_recovery_decided`. The recovery MUST be idempotent. **Resumed execution is NOT automatic:** `READY` steps (including recovered) are dispatched only by an explicit `start_or_resume`. A `RUNNING` step with a still-valid lease is NOT interrupted.

**Acceptance Criteria:**
E4.3.1 — A step stuck `RUNNING` with an expired lease becomes `INTERRUPTED` on recovery; a second recovery run leaves it decided once (idempotent).
E4.3.2 — A step `RUNNING` with a still-valid lease is NOT interrupted.
E4.3.3 — `step_interrupted` audit entry carries `prior_lease_owner`, `prior_lease_expires_at`.
E4.3.4 — After recovery, a safe recovered step is `READY` but NOT dispatched until `start_or_resume` is called.

### REQ-E4.4
WHEN the run reaches a terminal derivation,
THEN the run's terminal Phase 1 status MUST be derived **only when all steps are terminal**: `completed` if all `SUCCEEDED`; `failed` if any step terminally `FAILED` (real failure) and the failure-stop policy (REQ-E4.12) has settled all remaining steps, and none `CANCELLED` by cancel; `cancelled` if cancelled. While any step is non-terminal, the run is `running` (or `waiting_approval`). A `FAILED` step engages the failure-stop policy: no new dispatch; in-flight `RUNNING` steps finish; remaining non-terminal non-running steps are settled to `FAILED` (`skipped`); the run reaches `failed` once the policy has settled all steps. (With default concurrency 1, a `FAILED` step implies no other in-flight step.) A legacy zero-step run vacuously derives `completed` on start.

**Acceptance Criteria:**
E4.4.1 — After a `FAILED` step (concurrency 1), `start_or_resume` dispatches no further steps and the run reaches `failed` (after settle).
E4.4.2 — With concurrency > 1, an independent `RUNNING` step completes and records its own terminal state before the run reaches `failed` (run stays `running` until all terminal); meanwhile blocked steps are settled.
E4.4.3 — A legacy run (zero steps) vacuously completes on `start_or_resume` (`pending → running → completed`).

### REQ-E4.5
WHEN a step errors,
THEN `error_json` MUST be a structured object with `code: str`, `category: str` (`executor` | `timeout` | `cancelled` | `input_resolution` | `internal` | `skipped`), `message: str`, `retryable: bool`, `attempt: int`. `result_status` records the executor's declared outcome (`succeeded` | `failed` | `cancelled` | `indeterminate`). `indeterminate` outcomes are terminal `FAILED` with `retryable = false` (never auto-retried). Failure-stop-settled steps use `category = "skipped"`, `code = "STEP_BLOCKED_BY_RUN_FAILURE"`, `retryable = false`.

**Acceptance Criteria:**
E4.5.1 — A timeout produces `category = "timeout"`, `retryable = true` only for safe/idempotent steps.
E4.5.2 — An `indeterminate` outcome is persisted with `result_status = "indeterminate"` and `retryable = false`.
E4.5.3 — `error_json` is never set on `SUCCEEDED` steps.
E4.5.4 — A failure-stop-settled step has `category = "skipped"`, `code = "STEP_BLOCKED_BY_RUN_FAILURE"`, `retryable = false`.

### REQ-E4.6
WHEN duplicate dispatch is attempted,
THEN the `READY → RUNNING` transition MUST be a single CAS `UPDATE workflow_step_runs SET status='RUNNING', lease_owner=?, lease_expires_at=?, dispatched_at=now, started_at=now, attempt = attempt + 1, idempotency_key=f"{run_id}:{step_key}:{attempt+1}" WHERE id=? AND status='READY'` inside `BEGIN IMMEDIATE`; for an approvable step the approval consume (REQ-E3.3) is in the **same transaction**, before commit. Exactly one concurrent caller observes the lease CAS `rowcount == 1`; all others `rowcount == 0` and raise `StepDispatchConflict`. `lease_expires_at = now + STEP_LEASE_TTL_SECONDS` (default 60).

**Acceptance Criteria:**
E4.6.1 — Two concurrent `dispatch` calls on the same `READY` non-approvable step: one succeeds (new `lease_owner`, `attempt` incremented), one raises `StepDispatchConflict`.
E4.6.2 — The CAS uses `BEGIN IMMEDIATE`; the test asserts the transaction mode.
E4.6.3 — A `lease_owner` whose lease expired allows a fresh `INTERRUPTED → READY → RUNNING` re-dispatch (new `lease_owner`, incremented `attempt`).
E4.6.4 — For an approvable step, a concurrent dispatch race where one consumes the approval: only that one reaches `RUNNING` and invokes the executor; the other's consume `rowcount == 0` rolls back (unexecuted, re-gate) — see REQ-E3.3.9.

### REQ-E4.7
WHEN bounded retries are configured,
THEN a safe, idempotent step that fails with a retryable error and whose `attempt < max_attempts` MUST transition `RUNNING → READY` (transient; **non-approvable**) or `RUNNING → WAITING_APPROVAL` (**approvable** — re-gate, fresh authorization, REQ-E3.7), writing `step_retry_scheduled` with the next `attempt`. `attempt` is NOT incremented at this transition (incremented on the next `READY → RUNNING` dispatch). When `attempt >= max_attempts` (or the step is unsafe, non-idempotent, or `indeterminate`), the step transitions `RUNNING → FAILED` (terminal). `max_attempts` = total executions allowed (including the first); default 1 (no retry). Under the failure-stop policy (REQ-E4.12), retries are suppressed: a retryable failure transitions `RUNNING → FAILED` (reason `run_failure_stop`), not to `READY`/`WAITING_APPROVAL`.

**Acceptance Criteria:**
E4.7.1 — A safe non-approvable step with `max_attempts = 3` that fails at attempts 1 and 2 then succeeds at 3 reaches `SUCCEEDED` with `attempt = 3`.
E4.7.2 — A safe step that exhausts `max_attempts = 3` reaches `FAILED` with `attempt = 3`.
E4.7.3 — An unsafe/`indeterminate` step is never auto-retried even if `max_attempts > 1`.
E4.7.4 — A retry (non-approvable) transitions `RUNNING → READY` and never passes through `FAILED`; a retry (approvable) transitions `RUNNING → WAITING_APPROVAL` (fresh approval).
E4.7.5 — Under failure-stop, a retryable failure transitions `RUNNING → FAILED` (`run_failure_stop`), not to `READY`/`WAITING_APPROVAL`.
E4.7.6 — Wave 1's mock executor is the only executor and is declared `safe = true`, so retries are exercisable without external writes.

### REQ-E4.8
WHEN recovery decides retry-or-fail for an `INTERRUPTED` step,
THEN the recovery routine MUST transition `INTERRUPTED → READY` only when the step is safe and idempotent and `attempt < max_attempts` (and the run is not in failure-stop); otherwise `INTERRUPTED → FAILED`. If the run is already in failure-stop (another step already `FAILED`), recovery settles `INTERRUPTED → FAILED` (`skipped`). The decision is audited as `step_recovery_decided`. The recovered `READY` step awaits an explicit `start_or_resume` (REQ-E4.3).

**Acceptance Criteria:**
E4.8.1 — A safe `INTERRUPTED` step under its attempt budget (run not in failure-stop) goes `INTERRUPTED → READY` (not dispatched until explicit start).
E4.8.2 — A safe `INTERRUPTED` step at `attempt >= max_attempts` goes `INTERRUPTED → FAILED`.
E4.8.3 — An unsafe `INTERRUPTED` step goes `INTERRUPTED → FAILED`.
E4.8.4 — If the run is in failure-stop, an `INTERRUPTED` step is settled `INTERRUPTED → FAILED` (`skipped`).
E4.8.5 — `step_recovery_decided` audit entry carries `decision` (`retry` | `fail`), `reason`, `attempt`.

### REQ-E4.9
WHEN idempotency is required,
THEN each dispatch MUST carry an idempotency key `f"{run_id}:{step_key}:{attempt}"` (post-increment `attempt`) and an input digest (`SHA-256` of canonical `input_json`); a re-dispatch with the same key and input digest for a safe step MUST produce the same persisted result where the executor supports it (the mock executor is deterministic). Non-idempotent steps MUST NOT be retried (REQ-E4.7).

**Acceptance Criteria:**
E4.9.1 — Re-running the same safe step with identical input produces identical `output_json` (mock executor deterministic).
E4.9.2 — The idempotency key and input digest are persisted on the step row and appear in audit entries.
E4.9.3 — A changed input digest between attempts is audited and, for non-idempotent steps, blocks retry.

### REQ-E4.10
WHEN stale execution is detected,
THEN a heartbeat/lease model MUST mark a `RUNNING` step stale when `lease_expires_at <= now`; the lease is renewed every `STEP_HEARTBEAT_INTERVAL_SECONDS` (default 15) via a fenced CAS heartbeat (`UPDATE ... SET lease_expires_at = ? WHERE id = ? AND status = 'RUNNING' AND lease_owner = ? AND attempt = ?`). A lease owned by a cancelled run MUST NOT be renewed. Stale detection feeds REQ-E4.3 recovery.

**Acceptance Criteria:**
E4.10.1 — A heartbeat from the owning worker extends `lease_expires_at`; a heartbeat from a non-owner (or wrong `attempt`) is rejected (`rowcount == 0`).
E4.10.2 — A cancelled run's `RUNNING` step rejects heartbeat renewal.
E4.10.3 — Stale detection (lease expiry) feeds REQ-E4.3 recovery.

### REQ-E4.11
WHEN a stale executor completion arrives,
THEN every post-dispatch state-mutating CAS (`RUNNING → SUCCEEDED`, `RUNNING → FAILED`, `RUNNING → READY` retry, `RUNNING → WAITING_APPROVAL` re-gate, `RUNNING → CANCELLED`) MUST fence on the current `lease_owner` AND `attempt`: `UPDATE ... WHERE id = ? AND status = 'RUNNING' AND lease_owner = ? AND attempt = ?`. A completion from a stale (superseded) lease/attempt MUST yield `rowcount == 0`, be discarded, write `step_stale_completion_discarded`, and MUST NOT mutate the step or re-consume any approval (the approval, if any, was consumed at the original dispatch's transaction; a stale completion cannot consume again). This prevents a late return from a superseded dispatch (after the step was `INTERRUPTED` and re-dispatched, or after cancel) from corrupting state.

**Acceptance Criteria:**
E4.11.1 — A stale completion (old `lease_owner`/`attempt`) after the step was re-dispatched is discarded (`rowcount == 0`); the step keeps the new state.
E4.11.2 — A stale completion does NOT consume/re-consume any `ApprovalRequest`.
E4.11.3 — `step_stale_completion_discarded` audit entry carries `prior_lease_owner`, `prior_attempt`.
E4.11.4 — A cancel that supersedes an in-flight dispatch causes that dispatch's completion CAS to fail (`status` no longer `RUNNING`); no stale persistence occurs (and the consumed approval is not restored, REQ-E2.7.4).

### REQ-E4.12
WHEN the failure-stop policy is engaged (a step transitions to a real terminal `FAILED`),
THEN the orchestrator MUST deterministically settle the run: (a) **no new `READY → RUNNING` dispatch** occurs for any step; (b) already-`RUNNING` independent steps are allowed to finish to their natural terminal (a retryable failure during failure-stop goes `RUNNING → FAILED` with reason `run_failure_stop` — retry suppressed, REQ-E4.7.5); (c) every remaining non-terminal non-running step (`PENDING`, `READY`, `WAITING_APPROVAL`, `INTERRUPTED`) is transitioned to `FAILED` with `category = "skipped"`, `code = "STEP_BLOCKED_BY_RUN_FAILURE"`, `retryable = false`, `completed_at = now` (a guarded CAS per state, reusing existing transitions to `FAILED` — no new status); (d) any `pending` approval bound to a settled `WAITING_APPROVAL` step is set to `expired` (so a late resolve 409s/no-ops); (e) each settled step writes `step_failed` with the skipped reason. The run transitions to Phase 1 `failed` **only after** the stop policy has settled all remaining steps (all steps terminal). The policy is idempotent: re-entering it does not re-settle already-terminal steps.

**Acceptance Criteria:**
E4.12.1 — A linear workflow `a → b → c` where `b` fails terminally: `c` (and any later `PENDING`) is settled `FAILED` (`skipped`); the run reaches `failed`.
E4.12.2 — A diamond `a → {b,c} → d` where `b` fails while `c` is `RUNNING`: `c` finishes (its own terminal), `d` is settled `FAILED` (`skipped`, dependency `b` failed); the run reaches `failed`.
E4.12.3 — Independent in-flight steps: `a → b` (with `b` independent running `x`): when `a` fails, `x` finishes; blocked/`PENDING`/`READY` steps are settled `FAILED` (`skipped`); the run reaches `failed` only after `x` terminates.
E4.12.4 — A `WAITING_APPROVAL` step present when another step fails is settled `WAITING_APPROVAL → FAILED` (`skipped`) and its `pending` approval is expired (a later resolve returns 409 `APPROVAL_EXPIRED` or no-ops because the step is no longer `WAITING_APPROVAL`).
E4.12.5 — No new dispatch occurs after the run enters failure-stop (asserted by a test that attempts `start_or_resume` mid-failure-stop and observes no `RUNNING` transition for blocked steps).
E4.12.6 — Settle reuses only existing transitions to `FAILED` (no ninth step status); settled steps carry `category = "skipped"`.

---

## Area E5 — Executor Contracts

**Scope note (maps to task W1.5):** a versioned executor interface and the one safe executor Wave 1 ships.

### REQ-E5.1
WHEN an executor is registered,
THEN it MUST implement the `Executor` Protocol (in `src/devflow_api/executors/base.py`) with: `identifier: ExecutorIdentifier` (id + version), `capabilities: list[Capability]`, `input_schema: type[BaseModel]`, `output_schema: type[BaseModel]`, `timeout_policy: TimeoutPolicy`, and `async def execute(context: ExecutionContext) -> StepExecutionResult`. Contract Pydantic types live in `src/devflow_api/schemas/executor.py` (REQ-E2.8). The orchestrator selects an executor by `executor_id` and refuses dispatch for unregistered ids or capability mismatches (REQ-E2.2). The orchestrator invokes `execute` **only after** the dispatch transaction (lease + approval consume for approvable steps) has committed (REQ-E3.7).

**Acceptance Criteria:**
E5.1.1 — `ExecutorIdentifier`, `Capability`, `ExecutionContext`, `StepExecutionResult`, `TimeoutPolicy` are Pydantic v2 models in `src/devflow_api/schemas/executor.py`; `mypy --strict` passes.
E5.1.2 — Dispatching an unregistered `executor_id` fails the step `READY → FAILED` with `EXECUTOR_NOT_FOUND` (REQ-E2.2.3).
E5.1.3 — An executor whose declared `Capability` does not match the step's `tool_selection` fails the step `READY → FAILED` with `CAPABILITY_MISMATCH`.

### REQ-E5.2
WHEN an executor executes,
THEN it MUST receive an `ExecutionContext` containing `run_id`, `step_run_id`, `step_key`, `step_version`, `inputs` (resolved), `tool_selection`, `working_directory` (approved dir or `None`), `env_allowlist` (explicit dict, never the parent environment), `timeout_s`, `cancellation: CancellationHandle`, `actor_id`, `idempotency_key`, `input_digest`, and `attempt`. It MUST return a `StepExecutionResult` with `outcome` (`succeeded`|`failed`|`cancelled`|`indeterminate`), `output` (validated against `output_schema`), `artifact_refs: list[ArtifactRef]`, `error: StructuredError | None`, `metadata: ExecutionMetadata`.

**Acceptance Criteria:**
E5.2.1 — `ExecutionContext.working_directory` is either `None` or a member of `Settings.APPROVED_WORKING_DIRECTORIES`; executors MUST NOT write outside it.
E5.2.2 — `ExecutionContext.env_allowlist` is an explicit dict; executors MUST NOT read `os.environ` directly.
E5.2.3 — A result whose `output` fails `output_schema` validation is treated as `outcome = "failed"` with `category = "executor"`.
E5.2.4 — `artifact_refs` are references (paths/digests), never inlined large payloads.

### REQ-E5.3
WHEN a timeout or cancellation occurs,
THEN the orchestrator MUST enforce `timeout_s` via `asyncio.wait_for` and pass a `CancellationHandle`; a timeout (while the process is alive) produces `outcome = "failed"`, `category = "timeout"`, routed through retry logic (REQ-E4.7) or terminal `FAILED` — **never `INTERRUPTED`**. `INTERRUPTED` is reached only by recovery when the process died and the lease expired (REQ-E4.3). An executor that observes cancellation MUST return `outcome = "cancelled"` and the step reaches `CANCELLED`. A consumed approval (for an approvable step) is NOT restored on timeout or cancellation (REQ-E3.7.4).

**Acceptance Criteria:**
E5.3.1 — A step exceeding `timeout_s` transitions `RUNNING → READY` (retry, if safe & under budget) or `RUNNING → FAILED` (terminal) with `category = "timeout"` — never `INTERRUPTED`; for an approvable step the consumed approval stays consumed.
E5.3.2 — A cancelled executor call returns `outcome = "cancelled"` and the step reaches `CANCELLED`; the consumed approval is not restored.
E5.3.3 — A process death (no completion recorded) with an expired lease is recovered to `INTERRUPTED` by recovery, not by the dispatch path.

### REQ-E5.4
WHEN Wave 1 validates the engine,
THEN exactly one executor MUST ship: `MockExecutor` (`identifier.id = "mock"`, `version = 1`), a pure in-process no-op that echoes its inputs into `output`, sleeps a configurable short duration, observes the `CancellationHandle`, produces a deterministic `ArtifactRef`, declares `safe = true` and `idempotent = true`, uses no working directory, no environment, and no subprocess. No other executor and no shell/command execution MUST exist in Wave 1. **Executor security guards are mandatory** (hard gate): no module under `src/devflow_api/executors/` MAY invoke `subprocess`, `os.system`, `os.exec*`, `shell=True`, or read `os.environ`; executors MUST NOT import repositories/services/models (REQ-E2.8.3).

**Acceptance Criteria:**
E5.4.1 — `MockExecutor` is the only executor registered by default; `registry.list_executors()` returns exactly `["mock"]`.
E5.4.2 — `MockExecutor` performs no filesystem, network, or subprocess access (sandbox-leak test).
E5.4.3 — No module under `src/devflow_api/executors/` invokes `subprocess`/`os.system`/`os.exec*`/`shell=True` or reads `os.environ` (AST guard test — mandatory).
E5.4.4 — `MockExecutor` is deterministic: identical inputs → identical `output_json` and `artifact_refs`.

---

## Area E6 — Security Architecture

**Scope note (maps to task W1.6):** the local-first execution boundary and the honest statement of what is and is not isolated.

### REQ-E6.1
WHEN the API is exposed,
THEN all FastAPI/Uvicorn and Streamlit services MUST bind exclusively to `127.0.0.1` (Phase 0/1 constraint preserved); Wave 1 adds no network listener beyond the existing loopback services.

**Acceptance Criteria:**
E6.1.1 — No Wave 1 configuration binds to `0.0.0.0` or a non-loopback interface (config test).
E6.1.2 — Executor dispatch happens in-process over the loopback API only; no remote executor protocol in Wave 1.

### REQ-E6.2
WHEN an executor operates,
THEN it MUST be restricted to `Settings.APPROVED_WORKING_DIRECTORIES` (explicit allowlist; empty by default). Paths outside the allowlist MUST be refused as a pre-dispatch error (`READY → FAILED`, `WORKING_DIRECTORY_NOT_APPROVED`). `MockExecutor` uses no working directory. The spec MUST document that this is application-level path checking, not OS-enforced filesystem isolation.

**Acceptance Criteria:**
E6.2.1 — An `ExecutionContext.working_directory` not in the allowlist transitions the step `READY → FAILED` with `code = "WORKING_DIRECTORY_NOT_APPROVED"` before dispatch.
E6.2.2 — The default allowlist is empty; `MockExecutor` dispatches with `working_directory = None`.
E6.2.3 — A documentation note states this is not OS-level filesystem sandboxing.

### REQ-E6.3
WHEN environment variables are passed to any (future) subprocess,
THEN only `Settings.EXECUTOR_ENV_ALLOWLIST` variables are forwarded via an explicit `env` dict; there MUST be no implicit parent-environment inheritance. Wave 1 has no subprocess, so this is contractual only.

**Acceptance Criteria:**
E6.3.1 — The executor contract forbids `os.environ` reads in executor implementations (test + lint).
E6.3.2 — The spec explicitly states env-var scoping is Python-level, not OS sandboxing.
E6.3.3 — When subprocesses arrive in later waves, the explicit-env contract from Phase 1's MCP transport (W4-CR) is reused, not reinvented.

### REQ-E6.4
WHEN credentials appear,
THEN `RedactSecretsProcessor` (Phase 1) MUST run on every log record; no credential, token, or secret MUST appear in logs or audit payloads. `parameters_hash` stores a digest, never the secret material.

**Acceptance Criteria:**
E6.4.1 — A test asserts no token/secret substring appears in log records or `workflow_audit_log.payload_json` for a gated action (`test_no_secret_in_audit_or_logs`).
E6.4.2 — `parameters_hash` is a SHA-256 hex digest; raw parameters stored in `payload_json` only for non-secret artifacts and redaction applies.

### REQ-E6.5
WHEN approval enforcement is evaluated,
THEN every protected action MUST require an approval consumed atomically **before** the protected executor is invoked (REQ-E3.3, E3.7); credential presence MUST NOT be treated as authorization; an approval MUST NOT authorize a later, different, or unrelated action; a stale completion MUST NOT re-consume an approval (REQ-E4.11); a consumed approval is never restored (REQ-E3.7.4).

**Acceptance Criteria:**
E6.5.1 — A protected action whose pre-execution consume fails is refused (`APPROVAL_NOT_CONSUMABLE`); the executor is not invoked.
E6.5.2 — A consumed approval cannot authorize a second action (replay rejected).
E6.5.3 — An approval for operation `merge_approval` cannot authorize `pr_creation_approval`.
E6.5.4 — A stale completion does not re-consume an approval (fencing, REQ-E4.11.2).
E6.5.5 — Cancellation/failure/timeout/indeterminate after a successful consume does not restore the approval.

### REQ-E6.6
WHEN an external action outcome is indeterminate,
THEN the spec MUST define `INDETERMINATE` (5xx/transport errors for future external calls) as NOT auto-retried, terminal `FAILED` with `retryable = false`, and NOT auto-restoring any consumed approval; reconciliation requires a fresh approval (and, since `FAILED` is terminal, a fresh run/step). Wave 1 has no external calls; exercised by the mock executor's `indeterminate` test mode.

**Acceptance Criteria:**
E6.6.1 — `MockExecutor` has a test mode returning `outcome = "indeterminate"`; the step is `FAILED` with `retryable = false` and the consumed approval (if any) is not restored.
E6.6.2 — A fresh approval is required before any retry of an indeterminate action (via a new run/step).
E6.6.3 — The mock executor's `indeterminate` mode performs no external write (no side effect to reconcile).

### REQ-E6.7
WHEN audit integrity is considered,
THEN `WorkflowAuditLog` MUST remain append-only (Phase 1); Wave 1 MUST NOT add update/delete paths to audit rows. The spec MUST document that audit is append-only but not cryptographically signed/tamper-evident; a future wave may add chained hashes. No false claim of cryptographic integrity MUST be made.

**Acceptance Criteria:**
E6.7.1 — No `UPDATE`/`DELETE` is issued against `workflow_audit_log` in any Wave 1 code path (repository-shape test).
E6.7.2 — The spec documents the integrity posture honestly (append-only, not signed).

### REQ-E6.8
WHEN the boundary before real coding-agent execution is documented,
THEN the spec MUST list the additional isolation required before any real coding-agent (Codex/Kiro) or shell execution is enabled: OS-level process isolation, filesystem allowlists enforced by the OS, network egress control, resource limits (CPU/memory/time), and a credential vault. Wave 1 MUST NOT enable such execution and MUST gate it behind an explicit, separate future wave.

**Acceptance Criteria:**
E6.8.1 — The design document lists the isolation prerequisites in a dedicated section.
E6.8.2 — No Wave 1 code path enables real coding-agent or shell execution (guard test, REQ-E5.4.3).

---

## Area E7 — API and Streamlit Contracts

**Scope note (maps to task W1.7):** minimal APIs for observing and driving execution; full UX is Wave 5.

### REQ-E7.1
WHEN a client creates a run,
THEN `POST /api/v1/workflows/{id}/runs` MUST create the run and (for a valid template) all step rows (REQ-E2.1) and return HTTP 201 with the run and its initial steps. A non-empty invalid template returns HTTP 422 `INVALID_WORKFLOW_TEMPLATE`; a `NULL`/empty `definition_json` creates a legacy run (201, zero steps).

### REQ-E7.2
WHEN a client starts or resumes execution,
THEN `POST /api/v1/workflow-runs/{run_id}/start` MUST invoke `WorkflowOrchestrator.start_or_resume` and return HTTP 200 with the updated run and step states. Starting an already-terminal run returns HTTP 409 `RUN_NOT_STARTABLE`.

### REQ-E7.3
WHEN a client cancels a run,
THEN `POST /api/v1/workflow-runs/{run_id}/cancel` MUST invoke `WorkflowOrchestrator.cancel` and return HTTP 200 with the cancelled run. Cancelling an already-terminal run is idempotent (HTTP 200, no mutation).

### REQ-E7.4
WHEN a client inspects a run,
THEN `GET /api/v1/workflow-runs/{run_id}` MUST return the run plus `steps: list[StepRunResponse]` (ordered, with status, timestamps, `step_version`, `tool_selection`, `result_status`, `error`), and the existing Phase 1 `approvals`/`pending_approvals` projections (W1.5.4) preserved. `GET /api/v1/workflow-runs/{run_id}/steps/{step_id}` returns a single step including `output_json` and `artifact_refs` for `SUCCEEDED` steps and `error_json` for `FAILED` steps.

**Acceptance Criteria:**
E7.4.1 — `WorkflowRunResponse.steps` is ordered by template order; empty list `[]` never `null` (legacy runs return `[]`).
E7.4.2 — `approvals` and `pending_approvals` behave exactly as Phase 1 (W1.5.4a–c) — backward compatible.
E7.4.3 — `output_json`/`error_json` are `null` until the step reaches the relevant terminal state.

### REQ-E7.5
WHEN a client reads audit events,
THEN `GET /api/v1/workflow-runs/{run_id}/audit` MUST return ordered audit entries for the run (Phase 1 `WorkflowAuditLog`, extended event types). Pagination via `limit`/`offset` is supported.

### REQ-E7.6
WHEN a client approves or rejects a gate,
THEN `POST /api/v1/approvals/{id}/resolve` (Phase 1, extended) with `{"decision": "approved"|"rejected", "resolved_by": ...}` MUST validate the approval is `pending` (409 `APPROVAL_ALREADY_RESOLVED` if resolved; 409 `APPROVAL_EXPIRED` if `pending` but past `expires_at`), and resume or cancel the gated step (REQ-E2.5). Binding/validity is verified at the pre-execution consume (REQ-E3.3, E3.6), not at resolve.

### REQ-E7.7
WHEN the Streamlit UI observes execution,
THEN the existing `6_Workflows.py` page MUST be extended minimally to show a run's step list with live status, a "Start/Resume" button, a "Cancel" button, and an approval affordance for `WAITING_APPROVAL` steps. The full dashboard, filtering, and rich UX are deferred to Wave 5.

**Acceptance Criteria:**
E7.7.1 — `6_Workflows.py` renders run + steps without exception when the backend is available (`AppTest` with mocked `APIClient`).
E7.7.1b — A "Backend unavailable" notice (not an exception) is shown when the backend is unreachable (Phase 1 pattern preserved).
E7.7.2 — Start/Resume and Cancel buttons call the correct endpoints.
E7.7.3 — No fabricated/hard-coded run or step data.
E7.7.4 — `mypy --strict src/devflow_ui/pages/6_Workflows.py` exits with code 0.

### REQ-E7.8
WHEN OpenAPI is regenerated,
THEN `docs/openapi.json` MUST be regenerated to include the new endpoints (`/workflow-runs/{run_id}/start`, `/cancel`, `/audit`, `/steps/{step_id}`) and the extended `WorkflowRunResponse.steps`; no `/merge` endpoint is introduced (Phase 1 exclusion preserved).

---

## Area E8 — Testing and Acceptance Criteria

**Scope note (maps to task W1.8):** the automated test matrix and quality gates. Named tests below are the minimum required set; the implementation MAY add more.

### REQ-E8.1
WHEN the test suite runs,
THEN it MUST cover valid and invalid state transitions (REQ-E4.2), dependency ordering (REQ-E1.3), approval pre-execution consumption and replay/validity/re-gating (REQ-E3.3, E3.6, E3.7), concurrent execution and duplicate-dispatch prevention (REQ-E4.6), lease/attempt fencing and stale completions (REQ-E4.11), the failure-stop settle policy (REQ-E4.12), process interruption and recovery (REQ-E4.3, E4.8), cancellation (REQ-E2.7), timeouts (REQ-E5.3), idempotency (REQ-E4.9), attempt counting and retry budget (REQ-E4.7), run terminal derivation with blocked/independent steps (REQ-E4.4, E4.12), template snapshot immutability (REQ-E1.8), SQLite transaction rollback (REQ-E2.1, E4.6), audit logging (all), API validation (REQ-E7.x), mock executor behavior (REQ-E5.4), schema layering (REQ-E2.8), and backward compatibility with Phase 1.

**Required named tests (minimum set):**

State & transitions:
- `test_step_pending_to_ready_requires_dependencies_succeeded`
- `test_step_skip_ready_to_running_rejected`
- `test_step_terminal_state_revival_rejected`
- `test_invalid_transition_writes_rejection_audit_and_no_mutation`
- `test_pending_to_failed_on_input_resolution_failure`
- `test_ready_to_failed_on_executor_not_found`
- `test_ready_to_failed_on_working_directory_not_approved`
- `test_ready_to_failed_on_capability_mismatch`

Dependencies:
- `test_linear_chain_dispatches_in_order`
- `test_diamond_dependency_gates_until_all_succeeded`
- `test_failed_dependency_blocks_dependents`

Retry & attempt budget:
- `test_retryable_failure_transitions_running_to_ready_not_failed` (non-approvable)
- `test_retryable_failure_approvable_re_gates_to_waiting_approval` (approvable, fresh authorization)
- `test_retry_exhaustion_transitions_to_failed_terminal`
- `test_retry_never_passes_through_failed`
- `test_attempt_increments_on_dispatch`
- `test_attempt_budget_enforced_max_attempts`
- `test_unsafe_indeterminate_step_not_retried`
- `test_retry_suppressed_under_failure_stop` (REQ-E4.7.5)

Approvals — pre-execution consumption & validity (F14):
- `test_protected_executor_not_invoked_until_approval_consumed` (consume-before-execute)
- `test_consume_and_lease_acquired_in_one_transaction` (atomicity)
- `test_failed_consume_leaves_step_unexecuted_and_re_gates` (rollback; no lease; attempt unchanged)
- `test_consume_validation_rejects_status_not_approved`
- `test_consume_validation_rejects_already_consumed`
- `test_consume_validation_rejects_authorization_expired`
- `test_consume_validation_rejects_operation_mismatch`
- `test_consume_validation_rejects_digest_mismatch`
- `test_consume_validation_rejects_step_binding_mismatch`
- `test_consume_validation_rejects_actor_mismatch`
- `test_concurrent_consume_only_one_dispatch_proceeds` (concurrency)
- `test_consumed_approval_not_restored_on_failure`
- `test_consumed_approval_not_restored_on_cancel`
- `test_consumed_approval_not_restored_on_timeout`
- `test_consumed_approval_not_restored_on_indeterminate`
- `test_approval_re_gated_after_authorization_expiry`
- `test_expired_pending_approval_cannot_be_promoted_to_approved`

Approvals — expiry semantics (F16):
- `test_approval_expired_pre_sweep_returns_expired_code` (pending, now>=expires_at, sweep not run)
- `test_approval_expired_post_sweep_returns_expired_code` (status=expired after sweep; Phase 2 approval)
- `test_phase1_approval_already_resolved_still_returned_for_expired` (Phase 1 approval, expires_at IS NULL)
- `test_expire_and_re_gate_pre_sweep_waiting_approval_step` (old pending → expired + new pending, step stays WAITING_APPROVAL)
- `test_expire_and_re_gate_post_sweep_waiting_approval_step` (old expired + new pending, step stays WAITING_APPROVAL)
- `test_expire_and_re_gate_idempotent` (second call adds another pending, no corruption)
- `test_expire_and_re_gate_refused_when_run_cancelled`
- `test_expire_and_re_gate_refused_when_failure_stop_engaged`
- `test_concurrent_expire_and_re_gate_produces_valid_state` (both succeed; two pending approvals; documented risk)
- `test_old_approval_id_returns_expired_after_re_gate`

Concurrent gates and run status precedence (F17):
- `test_two_independent_gates_run_stays_waiting_approval`
- `test_approve_one_of_two_gates_run_stays_waiting_approval`
- `test_approve_both_gates_run_becomes_running`
- `test_reject_one_gate_cancels_run_regardless_of_other_gate`
- `test_executor_fails_while_gate_pending_run_stays_running_until_settled`
- `test_derive_run_status_all_succeeded_returns_completed`
- `test_derive_run_status_any_waiting_returns_waiting_approval`
- `test_derive_run_status_failure_stop_all_terminal_returns_failed`
- `test_derive_run_status_failure_stop_inflight_step_stays_running`
- `test_derive_run_status_idempotent_on_terminal_run`
- `test_derive_run_status_concurrent_calls_produce_consistent_state`
- `test_run_status_precedence_failed_beats_waiting_approval`

Approvals — general:
- `test_approval_consumed_once_replay_rejected`
- `test_approval_bound_to_step_a_cannot_authorize_step_b`
- `test_pending_approval_not_consumable`
- `test_expired_approval_not_consumable`
- `test_approved_authorization_expires_after_validity`

Concurrency, leases & fencing:
- `test_concurrent_dispatch_only_one_succeeds`
- `test_cas_uses_begin_immediate`
- `test_heartbeat_only_owner_renews_lease`
- `test_cancelled_run_rejects_heartbeat`
- `test_stale_completion_discarded_by_lease_fence`
- `test_stale_completion_cannot_consume_approval`

Failure-stop settle policy (F15):
- `test_failed_linear_workflow_settles_blocked_steps`
- `test_failed_diamond_dependency_settles_blocked_step`
- `test_independent_in_flight_step_finishes_before_run_failed`
- `test_no_new_dispatch_after_failure_stop`
- `test_waiting_approval_step_settled_and_approval_expired_on_run_failure`
- `test_run_reaches_failed_only_after_all_steps_settled`
- `test_settle_uses_skipped_category_no_ninth_status`

Recovery:
- `test_running_step_with_expired_lease_becomes_interrupted`
- `test_running_step_with_valid_lease_not_interrupted`
- `test_recovery_idempotent`
- `test_recovery_does_not_auto_dispatch_requires_explicit_start`
- `test_recovery_safe_step_retries_under_budget`
- `test_recovery_unsafe_step_fails`
- `test_recovery_step_at_max_attempts_fails`
- `test_recovery_settles_interrupted_when_run_in_failure_stop`

Cancellation:
- `test_cancel_run_marks_non_terminal_steps_cancelled`
- `test_cancel_run_keeps_succeeded_steps`
- `test_cancel_idempotent_on_terminal_run`
- `test_cancel_run_while_waiting_approval`
- `test_cancel_does_not_restore_consumed_approval`

Timeouts:
- `test_step_timeout_marks_failed_or_ready_not_interrupted`
- `test_executor_ignoring_cancel_marked_cancelled`
- `test_timeout_does_not_restore_consumed_approval_for_approvable`

Run derivation:
- `test_run_failed_derived_only_after_all_steps_terminal`
- `test_run_completed_only_when_all_steps_succeeded`
- `test_legacy_run_with_zero_steps_vacuously_completes`

Failure-stop with approval gates (F18):
- `test_failure_stop_with_waiting_approval_derives_running_not_waiting`
- `test_failure_stop_settles_waiting_approval_step_transitions_run_waiting_to_running`
- `test_failure_stop_all_steps_terminal_after_gate_settled_derives_failed`
- `test_derive_illegal_transition_raises_not_silent_noop`
- `test_derive_concurrent_race_caller_retries_once_and_succeeds`

Failure-stop normalization — final-step-settled edge case (CB):
- `test_failure_stop_all_terminal_while_run_waiting_approval_executes_two_step_transition`
- `test_failure_stop_two_step_transition_atomic_no_intermediate_running_visible`
- `test_concurrent_approval_resolve_cannot_bypass_failure_stop`
- `test_derive_uses_persisted_step_state_for_failure_stop_flag`

Concurrent re-gate uniqueness (F19):
- `test_expire_and_re_gate_concurrent_calls_produce_exactly_one_pending_approval`
- `test_expire_and_re_gate_second_call_returns_existing_pending_no_duplicate`
- `test_expire_and_re_gate_sqlite_busy_retried_bounded`
- `test_expire_and_re_gate_partial_unique_index_enforces_uniqueness`
- `test_phase1_approval_unaffected_by_partial_unique_index`
- `test_migration_008_creates_partial_unique_index`

Re-gate stale-pending and error scoping (CA):
- `test_expire_and_re_gate_stale_pending_approval_not_reused`
- `test_expire_and_re_gate_non_unique_integrity_error_propagates_unchanged`
- `test_expire_and_re_gate_session_rolled_back_after_unique_constraint_violation`

Template snapshot:
- `test_template_snapshot_immutable_after_live_workflow_edit`
- `test_template_hash_recorded_on_run`
- `test_legacy_run_has_no_snapshot_and_zero_steps`

Idempotency:
- `test_mock_executor_deterministic_output`
- `test_input_digest_change_blocks_retry_for_non_idempotent`

SQLite transactions:
- `test_run_creation_atomic_rollback`
- `test_cas_update_rolls_back_on_zero_rowcount`
- `test_dispatch_and_consume_atomic_rollback_on_consume_failure`

Audit:
- `test_every_transition_writes_audit`
- `test_audit_append_only_no_update_delete`
- `test_no_secret_in_audit_or_logs`
- `test_step_stale_completion_discarded_audited`
- `test_step_blocked_by_run_failure_audited`

API:
- `test_create_run_invalid_template_422`
- `test_create_run_legacy_run_on_null_template_201`
- `test_start_terminal_run_409`
- `test_cancel_terminal_run_idempotent_200`
- `test_run_response_includes_steps_and_approvals_projection`
- `test_audit_endpoint_pagination`

Mock executor & schema layering:
- `test_mock_executor_only_registered_executor`
- `test_mock_executor_no_fs_network_subprocess`
- `test_no_shell_execution_in_executors_package`
- `test_executors_do_not_import_repositories_services_models`
- `test_schemas_do_not_import_executors_package`

Backward compatibility (Phase 1):
- `test_phase1_workflow_crud_still_works`
- `test_phase1_create_run_on_null_template_returns_pending` (legacy run)
- `test_phase1_approval_resolve_still_works_without_operation`
- `test_phase1_run_status_constraint_unchanged`
- `test_approval_status_constraint_unchanged_no_consumed_value`
- `test_phase1_workflow_service_create_run_unchanged`

Security negatives:
- `test_working_directory_outside_allowlist_rejected`
- `test_env_allowlist_no_parent_inheritance_contract`
- `test_indeterminate_outcome_not_retried_and_approval_not_restored`
- `test_no_automatic_pr_merge_endpoint_absent`

Migrations:
- `test_migration_008_upgrade_creates_step_runs_extends_approvals_and_workflow_runs`
- `test_migration_008_creates_partial_unique_index_uidx_approval_step_pending`
- `test_migration_008_downgrade_restores_prior_schema`
- `test_full_round_trip_001_to_008`

### REQ-E8.2
WHEN quality gates run,
THEN `ruff check .`, `mypy --strict src/`, `pytest --cov=src --cov-branch --cov-fail-under=80` MUST all exit 0; `alembic upgrade head` reaches `008_add_workflow_execution_engine`; `alembic downgrade -1` returns to `007_add_github_confirmations`; and `docs/openapi.json` is regenerated. Phase 0 and Phase 1 tests continue to pass.

### REQ-E8.3
WHEN migration upgrade/downgrade is tested,
THEN migration 008 MUST be reversible: `upgrade` creates `workflow_step_runs`, adds the `approval_requests` columns/indexes, and adds the `workflow_runs` snapshot columns; `downgrade` drops them and restores the prior schema exactly, including the unchanged `approval_requests.status` and `workflow_runs.status` CHECK constraints.

---

## Traceability Matrix (requirements → task area → design section)

| Requirement | Task area (W1.x) | Design section |
|---|---|---|
| REQ-E1.x | W1.1 definition/execution model | §3 (data model), §4 (template model + snapshot) |
| REQ-E2.x | W1.2 orchestration service | §5 (orchestration), §6 (interface), §5.8 (run transitions/compat), §5.9 (derive_and_persist_run_status) |
| REQ-E2.10 | W1.2 run status derivation | §5.9 (status-precedence table + algorithm) |
| REQ-E3.x | W1.3 approval checkpoints | §7 (binding, validity, pre-execution consumption, re-gating, expiry re-gate) |
| REQ-E3.8 | W1.3 expiry re-gate | §7.5 (expire_and_re_gate) |
| REQ-E4.x | W1.4 lifecycle & recovery | §8 (state machine + WAITING_APPROVAL→WAITING_APPROVAL), §9 (recovery), §10 (SQLite + fencing), §5.5 (failure-stop) |
| REQ-E5.x | W1.5 executor contracts | §6 (contract), §11 (mock) |
| REQ-E6.x | W1.6 security | §12 (threat model) |
| REQ-E7.x | W1.7 API & Streamlit | §13 (API), §14 (Streamlit) |
| REQ-E8.x | W1.8 testing | §15 (test matrix), tasks.md |

---

## Assumptions, Dependencies, Out-of-Scope

**Assumptions:**
- Phase 1 v1.5.9 (commit `2d077ec`) is the merged, green baseline (PR #13 merged; 313 tests pass).
- The Alembic head is `007_add_github_confirmations` (verified); migration 008 chains from it; the implementer MUST re-confirm the head.
- The local operator identity is `Settings.LOCAL_OPERATOR_ID` (default `'local_operator'`), reused from Phase 1.
- `Workflow.definition_json` holds the `WorkflowTemplate`; the immutable snapshot is stored on `workflow_runs` (additive columns).

**Dependencies:**
- Phase 1 models, services (`WorkflowService.transition_run`, `ApprovalService.request_approval`/`resolve`, `run_repo.transition_status`), routers, and migrations 001–007.
- `Settings.LOCAL_OPERATOR_ID`, `RedactSecretsProcessor`, `UTCDateTime`, `_transaction`/`BEGIN IMMEDIATE` patterns (Phase 1).

**Out of scope (Wave 1):** real coding-agent execution, external integrations, PR creation/merge, full Streamlit dashboard.

**Backward-compatibility commitments:**
- `workflow_runs.status` and `approval_requests.status` CHECK constraints unchanged; new columns nullable; pending TTL via `expires_at`; approved validity via `authorization_expires_at`; consumption via `consumed_at`.
- Phase 1 `WorkflowService.create_run`/`transition_run` and `ApprovalService.request_approval`/`resolve` signatures preserved; `request_bound_approval` (new) and `resolve` (additive) extend without breaking Phase 1 callers.
- Legacy `NULL`/empty-template runs preserved (201 pending, zero steps); Phase 1 tests pass unmodified.
- Approval consumption is pre-execution and atomic with lease acquisition (REQ-E3.7); consumed approvals are never restored.
- Failure-stop settle reuses existing transitions to `FAILED` (no ninth status).
