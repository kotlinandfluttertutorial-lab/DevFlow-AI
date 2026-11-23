# DevFlow AI — Phase 2 Wave 3: Kiro Guided Handoff and Codex CLI Integration Tasks

**Version:** 1.0.2
**Status:** Frozen — v1.0.2 targeted integration corrections applied
**Scope:** Phase 2 Wave 3 — Kiro Guided Handoff and Codex CLI Integration
**Branch:** `feature/phase2-wave3-coding-agent-integration`
**Base commit:** `ee57dc7` (Wave 2 merged, develop HEAD)
**Last Updated:** 2026-11-23
**Companions:** `requirements.md` (v1.0.2), `design.md` (v1.0.2).

---

## Version History

- v1.0.0 — 2026-11-23 — Initial Wave 3 tasks.
- v1.0.1 — 2026-11-23 — ChatGPT review corrections F01–F10 applied.
- v1.0.2 — 2026-11-23 — Targeted integration corrections: execution mode enum updated to `manual|mock|real_when_isolated` in all acceptance criteria; `execution_plans` table AC updated for `baseline_commit`, `deadline_at`, NOT NULL FKs; durable waiting contract AC updated citing `outcome="indeterminate"`; git metadata path validation AC added; Kiro approval ordering AC added; artifact ownership AC updated for `execution_plan_id` and `content_hash`.

---

## Task Sizing Legend

- **S** — Small: a single model/schema/migration; < 1 day.
- **M** — Medium: a service or router with tests; 1–2 days.
- **L** — Large: a cross-cutting component with extensive tests; 2–3 days.

---

## Implementation Groups and Dependencies

```
Group A (foundation — no Wave 3 deps):
  W3.T6  Data model (migration 010; 7 tables)
  W3.T1  IsolationGate + WorkspaceValidator + config

Group B (executors and validators — depend on T6, T1):
  W3.T2  KiroHandoffExecutor
  W3.T3  ReportValidator: KiroVerificationReport
  W3.T4  CodexCLIExecutor
  W3.T5  ReportValidator: CodexImplementationReport (depends on T3, T4, T6)

Group C (services and API — depend on Group B):
  W3.T7  ExecutionPlanService + CodingAgentAuthorizationService
  W3.T10 WorkflowOrchestrator integration
  W3.T8  FastAPI routes

Group D (UI and tests — depend on Group C):
  W3.T9  Streamlit UI
  W3.T11 Offline security tests
  W3.T12 Full acceptance test suite
```

---

## Requirement Traceability

| Requirement Area | Tasks |
|---|---|
| W3.1 — Manual Workflow | W3.T2, W3.T4, W3.T5, W3.T8, W3.T9, W3.T12 |
| W3.2 — Kiro Guided Handoff | W3.T2, W3.T3, W3.T6, W3.T8, W3.T9, W3.T12 |
| W3.3 — Codex CLI Contract | W3.T4, W3.T5, W3.T7, W3.T8, W3.T9, W3.T11, W3.T12 |
| W3.4 — Implementation Authorization | W3.T7, W3.T8, W3.T9, W3.T12 |
| W3.5 — Workspace and Artifact Management | W3.T1, W3.T8, W3.T9, W3.T11, W3.T12 |
| W3.6 — Report Validation | W3.T3, W3.T5, W3.T8, W3.T9, W3.T11, W3.T12 |
| W3.7 — Execution Lifecycle | W3.T2, W3.T4, W3.T7, W3.T10, W3.T11, W3.T12 |
| W3.8 — Isolation Gate | W3.T1, W3.T11, W3.T12 |
| W3.9 — FastAPI Contracts | W3.T8, W3.T12 |
| W3.10 — Streamlit UI | W3.T9, W3.T12 |
| Security requirements | W3.T1, W3.T3, W3.T5, W3.T11 |

---

## Tasks

### W3.T1 — IsolationGate + WorkspaceValidator + Configuration

**Requirement areas:** REQ-W3.5.1–REQ-W3.5.5, REQ-W3.8.1–REQ-W3.8.3
**Size:** M
**Depends on:** Wave 2 baseline; `src/devflow_core/config.py`

**Description:**

Create `src/devflow_api/services/isolation_gate.py` with `IsolationGate`. Fail-closed: empty prerequisites → `not_configured` → blocks real execution. Unrecognized names → `prerequisites_not_met`. No config flag creates a `prerequisites_met` result.

Create `src/devflow_api/services/workspace_validator.py` with `WorkspaceValidator`. Use the **corrected algorithm** (design.md §6.1): inspect intermediate path components for symlinks BEFORE calling `Path.resolve()`. Reject UNC/device paths on Windows. Use `is_relative_to()` for containment, not string prefix. Implement `verify_git_identity()` using file parsing (design.md §6.2), not subprocess execution.

Add new `Settings` fields to `src/devflow_core/config.py`:
- `ISOLATION_PREREQUISITES: list[str] = Field(default_factory=list)`
- `WORKSPACE_FOLLOW_SYMLINKS: bool = False`
- `KIRO_HANDOFF_EXPIRY_SECONDS: int = Field(default=86400, gt=0)`
- `ARTIFACT_MAX_FILE_BYTES: int = Field(default=10_485_760, gt=0)`
- `ARTIFACT_MAX_TOTAL_BYTES: int = Field(default=104_857_600, gt=0)`
- `REPORT_FIELD_MAX_BYTES: int = Field(default=32768, gt=0)`
- `REPORT_MAX_BYTES: int = Field(default=5_242_880, gt=0)`

**Acceptance Criteria:**

W3.T1.1 — `IsolationGate` with empty list returns `not_configured`.
W3.T1.2 — Unrecognized prerequisite name returns `prerequisites_not_met`.
W3.T1.3 — No config flag creates `prerequisites_met`.
W3.T1.4 — `WorkspaceValidator` rejects a path with `..` resolving outside approved roots.
W3.T1.5 — `WorkspaceValidator` accepts a path with `..` resolving inside an approved root.
W3.T1.6 — Rejects symlink in any path component when `WORKSPACE_FOLLOW_SYMLINKS = false`.
W3.T1.7 — Rejects UNC path on Windows.
W3.T1.8 — Uses `is_relative_to()` for containment, not string prefix matching.
W3.T1.9 — `verify_git_identity()` reads `.git/config` without spawning a subprocess.
W3.T1.10 — All new `Settings` fields pass `mypy --strict`.
W3.T1.11 — Git metadata paths (gitdir target, config, HEAD) are validated against the approved workspace root before reading (design §6.3).
W3.T1.12 — `config` file containing `[include]` or `[includeIf]` returns `GIT_CONFIG_INCLUDES_UNSUPPORTED`.
W3.T1.13 — `objects/info/alternates` present returns `GIT_ALTERNATES_UNSUPPORTED`.
W3.T1.14 — `.gitmodules` present at workspace root returns `UNSUPPORTED_REPO_LAYOUT`.
W3.T1.15 — `HEAD` file exceeding 4 KB returns `GIT_METADATA_TOO_LARGE`.
W3.T1.16 — `gitdir:` target resolving outside approved workspace root returns `UNSUPPORTED_REPO_LAYOUT`.

**Definition of Done:** All 16 AC pass. `mypy --strict` passes. No production source outside named files altered.

**Estimated Complexity:** Medium

---

### W3.T2 — KiroHandoffExecutor (Prompt Generation, State Machine, Durable Wait)

**Requirement areas:** REQ-W3.1.1–REQ-W3.1.6, REQ-W3.2.1–REQ-W3.2.8, REQ-W3.7.1–REQ-W3.7.5
**Size:** L
**Depends on:** W3.T1 (Settings), W3.T6 (data model)

**Description:**

Create `src/devflow_api/executors/kiro_handoff.py` with `KiroHandoffExecutor` implementing the Wave 1 `Executor` Protocol. Implement the handoff state machine (design.md §3.1). The `awaiting_report` and `awaiting_impl_report` states are **non-running durable wait states**: no executor coroutine is held; the Wave 1 step enters `WAITING_APPROVAL`; the handoff sub-state is tracked in `handoff_records`. Resumption occurs on user action via fresh HTTP requests.

Store prompt content immutably in `handoff_artifacts` at export time (design.md §3.3). Implement digest binding (design.md §3.4). Implement expiry check in `CodingAgentRecoveryService`.

All transitions use `BEGIN IMMEDIATE` CAS. All transitions are audit-logged.

**Acceptance Criteria:**

W3.T2.1 — Creating a handoff for an approved spec version produces a `handoff_records` row in `handoff_created` state with all required fields.
W3.T2.2 — Exporting the prompt transitions to `awaiting_report`; prompt stored immutably in `handoff_artifacts`.
W3.T2.3 — Re-requesting an already-exported prompt returns identical content without regenerating `issued_at`.
W3.T2.4 — Submitting a report when in `awaiting_report` transitions to `report_submitted`.
W3.T2.5 — Submitting a report when not in `awaiting_report` returns `HANDOFF_NOT_AWAITING_REPORT`.
W3.T2.6 — After restart, `awaiting_report` handoff is still processable (report can be submitted).
W3.T2.7 — `artifact_digest` equals SHA-256 of concatenated approved artifact texts.
W3.T2.8 — Handoff past TTL transitions to `expired` via recovery service.
W3.T2.9 — All transitions recorded in audit log with `from_state`, `to_state`, timestamp.
W3.T2.10 — `mypy --strict` passes.

**Definition of Done:** All 10 AC pass. `KiroHandoffExecutor` registered as `"kiro_handoff"`. Recovery runs at startup. No Wave 1/Wave 2 test fails.

**Estimated Complexity:** Large

---

### W3.T3 — ReportValidator: KiroVerificationReport

**Requirement areas:** REQ-W3.2.4, REQ-W3.2.5, REQ-W3.6.1, REQ-W3.6.4, REQ-W3.6.5, REQ-W3.S.1–REQ-W3.S.3
**Size:** M
**Depends on:** W3.T2 (handoff record), W3.T6 (data model)

**Description:**

Create `src/devflow_api/services/report_validator.py` with `KiroReportValidator`. Define `KiroVerificationReport` and `KiroFinding` Pydantic v2 schemas in `src/devflow_api/schemas/coding_agent.py`. Implement the 14-step validation pipeline (design.md §7.1). Record `devflow_verification_passed` and `self_reported_verdict` separately. Implement finding classification with non-overridable categories (design.md §14.3). Unique constraint on `finding_id` within a handoff enforced at application level (and by DB constraint in migration 010). Store findings in `handoff_findings`.

**Acceptance Criteria:**

W3.T3.1 — Missing `verdict` returns `SCHEMA_VALIDATION_ERROR`.
W3.T3.2 — `artifact_digest` mismatch returns `DIGEST_MISMATCH` even if report asserts `PASS`.
W3.T3.3 — Duplicate `finding_id` values return `DUPLICATE_FINDING_IDS`.
W3.T3.4 — Report exceeding `REPORT_MAX_BYTES` returns `REPORT_TOO_LARGE`.
W3.T3.5 — `devflow_verification_passed = true` only when all 14 pipeline steps pass.
W3.T3.6 — Non-overridable findings are stored with `is_non_overridable = 1`.
W3.T3.7 — String fields with control characters are sanitized.
W3.T3.8 — Prompt injection detection produces an audit event with field name, not raw content.
W3.T3.9 — `mypy --strict` passes.
W3.T3.10 — Same report + same handoff always produces the same result.

**Definition of Done:** All 10 AC pass. `KiroVerificationReport` schema in `coding_agent.py`.

**Estimated Complexity:** Medium

---

### W3.T4 — CodexCLIExecutor (Contract, Mock, Manual, Real-Blocked)

**Requirement areas:** REQ-W3.1.2–REQ-W3.1.6, REQ-W3.3.1–REQ-W3.3.5, REQ-W3.7.1–REQ-W3.7.5
**Size:** L
**Depends on:** W3.T1 (IsolationGate), W3.T6 (data model)

**Description:**

Create `src/devflow_api/executors/codex_cli.py` with `CodexCLIExecutor`. Define `CodexExecutionInput`, `CodexExecutionOutput`, `CodexExecutionContract` schemas. Implement mock mode (deterministic synthetic output, no subprocess). Implement manual mode (prompt generation, `awaiting_impl_report` durable wait). Implement real-blocked outcome for `real_when_isolated` (IsolationGate check first; no subprocess, no authorization consumption). Compute canonical hash server-side from persisted plan data, never from client input.

**Acceptance Criteria:**

W3.T4.1 — Mock returns `execution_mode = "mock"`, `verdict = "PASS_WITH_WARNINGS"`, `synthetic = true`.
W3.T4.2 — No subprocess launched during mock (mock assert on `subprocess` module).
W3.T4.3 — Manual mode transitions to `awaiting_impl_report`; prompt stored immutably.
W3.T4.4 — Manual mode does not launch any subprocess.
W3.T4.5 — `real_when_isolated` with no prerequisites returns `ISOLATION_PREREQUISITES_NOT_MET`.
W3.T4.6 — Config flag does not bypass isolation gate.
W3.T4.7 — Client-supplied `canonical_execution_input_hash` is ignored; server recomputes.
W3.T4.8 — Canonical hash is deterministic; changing any input field changes hash.
W3.T4.9 — Wildcard `"*"` in `task_ids` returns HTTP 422.
W3.T4.10 — `mypy --strict` passes.
W3.T4.11 — `execution_mode = "blocked"` or any value outside `manual|mock|real_when_isolated` returns HTTP 422.
W3.T4.12 — `KiroHandoffExecutor.execute()` returns `outcome="indeterminate"` with `error.code="WAITING_FOR_KIRO_REPORT"` when entering `awaiting_report` state (source-verified against actual `StepExecutionResult` schema).
W3.T4.13 — Authorization is NOT consumed when `real_when_isolated` is blocked by `IsolationGate`.

**Definition of Done:** All 13 AC pass. `CodexCLIExecutor` registered as `"codex_cli"`. No real subprocess ever launched in Wave 3.

**Estimated Complexity:** Large

---

### W3.T5 — ReportValidator: CodexImplementationReport

**Requirement areas:** REQ-W3.6.2–REQ-W3.6.5, REQ-W3.1.5, REQ-W3.S.1–REQ-W3.S.3
**Size:** M
**Depends on:** W3.T3 (base validator), W3.T4 (execution attempt), W3.T6 (data model)

**Description:**

Extend `src/devflow_api/services/report_validator.py` with `CodexReportValidator`. Define `CodexImplementationReport`, `TaskResult`, `TestSummary` schemas (design.md §4.7). Implement the 19-step validation pipeline (design.md §7.2). Enforce task coverage: all authorized task IDs must be present; no duplicates; no contradictory verdict. Record `devflow_verification_passed` and `self_reported_verdict` separately.

**Acceptance Criteria:**

W3.T5.1 — Unrecognized `verdict` rejected.
W3.T5.2 — Unauthorized task ID in `task_results` returns `UNAUTHORIZED_TASK_IN_REPORT`.
W3.T5.3 — Duplicate task IDs in `task_results` return `DUPLICATE_TASK_RESULTS`.
W3.T5.4 — `execution_mode = "real"` for a mock/manual execution returns `EXECUTION_MODE_MISMATCH`.
W3.T5.5 — Missing authorized task from `task_results` returns `MISSING_TASK_RESULTS`.
W3.T5.6 — Failed task + `verdict = "PASS"` returns `CONTRADICTORY_VERDICT`.
W3.T5.7 — Inconsistent test counts return `INCONSISTENT_TEST_COUNTS`.
W3.T5.8 — Duplicate report submission returns `REPORT_ALREADY_SUBMITTED`.
W3.T5.9 — `devflow_verification_passed` and `self_reported_verdict` stored separately.
W3.T5.10 — `mypy --strict` passes.

**Definition of Done:** All 10 AC pass.

**Estimated Complexity:** Medium

---

### W3.T6 — Data Model: Migration 010

**Requirement areas:** Design §10
**Size:** M
**Depends on:** Wave 2 migration 009 as confirmed head

**Description:**

Create `alembic/versions/010_add_coding_agent_integration.py` with `down_revision = "009_add_ai_specification_generation"`. Create the 7 new tables: `handoff_records`, `handoff_artifacts`, `handoff_findings`, `execution_plans`, `execution_attempts`, `execution_artifacts`, `capability_records` (design.md §10.1). All indexes and CHECK constraints as specified. Unique constraint on `(workflow_run_id, workflow_step_run_id)` in `execution_plans` (not globally unique hash). Unique constraint on `(handoff_record_id, finding_id)` in `handoff_findings`. All timestamp columns use `UTCTextDateTime` from `src/devflow_api/models/specification.py`. Migration is fully reversible.

Add SQLAlchemy ORM models in `src/devflow_api/models/coding_agent.py` following the pattern in `specification.py`.

**Acceptance Criteria:**

W3.T6.1 — `alembic upgrade head` creates all 7 tables with correct schemas.
W3.T6.2 — `alembic downgrade -1` drops all 7 tables cleanly.
W3.T6.3 — Upgrade/downgrade chain from 001 to 010 and back passes without error.
W3.T6.4 — All CHECK constraints, indexes, and UNIQUE constraints from design.md §10.1 are present.
W3.T6.5 — No existing Phase 0/1/Wave 1/Wave 2 table is altered.
W3.T6.6 — `execution_plans` has `UNIQUE(workflow_run_id, workflow_step_run_id)`, not global hash uniqueness.
W3.T6.7 — `handoff_findings` has `UNIQUE(handoff_record_id, finding_id)`.
W3.T6.8 — `mypy --strict` passes for `models/coding_agent.py`.
W3.T6.9 — `execution_plans.workflow_run_id` and `workflow_step_run_id` are `NOT NULL` (standalone plans not supported in Wave 3).
W3.T6.10 — `execution_plans` contains `baseline_commit TEXT NOT NULL` and `deadline_at TEXT` columns.
W3.T6.11 — `execution_plans.execution_mode` CHECK constraint is `IN ('manual','mock','real_when_isolated')` — no `'blocked'` value.
W3.T6.12 — `handoff_artifacts.execution_plan_id` FK column present (NULL for kiro_prompt/kiro_report; NOT NULL for codex_prompt artifacts enforced at service layer).
W3.T6.13 — `handoff_artifacts.content_hash TEXT NOT NULL` present.

**Definition of Done:** All 13 AC pass. Migration 010 is new Alembic head.

**Estimated Complexity:** Medium

---

### W3.T7 — ExecutionPlanService + CodingAgentAuthorizationService

**Requirement areas:** REQ-W3.4.1–REQ-W3.4.6, REQ-W3.7.1–REQ-W3.7.5
**Size:** M
**Depends on:** W3.T6 (data model); Wave 1 `ApprovalService`

**Description:**

Create `src/devflow_api/services/execution_plan.py` with `ExecutionPlanService`. Creates `execution_plans` records. Computes canonical hash server-side from persisted plan data (design.md §8.3). Idempotency: same step + same hash → return existing plan; same step + different hash → `STALE_EXECUTION_INPUT`. Unique constraint `(workflow_run_id, workflow_step_run_id)` enforced at DB level and service level.

Create `src/devflow_api/services/coding_agent_authorization.py` with `CodingAgentAuthorizationService`. Integration with Wave 1 (design.md §8.1–8.2): populates step `inputs_json` with the full binding payload (design.md §8.3) before calling `request_bound_approval(run_id, step_run_id)`. Dispatch via `dispatch_protected_txn`. Atomic validation of all 10 conditions (REQ-W3.4.6).

**Acceptance Criteria:**

W3.T7.1 — `ExecutionPlanService.create()` creates plan with computed hash and `status = "draft"`.
W3.T7.2 — Same step + different hash → `STALE_EXECUTION_INPUT`.
W3.T7.3 — Same step + same hash → returns existing plan (idempotent).
W3.T7.4 — `request_bound_approval(run_id, step_run_id)` called with correct args.
W3.T7.5 — Approval granted → plan transitions to `authorized`.
W3.T7.6 — `dispatch_protected_txn` atomically consumes authorization.
W3.T7.7 — Second consumption attempt returns `AUTHORIZATION_ALREADY_CONSUMED`.
W3.T7.8 — Expired authorization fails `consume_atomic` → `AUTHORIZATION_EXPIRED`.
W3.T7.9 — Authorization from a different workflow run is rejected.
W3.T7.10 — `mypy --strict` passes for both service files.
W3.T7.11 — `execution_plans` rows are created with `workflow_run_id NOT NULL` and `workflow_step_run_id NOT NULL`; creating a plan without workflow context returns a service error.
W3.T7.12 — `baseline_commit` and `deadline_at` are persisted in `execution_plans` before `request_bound_approval` is called.
W3.T7.13 — Specification approval for a Wave 3 workflow-linked spec without a validated Kiro handoff matching the current content digest is rejected with `KIRO_VERIFICATION_REQUIRED`.
W3.T7.14 — Prompt artifact lookup uses `execution_plan_id` FK; a lookup by `handoff_id` alone without plan ID is rejected at service layer.

**Definition of Done:** All 14 AC pass. No new approval table introduced; Wave 1 `ApprovalService` only.

**Estimated Complexity:** Medium

---

### W3.T8 — FastAPI Routes (20 Endpoints)

**Requirement areas:** REQ-W3.9.1–REQ-W3.9.5
**Size:** L
**Depends on:** W3.T1–W3.T7

**Description:**

Create `src/devflow_api/routers/coding_agent.py` with the FastAPI router for all 20 endpoints (design.md §11). Follow Wave 2 patterns: HTTP 202 for async, HTTP 200 for status/poll. Idempotent prompt retrieval (GET endpoints return stored immutable content). Authorization request delegates to Wave 1 approval path. Define all schemas in `src/devflow_api/schemas/coding_agent_http.py`.

**Acceptance Criteria:**

W3.T8.1 — `GET /capabilities` returns all required fields including `codex_cli_capability_status`.
W3.T8.2 — `GET /isolation-status` returns status and unmet prerequisites.
W3.T8.3 — `POST /handoffs` returns HTTP 202.
W3.T8.4 — `GET /handoffs/{id}/prompt` returns immutable stored prompt; identical on repeat calls.
W3.T8.5 — All 20 endpoints in OpenAPI schema.
W3.T8.6 — Unauthenticated request returns HTTP 403.
W3.T8.7 — Synchronous errors use `{"error": ..., "code": ...}` envelope.
W3.T8.8 — Polling a failed async operation returns HTTP 200 with `error_code` in body.
W3.T8.9 — Artifact retrieval verifies SHA-256 hash; tampered artifact returns `ARTIFACT_INTEGRITY_FAILURE`.
W3.T8.10 — `mypy --strict` passes.

**Definition of Done:** All 10 AC pass. No existing routes affected. FastAPI startup succeeds.

**Estimated Complexity:** Large

---

### W3.T9 — Streamlit UI

**Requirement areas:** REQ-W3.10.1–REQ-W3.10.4
**Size:** M
**Depends on:** W3.T8

**Description:**

Create `src/devflow_ui/components/coding_agent.py` following design.md §12. All data access through `APIClient` only; no direct DB or service layer access. Mode selection (manual/mock/real_execution_blocked[disabled]). Kiro handoff screen with prompt display and report upload. Findings display: non-overridable findings have no override UI; advisory major findings have acknowledgment field requiring justification. Authorization expiry display. Audit timeline.

**Acceptance Criteria:**

W3.T9.1 — `real_execution_blocked` shown as disabled; "real execution succeeded" never shown in Wave 3.
W3.T9.2 — Prompt displayed in `st.code`; export triggers `.txt` download.
W3.T9.3 — Non-overridable findings show "BLOCKED — Cannot be overridden" with no bypass UI.
W3.T9.4 — Advisory major findings show acknowledgment field.
W3.T9.5 — Final approval button not rendered with unresolved non-overridable findings.
W3.T9.6 — Authorization expiry displayed.
W3.T9.7 — Audit timeline in chronological order.
W3.T9.8 — Self-reported verdict and DevFlow verification shown as separate items.

**Definition of Done:** All 8 AC pass (manual UI review). No existing Streamlit pages broken.

**Estimated Complexity:** Medium

---

### W3.T10 — WorkflowOrchestrator Integration

**Requirement areas:** REQ-W3.7.1–REQ-W3.7.5, REQ-W3.4.1
**Size:** M
**Depends on:** W3.T2, W3.T4, W3.T7

**Description:**

Register `KiroHandoffExecutor` and `CodexCLIExecutor` in `ExecutorRegistry` (update whitelist). `CodingAgentRecoveryService.recover()` runs at startup alongside `SpecificationRecoveryService`. Transitions `dispatching`/`mock_execution` → `interrupted`; expires overdue `awaiting_report` handoffs. Does NOT touch `awaiting_impl_report` or `awaiting_impl_authorization` (durable wait states).

**Acceptance Criteria:**

W3.T10.1 — `list_executors()` includes `"kiro_handoff"` and `"codex_cli"`.
W3.T10.2 — Orchestrator dispatches `executor_id = "kiro_handoff"` to `KiroHandoffExecutor`.
W3.T10.3 — Orchestrator dispatches `executor_id = "codex_cli"` to `CodexCLIExecutor`.
W3.T10.4 — Recovery transitions `dispatching`/`mock_execution` → `interrupted`.
W3.T10.5 — Recovery does NOT interrupt `awaiting_impl_report` or `awaiting_report`.
W3.T10.6 — Existing executors unaffected.

**Definition of Done:** All 6 AC pass. Wave 1/Wave 2 orchestrator tests pass.

**Estimated Complexity:** Medium

---

### W3.T11 — Offline Security Tests

**Requirement areas:** All security requirements, REQ-W3.8.1–W3.8.3, REQ-W3.5.1–W3.5.3
**Size:** M
**Depends on:** W3.T1–W3.T7

**Description:**

Create `tests/integration/test_wave3_security.py`. All offline (in-memory SQLite, mocked filesystem). Complete within 120 seconds.

**Acceptance Criteria:**

W3.T11.1 — IsolationGate: empty → `not_configured`; unrecognized → `prerequisites_not_met`.
W3.T11.2 — Config flag does not bypass isolation gate.
W3.T11.3 — WorkspaceValidator: `..` traversal rejected.
W3.T11.4 — WorkspaceValidator: symlink rejected when `WORKSPACE_FOLLOW_SYMLINKS = false`.
W3.T11.5 — Intermediate symlink check precedes `Path.resolve()` call.
W3.T11.6 — No subprocess launched during mock or blocked execution.
W3.T11.7 — Kiro report with `artifact_digest` mismatch and asserted `PASS` → rejected.
W3.T11.8 — Codex report with unauthorized task ID rejected.
W3.T11.9 — Authorization replay: second `consume_atomic` → `AUTHORIZATION_ALREADY_CONSUMED`.
W3.T11.10 — Non-overridable finding: no code path in validator permits bypass.
W3.T11.11 — String fields with control characters sanitized; injection pattern produces audit event.
W3.T11.12 — All criteria pass within 120 seconds.

**Definition of Done:** All 12 AC pass. Single `pytest tests/integration/test_wave3_security.py -x` run.

**Estimated Complexity:** Medium

---

### W3.T12 — Full Wave 3 Acceptance Test Suite

**Requirement areas:** All Wave 3 requirements; Wave 1/Wave 2 regression
**Size:** L
**Depends on:** W3.T1–W3.T11

**Description:**

Create `tests/integration/test_wave3_acceptance.py`. All offline (mocked services, in-memory SQLite). Complete within 120 seconds.

**Acceptance Criteria (W3.T12.1–W3.T12.28):**

**W3.T12.1 — Complete manual workflow**
Run all 17 manual workflow stages (REQ-W3.1.1) with mocked reports and approvals. Verify each stage transitions correctly, durable wait states survive between stages, and all 17 audit events are recorded.

**W3.T12.2 — Manual prompt export before authorization rejected**
Attempt to export implementation prompt without prior authorization. Verify HTTP 403 `IMPLEMENTATION_NOT_AUTHORIZED`. No prompt stored.

**W3.T12.3 — Authorized manual export consumes approval exactly once**
Export the authorized prompt. Verify authorization consumed via `consume_atomic`. Retrieve the prompt again; verify identical content returned and no second consumption attempt.

**W3.T12.4 — Manual report import does not launch subprocess**
Submit a manual implementation report. Verify no subprocess launched (mock assert). Verify attempt transitions to `impl_report_submitted`.

**W3.T12.5 — Mock output labelled synthetic; not real execution evidence**
Run mock execution. Verify all task result notes contain `[SYNTHETIC: mock execution]`. Verify audit event has `"synthetic": true`. Verify `execution_mode = "mock"` in report.

**W3.T12.6 — Real execution blocked regardless of configuration**
Request `real_when_isolated` with any configuration. Verify outcome is `real_execution_blocked` with `ISOLATION_PREREQUISITES_NOT_MET`. Verify no subprocess launched. Verify authorization not consumed.

**W3.T12.7 — Blocked execution does not consume approval**
Grant authorization. Request `real_when_isolated`. Verify outcome is `real_execution_blocked`. Verify `consumed_at` remains NULL on the authorization record.

**W3.T12.8 — Changed baseline commit invalidates execution eligibility**
Record baseline commit `abc123` in execution plan. Change workspace HEAD to `def456` before dispatch. Verify dispatch fails with `BASELINE_COMMIT_CHANGED`.

**W3.T12.9 — Changed task list invalidates authorization**
Authorize for tasks `["T1","T2"]`. Change to `["T1","T3"]`. Verify new hash does not match stored `parameters_hash` → `STALE_EXECUTION_INPUT`.

**W3.T12.10 — Changed deadline invalidates authorization**
Authorize with `deadline_at = now+1h`. Change deadline to `now+2h`. Verify `STALE_EXECUTION_INPUT` (hash changed).

**W3.T12.11 — Changed specification invalidates verification and authorization**
After handoff validation, update spec version (new artifact_digest). Verify existing handoff is not valid for the new version. Verify existing authorization invalidated (new hash required).

**W3.T12.12 — Approval from another workflow run rejected**
Create authorization in run A. Attempt to consume it in run B's dispatch. Verify `consume_atomic` fails (wrong `workflow_step_run_id`).

**W3.T12.13 — Concurrent dispatch has one winner**
Simulate two concurrent `dispatch_protected_txn` calls for the same step. Verify exactly one succeeds; the other raises `ApprovalNotConsumableError` or `StepDispatchConflict`.

**W3.T12.14 — Missing task results prevent PASS**
Submit a Codex report with one authorized task missing from `task_results`. Verify `MISSING_TASK_RESULTS`. Verify `devflow_verification_passed = false`.

**W3.T12.15 — Duplicate task results rejected**
Submit a Codex report with the same task ID appearing twice in `task_results`. Verify `DUPLICATE_TASK_RESULTS`.

**W3.T12.16 — Failed task with claimed PASS rejected**
Submit a Codex report with one task `status = "failed"` and `verdict = "PASS"`. Verify `CONTRADICTORY_VERDICT`.

**W3.T12.17 — Mismatched report digest rejected**
Submit a Kiro report with `artifact_digest` different from the handoff record. Verify `DIGEST_MISMATCH` and `devflow_verification_passed = false`.

**W3.T12.18 — Inconsistent test counts rejected**
Submit a Codex report where `passed + failed + skipped ≠ total`. Verify `INCONSISTENT_TEST_COUNTS`.

**W3.T12.19 — Kiro report persistence before execution attempt creation**
Submit a Kiro report and verify it is stored in `handoff_artifacts`, not `execution_artifacts`. Verify no `execution_attempts` row is required for Kiro report storage.

**W3.T12.20 — Artifact ownership and path traversal rejection**
Attempt to retrieve an artifact using a crafted storage key containing `..`. Verify storage key sanitization prevents traversal.

**W3.T12.21 — Intermediate symbolic link rejection**
Provide a workspace path containing a symlink as an intermediate component (not just the final component). Verify rejection with `SYMLINKS_DISALLOWED` when `WORKSPACE_FOLLOW_SYMLINKS = false`.

**W3.T12.22 — Windows junction policy**
On Windows: verify a Windows junction in a path component is treated as a symlink (rejected or followed per policy). On non-Windows: test is skipped.

**W3.T12.23 — Durable human wait without active worker lease**
Transition a handoff to `awaiting_report`. Verify no background task or coroutine is active. Simulate application idle. Submit a report. Verify report is accepted and processing resumes correctly.

**W3.T12.24 — Restart while waiting for Kiro report**
Create a handoff in `awaiting_report` state. Simulate restart (call `CodingAgentRecoveryService.recover()`). Verify handoff remains in `awaiting_report` (not interrupted). Verify report can still be submitted after restart.

**W3.T12.25 — Restart during report validation**
Create an execution attempt in `validating_report` or `dispatching` state. Simulate restart. Verify attempt transitions to `interrupted`. Verify no auto-redispatch occurs.

**W3.T12.26 — No automatic redispatch after restart**
Confirm `CodingAgentRecoveryService.recover()` does not invoke any executor, AI provider, or CLI. Verify only state transitions (interrupted, expired) and audit events are produced.

**W3.T12.27 — Non-overridable security findings cannot be bypassed**
Submit a Kiro report with a `blocker` finding categorized as `security`. Verify `is_non_overridable = 1`. Verify the workflow cannot advance. Verify the UI does not render an override button (API returns `non_overridable: true` on the finding).

**W3.T12.28 — Wave 1 and Wave 2 regression compatibility**
Run all existing Wave 1 orchestration tests and Wave 2 specification tests within the same test run. Verify all pass. Verify no Phase 0 or Phase 1 test has been broken by Wave 3 changes.

**Definition of Done:**
- All 28 sub-criteria pass in a single `pytest tests/integration/ -x` run.
- Total run completes within 120 seconds.
- No live external service called.
- No subprocess launched.
- Exit code 0.

**Estimated Complexity:** Large
