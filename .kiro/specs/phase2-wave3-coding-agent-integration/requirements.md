# DevFlow AI — Phase 2 Wave 3: Kiro Guided Handoff and Codex CLI Integration Requirements

**Version:** 1.0.4
**Status:** Freeze candidate — v1.0.4 targeted technical corrections
**Scope:** Phase 2 Wave 3 — Kiro Guided Handoff and Codex CLI Integration
**Branch:** `feature/phase2-wave3-coding-agent-integration`
**Base commit:** `ee57dc7` (Wave 2 merged, develop HEAD)
**Last Updated:** 2026-11-23
**Supersedes:** v1.0.0. Extends `.kiro/specs/phase2-wave2-ai-specification-generation/` (v1.6.1); does not modify it.

**Traceability:** Every requirement maps to at least one task in `tasks.md`. Every task maps to at least one requirement. ADRs are in `design.md` §13. Resolution matrix is in `docs/reports/phase2-wave3-spec-resolution-v1.0.1.md`.

---

## Version History

- v1.0.0 — 2026-11-23 — Initial Wave 3 specification.
- v1.0.1 — 2026-11-23 — Revised following ChatGPT review. F01: manual added as first-class execution mode; blocked redefined as an outcome state. F02: authorization binding corrected against actual Wave 1 source; plan-level idempotency strategy replaced global hash uniqueness constraint. F03: report binding fields extended; empty/partial task result now blocks PASS. F04: WorkspaceValidator algorithm corrected; symlink detection fixed; UNC/device-path policy added; TOCTOU documented. F05: approval ordering corrected; Kiro verification precedes final spec approval. F06: durable wait design clarified; non-running human-wait state defined. F07: report_artifacts ownership fixed for pre-attempt Kiro phase; handoff_artifacts table introduced. F08: non-overridable finding categories defined; override limited to advisory findings. F09: Codex CLI capability claims corrected; abstract contract retained. F10: API endpoint list updated; counts corrected.
- v1.0.2 — 2026-11-23 — Targeted integration corrections: (C1) execution mode enum reconciled to `manual|mock|real_when_isolated`; `blocked` removed from requested modes everywhere. (C2) immutable execution plan persistence: `baseline_commit`, `deadline_at` added; workflow ownership made non-null; crash-safe first transition specified; auth not consumed when real execution blocked. (C3) durable waiting contract source-verified against actual `StepExecutionResult` (`outcome="indeterminate"`); `WAITING_APPROVAL` step state confirmed; backward-compatible extension specified; wait reason, lease release, idempotent resumption, recovery, and duplicate-consumption prevention all specified. (C4) git metadata path validation added to WorkspaceValidator; bound reads, no config includes, `UNSUPPORTED_REPO_LAYOUT` error code defined. (C5) Kiro approval ordering enforced at `SpecificationApprovalService` backend; stale evidence invalidation rule added. (C6) artifact ownership: prompt lookup by `execution_plan_id` only; `content_hash` required; atomic publication; crash-safe recovery. (C7) version bumped to v1.0.2; counts updated.
- v1.0.3 — 2026-11-23 — R01–R05 schema and lifecycle corrections: replacement plan partial index, durable waiting source-verification, publication state DDL alignment, artifact ownership enforcement, Wave 3 workflow discriminator.
- v1.0.4 — 2026-11-24 — Correct plan replacement, explicit report waiting, atomic publication and capability ownership.

---

## Capability Investigation Results

### Kiro programmatic interface

**Selected implementation scope:** Guided handoff. DevFlow AI generates a structured prompt, the user runs Kiro manually in the Kiro IDE, and the user imports the resulting report back into DevFlow AI.

**Rationale:** As of November 2026, no stable publicly documented programmatic API exists for external systems to invoke Kiro automatically through the Kiro IDE. No CLI entrypoint, published REST API, SDK for external callers, or supported IPC mechanism has been confirmed available. If a stable programmatic API is published in the future, the executor design accommodates an adapter without changing the approval or lifecycle model.

**Design mandate:** `KiroHandoffExecutor` implements the guided handoff model. Wave 3 MUST NOT use UI automation, undocumented IPC, hidden APIs, or screen-scraping.

### Codex CLI

**Status:** Not installed on the current machine (`codex` command not found, exit code 1). Capability is **unavailable / unverified** on this system.

**Abstract invocation contract (from published documentation as of November 2026):**
- Non-interactive execution: `--approval-mode` flag with values `suggest`, `auto-edit`, `full-auto`.
- Working directory: `--cwd` flag.
- Authentication: `OPENAI_API_KEY` environment variable.
- Cancellation: SIGINT/SIGTERM.
- Exit codes: 0 = success, non-zero = failure.

**Important:** The exact flags above are **not verified** against an installed CLI binary. Wave 3 MUST NOT invoke the CLI. The abstract contract is retained for Wave 6 implementation. Exact executable flags are deferred to Wave 6, when the CLI will be installed, verified, and version-locked.

**Wave 3 behavior:** `CodexCLIExecutor` supports `mock` and `real_when_isolated` as requested modes. All `real_when_isolated` requests result in a `real_execution_blocked` outcome state in Wave 3.

---

## Overview

Wave 3 delivers the Kiro guided handoff executor, the Codex CLI executor contract, manual and mock implementation workflows, implementation authorization binding, workspace and artifact management, report validation, execution lifecycle management, isolation prerequisite enforcement, and the FastAPI and Streamlit integration.

**Wave 3 ships no real coding-agent execution.** `real_execution_blocked` is an outcome state, not a requested mode. The only requested modes are `manual`, `mock`, and `real_when_isolated`. Real subprocess invocation requires Wave 6 OS-level isolation prerequisites.

### Execution Modes (first-class)

| Mode | Description |
|---|---|
| `manual` | User exports prompt, runs Codex/Kiro independently, imports report. No subprocess. No isolation needed. |
| `mock` | DevFlow AI produces deterministic synthetic output in-process. No subprocess. No isolation needed. |
| `real_when_isolated` | Requested real execution. Blocked in Wave 3 (outcome: `real_execution_blocked`). Requires Wave 6. |

`real_execution_blocked` is an **outcome state** of `real_when_isolated` requests, not a mode.

### Global Constraints

- All Phase 0, Phase 1, Wave 1, and Wave 2 invariants are preserved. No existing approval model, state machine, or service contract is changed.
- The application MUST remain local-first, single-user, loopback-only, and free-core.
- All development services MUST bind exclusively to `127.0.0.1`.
- The codebase MUST NOT contain Android, React, TypeScript, or Vite artefacts.
- No hardcoded credentials or secrets MUST appear in source control.
- Architecture boundaries: UI → API → domain services → repositories → SQLite; services → adapters.
- `datetime.utcnow()` MUST NOT appear in any Wave 3 code; use `datetime.now(UTC)` exclusively.
- All new timestamp columns MUST use `UTCDateTime` from `src/devflow_api/models/base.py` or `UTCTextDateTime` from `src/devflow_api/models/specification.py`.
- Imported report content MUST be treated as untrusted data at all points. Sanitization and prompt delimiters reduce risk but do not eliminate prompt injection; they are defense-in-depth, not a guarantee.
- Real coding-agent execution is BLOCKED throughout Wave 3.
- No automatic GitHub write, commit, push, merge, or deploy is introduced by Wave 3.

### Out of Scope for Wave 3

| Capability | Deferral |
|---|---|
| OS-level process isolation | Wave 6 |
| Real Codex CLI subprocess execution | Wave 6 |
| GitHub PR creation | Wave 4 |
| Full Streamlit workflow dashboard | Wave 5 |
| Automatic PR merge | Permanent exclusion |
| Multi-user access control | Permanent exclusion |

---

## Area W3.1 — Manual Workflow

**Scope note:** Manual is a first-class execution mode. All stages operate without programmatic Kiro or Codex integration. The user exports prompts, runs tools independently, and imports reports. DevFlow AI validates all imported reports deterministically.

**Maps to tasks:** W3.T2, W3.T3, W3.T4, W3.T5, W3.T8, W3.T9, W3.T12

### REQ-W3.1.1 — Correct Workflow Ordering
WHEN a user progresses through the Wave 3 workflow,
THEN the system MUST enforce this ordered sequence: (1) Feature description submitted; (2) Specification generated (Wave 2); (3) Specification AI-reviewed (Wave 2 — optional preliminary review); (4) Kiro verification handoff created; (5) User exports Kiro verification prompt; (6) User runs Kiro manually; (7) User imports Kiro verification report; (8) DevFlow AI validates the Kiro report deterministically; (9) Blocking findings resolved (revision loop if needed); (10) Final human specification approval granted; (11) Immutable execution plan prepared; (12) Implementation authorization requested and granted; (13) Authorized prompt exported (manual mode) or mock execution dispatched; (14) User runs Codex manually (manual mode) or mock completes in-process; (15) User imports implementation report (manual mode) or mock report collected; (16) DevFlow AI validates implementation results; (17) Completion recorded. Final specification approval (stage 10) MUST come after Kiro verification validation (stage 8). Preliminary AI review before Kiro verification (stage 3) is permitted but does not replace final approval.

**Acceptance Criteria:**
REQ-W3.1.1.1 — Each of the 17 workflow stages is represented by a named durable state.
REQ-W3.1.1.2 — The workflow does not advance past stage N unless stage N's acceptance criteria pass and any required approval or authorization is recorded.
REQ-W3.1.1.3 — The system rejects an attempt to obtain final specification approval before Kiro report validation is recorded.
REQ-W3.1.1.4 — A test with mocked Kiro and Codex report imports and mocked approvals completes all stages without error.

### REQ-W3.1.2 — Manual Execution Plan
WHEN a user selects manual execution mode,
THEN the system MUST prepare an immutable execution plan that records: specification version ID, artifact digest, authorized task IDs (sorted), repository identity, workspace identity, execution mode (`manual`), and deadline. The plan MUST be prepared and persisted before implementation authorization is requested.

**Acceptance Criteria:**
REQ-W3.1.2.1 — An execution plan record is created with status `draft` before authorization is requested.
REQ-W3.1.2.2 — The plan is immutable after authorization is granted; changes to any binding field require a new plan.
REQ-W3.1.2.3 — An implementation prompt cannot be exported without an authorized plan.

### REQ-W3.1.3 — Manual Prompt Export and Authorization Consumption
WHEN a user exports the authorized implementation prompt in manual mode,
THEN the system MUST consume the implementation authorization at the first export event using the Wave 1 `dispatch_protected_txn` path, record `manual_handoff_created_at`, transition the execution attempt to `awaiting_impl_report`, and prevent duplicate exports from consuming a second authorization. Subsequent retrievals of an already-issued prompt are permitted (idempotent read of immutable content) without re-consuming authorization.

**Acceptance Criteria:**
REQ-W3.1.3.1 — The first export consumes the authorization (CAS: `consumed_at IS NULL → set consumed_at`).
REQ-W3.1.3.2 — Retrieving the same exported prompt a second time does not create a second authorization consumption.
REQ-W3.1.3.3 — Exporting a prompt without a valid unconsumed authorization returns HTTP 403 `IMPLEMENTATION_NOT_AUTHORIZED`.
REQ-W3.1.3.4 — The exported prompt includes: spec version ID, artifact digest, authorized task IDs, repo identity, workspace identity, execution mode, and an immutable `issued_at` timestamp (not regenerated on re-read).

### REQ-W3.1.4 — Manual Implementation Report Import
WHEN a user imports a manual implementation report,
THEN the system MUST accept it only when the execution attempt is in `awaiting_impl_report` state, transition to `impl_report_submitted`, store the raw report content verbatim without executing any field, and begin async validation. No subprocess is launched.

**Acceptance Criteria:**
REQ-W3.1.4.1 — Submitting a report when the attempt is not in `awaiting_impl_report` returns HTTP 409 `ATTEMPT_NOT_AWAITING_REPORT`.
REQ-W3.1.4.2 — Raw report content is stored verbatim; no field is passed to `eval()`, `exec()`, or any subprocess.
REQ-W3.1.4.3 — Submitting a manual report does not launch any subprocess (verified by mock assert in tests).

### REQ-W3.1.5 — Verified vs Self-Reported Evidence
WHEN a manual implementation report is validated,
THEN the system MUST separately record: (a) DevFlow AI's independently verified evidence (schema validity, digest match, task ID coverage, execution mode match) and (b) the self-reported outcome asserted by the report. A self-reported PASS verdict is NOT proof that implementation or tests succeeded; the user must separately confirm implementation quality.

**Acceptance Criteria:**
REQ-W3.1.5.1 — The validation result distinguishes `devflow_verification_passed` (boolean) from `self_reported_verdict` (from report).
REQ-W3.1.5.2 — A report asserting `verdict = "PASS"` but failing DevFlow verification checks does not produce `devflow_verification_passed = true`.
REQ-W3.1.5.3 — The UI clearly labels self-reported outcome as "Reported by user/agent" and DevFlow's check as "Verified by DevFlow AI".

### REQ-W3.1.6 — Manual Mode Never Launches Subprocess
WHEN execution mode is `manual`,
THEN no subprocess, shell command, OS process, or external binary MUST be invoked at any point in the execution lifecycle. Manual execution is entirely within DevFlow AI's control boundary (prompt generation, state management, report validation).

**Acceptance Criteria:**
REQ-W3.1.6.1 — No call to `subprocess`, `os.system`, `os.popen`, `asyncio.create_subprocess_exec`, or any equivalent appears in code paths active during manual execution.
REQ-W3.1.6.2 — Tests verify no subprocess is launched during the complete manual workflow.

---

## Area W3.2 — Kiro Guided Handoff

**Scope note:** KiroHandoffExecutor manages the state machine for Kiro verification. Handoffs are durable and survive application restarts. The executor does not hold an active lease during human-wait states.

**Maps to tasks:** W3.T2, W3.T3, W3.T6, W3.T8, W3.T9, W3.T12

### REQ-W3.2.1 — Handoff Creation
WHEN a Kiro handoff is initiated,
THEN the system MUST create a `handoff_records` row with: a unique integer ID, specification request ID, specification version ID, artifact digest (SHA-256 of the three approved artifact texts), selected task IDs (sorted JSON array), prompt text hash (SHA-256 of the generated prompt), actor ID, and a creation timestamp. State MUST be `handoff_created`. The specification version MUST be in `approved` or `ready_for_approval` state (Wave 2 states) for handoff creation to succeed. Only one non-terminal handoff per specification version ID is permitted at a time.

**Acceptance Criteria:**
REQ-W3.2.1.1 — A `handoff_records` row is created atomically with all required fields populated.
REQ-W3.2.1.2 — Initial state is `handoff_created`.
REQ-W3.2.1.3 — A second handoff creation for the same spec version ID while the first is non-terminal returns HTTP 409 `HANDOFF_ALREADY_ACTIVE`.
REQ-W3.2.1.4 — Creating a handoff for a spec version not in `approved` or `ready_for_approval` state returns HTTP 409 `SPECIFICATION_NOT_ELIGIBLE`.

### REQ-W3.2.2 — Prompt Export and Durable Wait
WHEN the user exports the Kiro verification prompt,
THEN the system MUST transition the handoff from `handoff_created` to `awaiting_report`, store the immutable prompt content (or the full set of inputs required to reproduce it exactly), set `prompt_exported_at` and `awaiting_since`, and release any executor lease. The handoff MUST NOT hold an active WorkflowStepRun lease during `awaiting_report` state; the durable wait is recorded only in the handoff record.

**Acceptance Criteria:**
REQ-W3.2.2.1 — Transition to `awaiting_report` is recorded with `prompt_exported_at` and `awaiting_since` timestamps.
REQ-W3.2.2.2 — The prompt content or its reproducible inputs are stored immutably at export time, including the `issued_at` timestamp.
REQ-W3.2.2.3 — Re-requesting the same prompt after export returns the same content without regenerating timestamps.
REQ-W3.2.2.4 — No WorkflowStepRun lease is held while the handoff is in `awaiting_report`.

### REQ-W3.2.3 — Report Submission
WHEN a user submits a Kiro verification report,
THEN the system MUST accept it only when the handoff is in `awaiting_report` state, transition to `report_submitted`, record `report_submitted_at`, store raw report content in `handoff_artifacts` (not in a column requiring an execution attempt ID), and start validation asynchronously. No field of the report is treated as an instruction.

**Acceptance Criteria:**
REQ-W3.2.3.1 — Report submission transitions state from `awaiting_report` to `report_submitted`.
REQ-W3.2.3.2 — Raw report content is stored in `handoff_artifacts` with an artifact type of `kiro_report`.
REQ-W3.2.3.3 — Submitting a report when not in `awaiting_report` returns HTTP 409 `HANDOFF_NOT_AWAITING_REPORT`.
REQ-W3.2.3.4 — Report storage is recorded before the HTTP 202 response is returned (atomic write).

### REQ-W3.2.4 — Report Validation
WHEN a Kiro verification report is validated,
THEN the system MUST run the deterministic validation pipeline: (1) JSON parse, (2) `KiroVerificationReport` schema validation, (3) schema version check, (4) `handoff_id` matches the handoff integer ID (string representation), (5) `specification_version_id` matches, (6) `specification_request_id` matches, (7) `artifact_digest` matches — `DIGEST_MISMATCH` on failure, (8) finding category validation, (9) string field sanitization (strip control chars `\x00–\x08`, `\x0b–\x0c`, `\x0e–\x1f`; cap at `REPORT_FIELD_MAX_BYTES`), (10) report size check (total raw report ≤ `REPORT_MAX_BYTES`, default 5 MB), (11) timestamp validity check (`reviewed_at` is a valid ISO 8601 UTC timestamp not in the future), (12) finding ID uniqueness (no duplicate `finding_id` within a report), (13) prompt injection pattern detection (audit-log on detection). `PASS` verdict recorded independently only after all checks pass.

**Acceptance Criteria:**
REQ-W3.2.4.1 — A schema-invalid report (missing `verdict`) transitions to `failed` with `SCHEMA_VALIDATION_ERROR`.
REQ-W3.2.4.2 — An `artifact_digest` mismatch transitions to `failed` with `DIGEST_MISMATCH` even if the report asserts `verdict = "PASS"`.
REQ-W3.2.4.3 — Duplicate `finding_id` values within one report return `DUPLICATE_FINDING_IDS`.
REQ-W3.2.4.4 — A report exceeding `REPORT_MAX_BYTES` is rejected with `REPORT_TOO_LARGE`.
REQ-W3.2.4.5 — A valid report with no blocking issues transitions to `report_validated` and records `validated_at`.
REQ-W3.2.4.6 — Validation is deterministic: the same report against the same handoff always produces the same outcome.

### REQ-W3.2.5 — Finding Classification and Override Policy
WHEN a Kiro verification report contains findings,
THEN the system MUST classify each finding as `blocker`, `major`, `minor`, or `informational`. The following finding categories are **non-overridable** and prevent advancement regardless of any user justification: `blocker` findings with categories `security`, `integrity_failure`, `digest_mismatch`, `invalid_authorization`, `isolation_failure`, `invalid_report_schema`, or `missing_mandatory_evidence`. `major` findings without a security/integrity sub-category may be formally acknowledged by the user (not silently bypassed) with a recorded justification and audit event. `minor` and `informational` findings are advisory and require only acknowledgment.

**Acceptance Criteria:**
REQ-W3.2.5.1 — Non-overridable findings cannot be bypassed by any user action; the workflow remains blocked until a corrected specification version produces a clean handoff.
REQ-W3.2.5.2 — Acknowledging a major advisory finding requires a user-supplied justification (minimum 20 characters) which is stored in `handoff_findings.override_justification` and audit-logged.
REQ-W3.2.5.3 — A report with no blocker or unacknowledged major findings may advance after validation.
REQ-W3.2.5.4 — The UI does not display an "override" option for non-overridable findings.

### REQ-W3.2.6 — Correction Request Cycle
WHEN findings require specification corrections,
THEN the user MUST be able to request a correction cycle: the handoff transitions to `correction_requested` (terminal for this handoff), the original handoff and all findings are preserved as historical evidence, the specification revision cycle (Wave 2) produces a new version with a new digest, and a new handoff can then be created for the revised version. A new handoff for the same specification version (without revision) is not permitted after `correction_requested`.

**Acceptance Criteria:**
REQ-W3.2.6.1 — Requesting corrections from a `report_validated` handoff transitions it to `correction_requested`.
REQ-W3.2.6.2 — The original handoff record and all findings are preserved and queryable.
REQ-W3.2.6.3 — Creating a new handoff for the same specification version after `correction_requested` returns HTTP 409 `SPECIFICATION_REQUIRES_REVISION`.

### REQ-W3.2.7 — Handoff Expiry
WHEN a handoff has been in `awaiting_report` state for longer than `KIRO_HANDOFF_EXPIRY_SECONDS` (default 86400),
THEN the recovery service MUST transition it to `expired`, record `expired_at`, emit an audit event, and prevent further report submission. A new handoff can be created for the same specification version.

**Acceptance Criteria:**
REQ-W3.2.7.1 — `awaiting_since + KIRO_HANDOFF_EXPIRY_SECONDS < now()` triggers transition to `expired`.
REQ-W3.2.7.2 — Submitting a report to an expired handoff returns HTTP 409 `HANDOFF_EXPIRED`.
REQ-W3.2.7.3 — A new handoff can be created after expiry.

### REQ-W3.2.8 — Handoff Cancellation
WHEN a user cancels a non-terminal handoff,
THEN the system MUST transition it to `cancelled`, record `cancelled_at`, emit an audit event, and allow a new handoff for the same specification version.

**Acceptance Criteria:**
REQ-W3.2.8.1 — Cancelling `handoff_created` or `awaiting_report` transitions to `cancelled`.
REQ-W3.2.8.2 — Cancelling a `report_validated` handoff transitions to `cancelled`; findings are preserved.
REQ-W3.2.8.3 — Cancelling a terminal handoff (`report_validated`, `failed`, `expired`, `cancelled`, `correction_requested`) returns HTTP 409 `HANDOFF_NOT_CANCELLABLE`.

### REQ-W3.2.9 — Backend Kiro Verification Prerequisite for Specification Approval
WHEN a final specification approval is requested for a Wave 3 workflow-linked specification version,
THEN the `SpecificationApprovalService` backend MUST reject the approval request if: (a) no completed `handoff_records` row exists with `status = 'report_validated'` whose `artifact_digest` matches the current `SpecificationVersion.content_digest`, or (b) the handoff's `workflow_run_id` does not match the current workflow run. This check is enforced server-side regardless of UI state. The error code is `KIRO_VERIFICATION_REQUIRED`.

**Acceptance Criteria:**
REQ-W3.2.9.1 — A specification approval request with no linked validated Kiro handoff is rejected with `KIRO_VERIFICATION_REQUIRED`.
REQ-W3.2.9.2 — A specification approval request where the validated handoff's `artifact_digest` does not match `SpecificationVersion.content_digest` is rejected with `KIRO_VERIFICATION_REQUIRED`.
REQ-W3.2.9.3 — The check is performed in `SpecificationApprovalService` before creating or resolving any approval record; it is not enforced only in the UI.
REQ-W3.2.9.4 — Legacy Wave 2 specification approvals (not linked to a Wave 3 workflow) are unaffected by this rule; the check applies only when the specification is associated with a Wave 3 workflow run.
REQ-W3.2.9.5 — A run identified from trusted workflow configuration as Wave 3 rejects approval with `KIRO_VERIFICATION_REQUIRED` if its capability snapshot is missing, invalid or inconsistent. A client flag cannot disable the prerequisite.

### REQ-W3.2.10 — Stale Kiro Evidence Invalidation on Specification Revision
WHEN a specification version is revised (a new version row is created for the same specification request),
THEN the new version row MUST be created with `kiro_verification_id = NULL`, invalidating any Kiro evidence bound to the prior version. A validated Kiro handoff for version N is not valid evidence for version N+1 even if the content is similar.

**Acceptance Criteria:**
REQ-W3.2.10.1 — Creating a new specification version sets `kiro_verification_id = NULL` on the new row.
REQ-W3.2.10.2 — Attempting to approve version N+1 using Kiro evidence bound to version N returns `KIRO_VERIFICATION_REQUIRED`.
REQ-W3.2.10.3 — The invalidation applies only to the new version; the prior version's records are preserved as historical evidence.

---

## Area W3.3 — Codex CLI Integration Contract

**Scope note:** The CodexCLIExecutor defines the typed contract. In Wave 3, only `mock` and `real_when_isolated` (outcome: `real_execution_blocked`) modes operate. No CLI binary is invoked.

**Maps to tasks:** W3.T4, W3.T5, W3.T7, W3.T8, W3.T9, W3.T11, W3.T12

### REQ-W3.3.1 — Typed Execution Input
WHEN a Codex execution is requested,
THEN the system MUST validate a typed `CodexExecutionInput` before accepting. The input MUST contain: `specification_request_id`, `specification_version_id`, `artifact_digest`, `task_ids` (non-empty list; no wildcard `*`), `repo_identity`, `workspace_identity`, `execution_mode` (`manual | mock | real_when_isolated`), `deadline_at` (UTC datetime in the future). The `canonical_execution_input_hash` MUST be computed server-side from persisted plan data, never trusted from client input.

> **Note on execution mode vs. execution outcome:** The requested execution mode is one of `manual`, `mock`, or `real_when_isolated`. These are the three values accepted as input. The outcome `real_execution_blocked` arises when `real_when_isolated` is requested but isolation prerequisites are unmet at execution time. It is an **outcome state**, not a valid requested mode. `blocked` is never accepted as an execution mode in any input field.

**Acceptance Criteria:**
REQ-W3.3.1.1 — Missing any required field returns HTTP 422.
REQ-W3.3.1.2 — An empty `task_ids` list returns HTTP 422.
REQ-W3.3.1.3 — `task_ids` containing `"*"` returns HTTP 422 `WILDCARD_TASK_ID_REJECTED`.
REQ-W3.3.1.4 — `deadline_at` in the past returns HTTP 422.
REQ-W3.3.1.5 — A client-supplied `canonical_execution_input_hash` is ignored; the server always recomputes it from persisted plan data.
REQ-W3.3.1.6 — Submitting `execution_mode = "blocked"` or any value outside `manual|mock|real_when_isolated` returns HTTP 422.

### REQ-W3.3.2 — Mock Execution Mode
WHEN execution mode is `mock`,
THEN the executor MUST produce a deterministic synthetic `CodexImplementationReport` without launching any subprocess, accessing any filesystem path outside the approved workspace roots, or making any network call. The report MUST have `execution_mode = "mock"` and `verdict = "PASS_WITH_WARNINGS"`. The report MUST clearly label all task results as synthetic. All audit events MUST include `"execution_mode": "mock"`. Mock results MUST NOT be represented as real implementation evidence.

**Acceptance Criteria:**
REQ-W3.3.2.1 — Mock execution returns a report with `execution_mode = "mock"` and `verdict = "PASS_WITH_WARNINGS"`.
REQ-W3.3.2.2 — No subprocess is launched (verified by patching `subprocess` module).
REQ-W3.3.2.3 — The audit event includes `"execution_mode": "mock"` and `"synthetic": true`.
REQ-W3.3.2.4 — Mock execution is deterministic: same input always produces same synthetic report.

### REQ-W3.3.3 — Real Execution Blocked in Wave 3
WHEN execution mode is `real_when_isolated`,
THEN the `IsolationGate` MUST be checked before any other action. If prerequisites are not met, the outcome state MUST be `real_execution_blocked` with error code `ISOLATION_PREREQUISITES_NOT_MET`. No authorization is consumed. No subprocess is launched. No workspace is accessed. This check is fail-closed: absence of affirmative all-prerequisites-met counts as not-met. In Wave 3, all `real_when_isolated` requests MUST result in `real_execution_blocked`.

**Acceptance Criteria:**
REQ-W3.3.3.1 — `real_when_isolated` with no prerequisites configured returns `ISOLATION_PREREQUISITES_NOT_MET`.
REQ-W3.3.3.2 — No configuration flag alone bypasses the gate.
REQ-W3.3.3.3 — Authorization is not consumed when execution is blocked.
REQ-W3.3.3.4 — Audit event includes `"blocked_by": "isolation_gate"`.

### REQ-W3.3.4 — Canonical Execution Input Hash
WHEN an execution plan is finalized,
THEN the system MUST compute the canonical execution input hash server-side from persisted plan fields: `specification_version_id`, `artifact_digest`, `sorted(task_ids)`, `repo_identity`, `workspace_identity`, `execution_mode`. The hash MUST use SHA-256 of the canonical JSON serialization (keys sorted, no whitespace). This hash is stored in the execution plan record and is used for stale-input detection; it is never accepted from client input.

**Acceptance Criteria:**
REQ-W3.3.4.1 — The same persisted plan fields always produce the same hash.
REQ-W3.3.4.2 — Changing any field (including one task ID or the execution mode) produces a different hash.
REQ-W3.3.4.3 — The hash is computed from the database record, not from the HTTP request body.

### REQ-W3.3.5 — Abstract Real Execution Contract (Future Wave 6)
WHEN real Codex execution is eventually implemented (Wave 6),
THEN the contract MUST require: (a) sanitized log capture (credential patterns redacted), (b) structured output collection from stdout, (c) exit status validation, (d) git diff for changed files, (e) artifact size limits before storage, (f) audit correlation linking execution to authorization. Wave 3 documents this contract without implementing it.

**Acceptance Criteria:**
REQ-W3.3.5.1 — `CodexExecutionContract` schema defines: `exit_code`, `stdout_summary`, `changed_files`, `artifact_refs`, `execution_mode`, `authorization_ref`.
REQ-W3.3.5.2 — The schema passes `mypy --strict`.
REQ-W3.3.5.3 — Log capture excludes fields named `token`, `key`, `secret`, `password`, `credential` (case-insensitive).

---

## Area W3.4 — Implementation Authorization

**Scope note:** Wave 3 reuses the existing Wave 1 `ApprovalService.request_bound_approval()` and `dispatch_protected_txn()` infrastructure. The `implementation_authorization` operation is an `ApprovalOperation` literal already defined in `src/devflow_api/schemas/workflow_template.py`. The authorization is bound via the Wave 1 step inputs mechanism.

**Maps to tasks:** W3.T7, W3.T8, W3.T9, W3.T12

### REQ-W3.4.1 — Authorization Request Using Wave 1 Path
WHEN a user requests implementation authorization,
THEN the system MUST invoke `ApprovalService.request_bound_approval(run_id, step_run_id)` for the workflow step representing the implementation authorization gate. The step's `protected_operation` MUST be `"implementation_authorization"`. The step's `inputs_json` MUST include: `specification_request_id`, `specification_version_id`, `artifact_digest`, `kiro_handoff_id` (the validated handoff integer ID), `execution_plan_id`, `task_ids` (sorted list), `repo_identity`, `workspace_identity`, `execution_mode`, `deadline_at`, `baseline_commit`. The `parameters_hash` is derived automatically by Wave 1 as `canonical_hash(step.inputs_json)` from the persisted inputs.

**Acceptance Criteria:**
REQ-W3.4.1.1 — `request_bound_approval` is invoked with the correct `run_id` and `step_run_id`.
REQ-W3.4.1.2 — The step's `protected_operation` is `"implementation_authorization"`.
REQ-W3.4.1.3 — The step's `inputs_json` includes all required binding fields before authorization is requested.
REQ-W3.4.1.4 — Approving the specification (Wave 2 gate) does NOT create an `implementation_authorization` approval record.
REQ-W3.4.1.5 — The `parameters_hash` stored in `approval_requests` equals `canonical_hash(step.inputs_json)` for the step's persisted inputs.

### REQ-W3.4.2 — Authorization Consumption via Protected Dispatch
WHEN implementation is dispatched (manual prompt export or mock execution),
THEN the system MUST invoke `dispatch_protected_txn` for the implementation step, which atomically: (a) transitions the step to `RUNNING`, (b) calls `consume_atomic` with `operation="implementation_authorization"`, `parameters_hash=step.input_digest`, `workflow_step_run_id=step.id`, `actor_id=LOCAL_OPERATOR_ID`, (c) verifies the authorization is `approved`, not consumed (`consumed_at IS NULL`), not expired (`authorization_expires_at > now()`), and all binding fields match. This is the single authorization consumption path; no parallel consumption path is introduced.

**Acceptance Criteria:**
REQ-W3.4.2.1 — Authorization is consumed via `dispatch_protected_txn`; `consumed_at` is set atomically.
REQ-W3.4.2.2 — A second dispatch attempt for the same step returns `ApprovalNotConsumableError` → `AUTHORIZATION_ALREADY_CONSUMED`.
REQ-W3.4.2.3 — Concurrent dispatch: exactly one succeeds; the other raises `ApprovalNotConsumableError`.
REQ-W3.4.2.4 — No new `ApprovalStatus` values are introduced to `approval_requests`; only the four existing values (`pending`, `approved`, `rejected`, `expired`) are used.

### REQ-W3.4.3 — Authorization Expiry
WHEN authorization expires before consumption,
THEN `dispatch_protected_txn` fails with `ApprovalNotConsumableError` (the `authorization_expires_at > now()` condition in `consume_atomic` is not met), which MUST be surfaced to the user as `AUTHORIZATION_EXPIRED`. The authorization is not consumed.

**Acceptance Criteria:**
REQ-W3.4.3.1 — An authorization with `authorization_expires_at` in the past fails `consume_atomic`.
REQ-W3.4.3.2 — The error surfaces as `AUTHORIZATION_EXPIRED` in the API response.
REQ-W3.4.3.3 — Expired authorizations are not consumed.

### REQ-W3.4.4 — Stale Input Rejection
WHEN the execution input changes after authorization is granted,
THEN the new inputs produce a different `canonical_hash`, which will not match the stored `parameters_hash` in the approval record, causing `consume_atomic` to fail. Changed task IDs, artifact digest, workspace, or execution mode all constitute a mismatch.

**Acceptance Criteria:**
REQ-W3.4.4.1 — An execution with task_ids different from those in the step's persisted inputs fails `consume_atomic` → `STALE_EXECUTION_INPUT`.
REQ-W3.4.4.2 — An execution with a changed `artifact_digest` fails → `STALE_EXECUTION_INPUT`.
REQ-W3.4.4.3 — A new plan and authorization are required after any input change.

### REQ-W3.4.5 — Duplicate Dispatch Prevention
WHEN a second dispatch is attempted for the same workflow step,
THEN the Wave 1 `dispatch_protected_txn` CAS mechanism (step must be in `READY` state to transition to `RUNNING`) prevents concurrent dispatch. `StepDispatchConflict` is raised if two dispatches race.

**Acceptance Criteria:**
REQ-W3.4.5.1 — A second execution for the same step returns `DUPLICATE_EXECUTION_PREVENTED`.
REQ-W3.4.5.2 — The first execution is unaffected.

### REQ-W3.4.6 — Complete Atomic Validation on Consumption
WHEN authorization is consumed via `dispatch_protected_txn`,
THEN the following MUST all be validated in one atomic `BEGIN IMMEDIATE` transaction: (a) approved decision, (b) correct actor and operation, (c) correct run and step, (d) matching `parameters_hash`, (e) valid spec approval present (Wave 2), (f) valid Kiro verification evidence present, (g) unexpired authorization, (h) unconsumed authorization, (i) no active duplicate execution, (j) eligible execution mode. All conditions are verified against persisted data, never against client-supplied inputs alone.

**Acceptance Criteria:**
REQ-W3.4.6.1 — Failing any of the 10 conditions prevents execution.
REQ-W3.4.6.2 — An authorization for a different workflow run (approval from run A, dispatch in run B) is rejected.
REQ-W3.4.6.3 — All 10 conditions are checked before `consumed_at` is set.

### REQ-W3.4.7 — Durable Persistence of All Authorization Inputs
WHEN an execution plan is created before implementation authorization is requested,
THEN the complete immutable execution plan — including `baseline_commit`, `deadline_at`, and `canonical_input_hash` — MUST be persisted in a single atomic transaction before `implementation_authorization` is requested. Authorization input reconstruction from mutable client data is prohibited; all binding fields are read from the persisted execution plan row.

**Acceptance Criteria:**
REQ-W3.4.7.1 — `baseline_commit` and `deadline_at` are persisted in `execution_plans` before authorization is requested.
REQ-W3.4.7.2 — Every authorized execution plan is linked to a workflow run and step (`workflow_run_id NOT NULL`, `workflow_step_run_id NOT NULL`). Standalone (non-workflow) execution plans are not supported in Wave 3.
REQ-W3.4.7.3 — The first manual prompt export, authorization consumption, durable prompt publication, and execution-attempt state transition are crash-safe: one `BEGIN IMMEDIATE` transaction verifies and consumes approval, stores immutable prompt bytes and digest as the publication intent, changes plan/attempt state and writes audit evidence.
REQ-W3.4.7.4 — Authorization MUST NOT be consumed for `real_when_isolated` mode when isolation prerequisites are unmet at execution time.
REQ-W3.4.7.5 — Replacement plans (after input changes) require creating a new `execution_plans` row and requesting fresh authorization. The old plan's authorization is not transferable.

### REQ-W3.4.8 — Replacement Execution Plans (R01)
WHEN input fields change after an execution plan is created for a workflow step,
THEN the system MUST support creating a replacement plan by atomically setting the old plan's status to `stale` and inserting a new plan row for the same `workflow_step_run_id`. At most one plan in an active status (`draft`, `ready`, `prompt_publishing`, `prompt_published`, `executing`) is permitted per `workflow_step_run_id` at any time, enforced by partial unique index. Historical and stale plans are retained as audit evidence.

**Acceptance Criteria:**
REQ-W3.4.8.1 — Creating a replacement plan atomically sets the old plan to `stale` in the same `BEGIN IMMEDIATE` transaction that inserts the new plan row.
REQ-W3.4.8.2 — A `stale` plan's authorization cannot be consumed; CAS on `consumed_at IS NULL` prevents reuse.
REQ-W3.4.8.3 — Fresh `implementation_authorization` approval is required for the new plan's `canonical_input_hash`; the stale plan's approval is not transferable.
REQ-W3.4.8.4 — The partial unique index `uidx_execution_plans_active_step` prevents inserting a second active plan without first staling the existing one.
REQ-W3.4.8.5 — Stale and completed plans for the same `workflow_step_run_id` are preserved and queryable as historical evidence.

---

## Area W3.5 — Workspace and Artifact Management

**Maps to tasks:** W3.T1, W3.T8, W3.T9, W3.T11, W3.T12

### REQ-W3.5.1 — Canonical Path Validation
WHEN a workspace path is provided,
THEN the system MUST: (1) check the path is absolute (reject relative paths with `RELATIVE_PATH_REJECTED`); (2) on Windows, reject UNC paths (`\\server\share`) and device paths (`\\.\`, `\\?\`) with `INVALID_PATH_FORMAT`; (3) resolve intermediate path components without following symlinks first, by inspecting each component for symlinks before `Path.resolve()`; (4) call `Path.resolve()` to eliminate all `..` components; (5) verify the resolved path exists and is a directory; (6) verify the resolved path falls within one of the operator-configured approved workspace roots using proper path containment (not string-prefix matching). Path containment MUST use `resolved_path.is_relative_to(approved_root)` or equivalent, not string prefix comparison.

**Acceptance Criteria:**
REQ-W3.5.1.1 — A relative path is rejected with `RELATIVE_PATH_REJECTED`.
REQ-W3.5.1.2 — On Windows, `\\server\share` and `\\?\` paths are rejected with `INVALID_PATH_FORMAT`.
REQ-W3.5.1.3 — A path with `..` resolving outside approved roots is rejected with `PATH_NOT_IN_APPROVED_WORKSPACE`.
REQ-W3.5.1.4 — A path with `..` resolving inside an approved root is accepted.
REQ-W3.5.1.5 — Path containment uses `is_relative_to()`, not string prefix matching.

### REQ-W3.5.2 — Symlink and Junction Policy
WHEN path resolution encounters a symlink, Windows junction, or Windows reparse point,
THEN the policy is controlled by `WORKSPACE_FOLLOW_SYMLINKS` (default `false`). When `false`: any path component that is a symlink, junction, or reparse point is rejected with `SYMLINKS_DISALLOWED` before `Path.resolve()` is called on the full path. When `true`: each path component is resolved in order; after full resolution, the target MUST still fall within an approved workspace root. The intermediate component check MUST precede the final `Path.resolve()` call to avoid the TOCTOU window where a symlink is created after the check.

**Acceptance Criteria:**
REQ-W3.5.2.1 — When `WORKSPACE_FOLLOW_SYMLINKS = false`, any symlink or junction in any path component is rejected with `SYMLINKS_DISALLOWED`.
REQ-W3.5.2.2 — When `WORKSPACE_FOLLOW_SYMLINKS = true`, a symlink pointing outside an approved root is rejected with `PATH_TRAVERSAL_DETECTED`.
REQ-W3.5.2.3 — The intermediate component check runs before the final `Path.resolve()` call.

### REQ-W3.5.3 — Platform-Specific Behavior
WHEN path validation runs on Windows,
THEN path comparison MUST be case-insensitive (Windows NTFS is case-insensitive). On POSIX, path comparison is case-sensitive. Backslash separators in input paths MUST be normalized to forward slashes only within the path validation context; this normalization MUST NOT be applied to POSIX paths. These are application-level checks and do not constitute OS isolation.

**Acceptance Criteria:**
REQ-W3.5.3.1 — On Windows, `C:\Work\proj` and `C:\work\PROJ` refer to the same path for containment checks.
REQ-W3.5.3.2 — Backslash normalization is applied only when the platform is Windows.
REQ-W3.5.3.3 — TOCTOU risk is documented; workspace validation is performed as late as possible, immediately before authorized use.

### REQ-W3.5.4 — Git Identity and Baseline Commit
WHEN a workspace passes path validation,
THEN the system MUST: (1) confirm a `.git` directory (or `.git` file for worktrees) exists at the workspace root; (2) for a `.git` file (worktree or submodule), read the `gitdir` reference and confirm it points to a valid git directory; (3) parse `.git/config` (or the worktree's config) for the `[remote "origin"]` URL without spawning a subprocess; (4) compare the parsed URL against `execution_input.repo_identity`; (5) read the HEAD commit SHA from `.git/HEAD` or `.git/refs/heads/<branch>` without spawning a subprocess; (6) record the baseline commit SHA in the execution attempt record. If git identity verification requires subprocess execution, the subprocess MUST be explicitly distinguished from coding-agent execution and MUST be a constrained read-only git command (not a full shell).

**Acceptance Criteria:**
REQ-W3.5.4.1 — A workspace without `.git` returns `NOT_A_GIT_REPOSITORY`.
REQ-W3.5.4.2 — A workspace whose git remote does not match `repo_identity` returns `REPOSITORY_IDENTITY_MISMATCH`.
REQ-W3.5.4.3 — The baseline commit SHA is recorded in the execution attempt.
REQ-W3.5.4.4 — Git config parsing does not spawn a full shell.

### REQ-W3.5.6 — Git Metadata Path Validation
WHEN the `WorkspaceValidator` reads any git metadata file or directory (`.git` dir, `.git` indirection file, `gitdir:` target, `commondir` reference, `config`, `HEAD`, ref files, `packed-refs`),
THEN every resolved path MUST be validated against the approved workspace root before being read. The validator MUST reject: (a) paths outside approved workspace roots, (b) symbolic links in git metadata paths (consistent with the workspace symlink policy in REQ-W3.5.2), (c) Windows junctions and reparse points.

**Acceptance Criteria:**
REQ-W3.5.6.1 — A `gitdir:` target that resolves outside the approved workspace root returns `UNSUPPORTED_REPO_LAYOUT`.
REQ-W3.5.6.2 — `.git` as a file is supported only if the `gitdir:` target resolves within an approved workspace root; otherwise returns `UNSUPPORTED_REPO_LAYOUT`.
REQ-W3.5.6.3 — Git metadata file reads are bounded: `HEAD`, `gitdir`, `commondir` ≤ 4 KB; `config` ≤ 64 KB; `packed-refs` ≤ 1 MB. Exceeding these limits returns `GIT_METADATA_TOO_LARGE`.
REQ-W3.5.6.4 — Git `config` files containing `include` or `includeIf` directives are rejected (returns `GIT_CONFIG_INCLUDES_UNSUPPORTED`). Config includes are not followed.
REQ-W3.5.6.5 — Repositories with an `objects/info/alternates` file are rejected (returns `GIT_ALTERNATES_UNSUPPORTED`). External object alternates are not followed.
REQ-W3.5.6.6 — Repositories containing a `.gitmodules` file at the root return `UNSUPPORTED_REPO_LAYOUT`. Submodule validation is deferred.
REQ-W3.5.6.7 — The standard repository layout requires `.git` to be a directory (not a file) for non-worktree repos. Standard layout validated before metadata reads proceed.
REQ-W3.5.6.8 — TOCTOU limitation documented: validation and read are not atomic. Mitigation is defense-in-depth (sandboxed process boundary at Wave 6, operator-controlled workspace roots).

### REQ-W3.5.5 — Artifact Size Limits and Hashes
WHEN implementation artifacts are stored,
THEN each file MUST NOT exceed `ARTIFACT_MAX_FILE_BYTES` (default 10 MB) and the total artifact set MUST NOT exceed `ARTIFACT_MAX_TOTAL_BYTES` (default 100 MB). Each accepted artifact MUST have a SHA-256 content hash recorded. Artifact file names are generated by DevFlow AI (safe identifiers); client-supplied file names are sanitized or rejected.

**Acceptance Criteria:**
REQ-W3.5.5.1 — An artifact exceeding `ARTIFACT_MAX_FILE_BYTES` is rejected with `ARTIFACT_TOO_LARGE`.
REQ-W3.5.5.2 — Each accepted artifact has a SHA-256 hash stored alongside it.
REQ-W3.5.5.3 — Client-supplied file names are sanitized to remove path separators and control characters before storage.

---

## Area W3.6 — Report Validation

**Scope note:** All imported report content is untrusted data. Validation is deterministic and structural. It does not guarantee semantic correctness of the reported work.

**Maps to tasks:** W3.T3, W3.T5, W3.T8, W3.T9, W3.T11, W3.T12

### REQ-W3.6.1 — KiroVerificationReport Schema
WHEN a Kiro verification report is imported,
THEN it MUST be validated against the `KiroVerificationReport` Pydantic v2 schema. The schema MUST define: `schema_version: Literal["1.0"]`, `handoff_id: str` (the string representation of the integer handoff ID), `specification_request_id: int`, `specification_version_id: int`, `artifact_digest: str`, `verification_scope: list[str]` (task IDs covered), `verdict: Literal["PASS","PASS_WITH_FINDINGS","FAIL","INCONCLUSIVE"]`, `findings: list[KiroFinding]` (max 200; unique `finding_id`), `reviewer_notes: str | None` (max 8192 chars after sanitization), `reviewed_at: str` (ISO 8601 UTC).

**Acceptance Criteria:**
REQ-W3.6.1.1 — A report missing `verdict` is rejected before any finding is recorded.
REQ-W3.6.1.2 — An unrecognized `verdict` value is rejected.
REQ-W3.6.1.3 — `artifact_digest` mismatch against the handoff record returns `DIGEST_MISMATCH`.
REQ-W3.6.1.4 — Duplicate `finding_id` values are rejected with `DUPLICATE_FINDING_IDS`.
REQ-W3.6.1.5 — All validation is deterministic.

### REQ-W3.6.2 — CodexImplementationReport Schema
WHEN a Codex implementation report is imported,
THEN it MUST be validated against `CodexImplementationReport`. The schema MUST define: `schema_version: Literal["1.0"]`, `execution_attempt_id: str`, `execution_plan_id: int`, `specification_version_id: int`, `artifact_digest: str`, `authorized_task_ids: list[str]` (from the execution plan), `repo_identity: str`, `baseline_commit: str`, `execution_mode: Literal["mock","manual","real"]`, `verdict: Literal["PASS","PASS_WITH_WARNINGS","FAIL","INCOMPLETE"]`, `task_results: list[TaskResult]` (all authorized tasks MUST be present), `changed_files: list[str]` (max 1000), `test_summary: TestSummary | None`, `artifact_refs: list[str]` (max 100), `completed_at: str` (ISO 8601 UTC).

**Acceptance Criteria:**
REQ-W3.6.2.1 — An unrecognized `verdict` is rejected.
REQ-W3.6.2.2 — A `TaskResult` with a task ID not in `authorized_task_ids` is rejected with `UNAUTHORIZED_TASK_IN_REPORT`.
REQ-W3.6.2.3 — Duplicate task IDs in `task_results` are rejected with `DUPLICATE_TASK_RESULTS`.
REQ-W3.6.2.4 — `execution_mode = "real"` when the execution was `mock` or `manual` is rejected with `EXECUTION_MODE_MISMATCH`.
REQ-W3.6.2.5 — `execution_mode = "mock"` when the execution was `manual` is rejected with `EXECUTION_MODE_MISMATCH`.

### REQ-W3.6.3 — Task Coverage Requirements
WHEN a Codex implementation report is validated,
THEN all authorized task IDs MUST appear in `task_results`. An empty `task_results` list or a list missing any authorized task ID prevents a `PASS` or `PASS_WITH_WARNINGS` verdict. A task result with `status = "failed"` or `status = "partial"` while `verdict = "PASS"` is a contradictory verdict and MUST be rejected with `CONTRADICTORY_VERDICT`.

**Acceptance Criteria:**
REQ-W3.6.3.1 — A report with `task_results` missing any authorized task ID is rejected with `MISSING_TASK_RESULTS`.
REQ-W3.6.3.2 — An empty `task_results` list prevents `PASS` or `PASS_WITH_WARNINGS`.
REQ-W3.6.3.3 — A failed task with claimed `verdict = "PASS"` is rejected with `CONTRADICTORY_VERDICT`.
REQ-W3.6.3.4 — Test count consistency: if `test_summary.passed + test_summary.failed + test_summary.skipped ≠ test_summary.total`, the report is rejected with `INCONSISTENT_TEST_COUNTS`.
REQ-W3.6.3.5 — Coverage percent outside `[0.0, 100.0]` is rejected.

### REQ-W3.6.4 — DevFlow AI Independent Verification
WHEN any report is accepted,
THEN DevFlow AI MUST record its own independent verification result separately from the report's self-asserted verdict. The DevFlow verification result covers: schema validity, identity binding, digest match, task coverage, execution mode match, and timestamp validity. The report's self-reported verdict is recorded as `self_reported_verdict` and NOT treated as proof that work was completed.

**Acceptance Criteria:**
REQ-W3.6.4.1 — The validation result distinguishes `devflow_verification_passed` from `self_reported_verdict`.
REQ-W3.6.4.2 — A report asserting `verdict = "PASS"` but failing any DevFlow check does not set `devflow_verification_passed = true`.
REQ-W3.6.4.3 — Stale reports (identical to a previously validated report for the same attempt) are rejected with `REPORT_ALREADY_SUBMITTED`.

### REQ-W3.6.5 — Report Content Security
WHEN string fields in an imported report are processed,
THEN control characters (`\x00–\x08`, `\x0b–\x0c`, `\x0e–\x1f`) MUST be stripped. Fields exceeding `REPORT_FIELD_MAX_BYTES` (default 32768) MUST be truncated with a truncation marker. No field MUST be passed to `eval()`, `exec()`, `subprocess`, or any equivalent. When a prompt injection pattern is detected (content matching patterns indicating instruction override attempts), an audit event MUST be logged with the field name but NOT the raw content. Sanitization and delimiters reduce injection risk but do not eliminate it; they are defense-in-depth.

**Acceptance Criteria:**
REQ-W3.6.5.1 — Fields with null bytes are sanitized before storage.
REQ-W3.6.5.2 — Fields exceeding `REPORT_FIELD_MAX_BYTES` are truncated.
REQ-W3.6.5.3 — An audit event is logged when injection patterns are detected; it contains the field name but not the raw value.

---

## Area W3.7 — Execution Lifecycle

**Maps to tasks:** W3.T2, W3.T4, W3.T7, W3.T10, W3.T11, W3.T12

### REQ-W3.7.1 — State Machine (Authoritative)
The Wave 3 execution lifecycle uses two separate state machines: one for Kiro handoffs (`handoff_records`) and one for execution attempts (`execution_attempts`). These are additive; they do not modify the Wave 1 `WorkflowStepRun` state machine.

**Handoff states:** `handoff_created` → `awaiting_report` → `report_submitted` → `report_validated` | `failed`. Terminal: `report_validated`, `failed`, `expired`, `cancelled`, `correction_requested`.

**Execution attempt states:** `plan_prepared` → `awaiting_impl_authorization` → `authorization_granted` → `dispatching` → [`mock_execution` | `manual_handoff` | `real_execution_blocked`] → `awaiting_impl_report` (manual only) → `impl_report_submitted` (manual only) → `validating_report` → `completed` | `failed` | `cancelled` | `timeout` | `interrupted`.

**Acceptance Criteria:**
REQ-W3.7.1.1 — Attempting a state transition not in the directed graphs above returns `INVALID_STATE_TRANSITION`.
REQ-W3.7.1.2 — Each transition is recorded in the audit log with `from_state`, `to_state`, and timestamp.
REQ-W3.7.1.3 — All states are representable in the database CHECK constraints.

### REQ-W3.7.2 — CAS State Transitions
WHEN a state transition is performed,
THEN it MUST use CAS (compare-and-swap) on the current state within a `BEGIN IMMEDIATE` SQLite transaction. Concurrent transitions for the same record: the second concurrent transition receives `CONCURRENT_TRANSITION_CONFLICT`.

**Acceptance Criteria:**
REQ-W3.7.2.1 — A CAS transition that finds the state has already changed returns `CONCURRENT_TRANSITION_CONFLICT`.
REQ-W3.7.2.2 — All transitions use `BEGIN IMMEDIATE` transactions.

### REQ-W3.7.3 — Durable Human-Wait State
WHEN a Kiro handoff is in `awaiting_report` state,
THEN no active executor coroutine, task, or lease MUST be held. The durable wait is recorded only in `handoff_records`. On the next relevant user action (report submission or cancellation), the system resumes by reading the persisted state. This applies to both handoff and manual implementation waiting states.

**Acceptance Criteria:**
REQ-W3.7.3.1 — After transitioning to `awaiting_report`, no background task holds a reference to the handoff coroutine.
REQ-W3.7.3.2 — After an application restart, `awaiting_report` handoffs are still processable (report can be submitted, expiry checked).
REQ-W3.7.3.3 — `awaiting_impl_report` execution attempts survive restart and accept report import.
REQ-W3.7.3.4 — The wait reason is stored as a discriminated field distinguishing `waiting_for_kiro_report` from `waiting_for_impl_authorization`; the two states cannot be confused by the resumption logic.
REQ-W3.7.3.5 — Resumption after restart re-reads attempt status from the database; already-resumed attempts are not re-dispatched.
REQ-W3.7.3.6 — `real_when_isolated` NEVER auto-dispatches even after isolation prerequisites might theoretically become available; the user must explicitly request a new attempt.

### REQ-W3.7.6 — Durable Wait Mechanism Contract (R02)
WHEN a Kiro handoff enters `awaiting_report` state,
THEN a typed waiting_human result MUST fence and atomically transition RUNNING to WAITING_HUMAN, persist the report wait owner and attempt token, clear the lease and audit. Kiro and manual Codex reports have distinct reasons. Validate token, attempt, digest, ownership and expiry on acceptance, and return the persisted result for identical replay. Cancellation and expiry settle the wait atomically. Startup preserves valid waits and never redispatches a coding agent. Report waits create no approval row. Independent implementation authorization continues to use the existing WAITING_APPROVAL gate.

**Acceptance Criteria:**
REQ-W3.7.6.1 — Wait reason is persisted in the domain record (`handoff_records.status`) and is distinguishable from approval-waiting by `error.code` on the `StepExecutionResult`.
REQ-W3.7.6.2 — The step lease is released before the handoff enters `awaiting_report`; no active coroutine holds the step.
REQ-W3.7.6.3 — Recovery (`CodingAgentRecoveryService.recover()`) does not re-dispatch `awaiting_report` or `awaiting_impl_report` attempts.
REQ-W3.7.6.4 — No `ApprovalRequest` row is created for report-waiting; only the `implementation_authorization` gate creates `ApprovalRequest` rows.
REQ-W3.7.6.5 — Idempotent resumption: a second attempt to resume an already-resumed attempt returns the current status without re-dispatching.

### REQ-W3.7.4 — Recovery on Restart
WHEN the application starts,
THEN the `CodingAgentRecoveryService` MUST: (a) find `execution_attempts` in `dispatching` or `mock_execution` states and transition them to `interrupted`; (b) find handoffs in `awaiting_report` where `awaiting_since + KIRO_HANDOFF_EXPIRY_SECONDS < now()` and transition them to `expired`; (c) emit an audit event for each. The recovery service MUST NOT re-dispatch any execution or invoke any AI provider, Kiro, or Codex CLI.

**Acceptance Criteria:**
REQ-W3.7.4.1 — Attempts in `dispatching` or `mock_execution` are transitioned to `interrupted` on restart.
REQ-W3.7.4.2 — `awaiting_report` and `awaiting_impl_report` attempts are NOT interrupted on restart (they represent durable human-wait states).
REQ-W3.7.4.3 — No execution is auto-dispatched after recovery.
REQ-W3.7.4.4 — Recovery is idempotent.

### REQ-W3.7.5 — Timeout and Cancellation
WHEN an execution times out (`deadline_at` passed),
THEN the recovery service transitions it to `timeout`, records `timed_out_at`, emits an audit event. When a user cancels, the execution transitions to `cancelled`, records `cancelled_at`, and the authorization is marked cancelled (not consumed). A new attempt requires a new authorization.

**Acceptance Criteria:**
REQ-W3.7.5.1 — An attempt past `deadline_at` and not terminal is transitioned to `timeout`.
REQ-W3.7.5.2 — Cancellation transitions to `cancelled`; authorization record reflects cancellation.
REQ-W3.7.5.3 — Cancelling a terminal attempt returns `EXECUTION_NOT_CANCELLABLE`.

---

## Area W3.8 — Isolation Prerequisite Gate

**Maps to tasks:** W3.T1, W3.T11, W3.T12

### REQ-W3.8.1 — Fail-Closed Gate
WHEN any `real_when_isolated` execution is requested,
THEN the `IsolationGate` MUST check all `Settings.ISOLATION_PREREQUISITES` before any other action. The check is fail-closed: an empty list, unrecognized names, or any error all result in `ISOLATION_PREREQUISITES_NOT_MET`. The gate runs before workspace validation, before authorization consumption, and before any subprocess invocation. In Wave 3, all `real_when_isolated` requests MUST result in `real_execution_blocked`.

**Acceptance Criteria:**
REQ-W3.8.1.1 — Empty `ISOLATION_PREREQUISITES` → `not_configured` → real execution blocked.
REQ-W3.8.1.2 — An unrecognized prerequisite name → `prerequisites_not_met`.
REQ-W3.8.1.3 — The gate runs before authorization is consumed; blocked execution does not consume authorization.
REQ-W3.8.1.4 — Tests verify no subprocess is launched when the gate blocks.

### REQ-W3.8.2 — No Config-Flag Bypass
WHEN any boolean configuration flag is set (`CODEX_ISOLATION_OVERRIDE`, `FORCE_REAL_EXECUTION`, or any equivalent),
THEN this MUST NOT satisfy the isolation gate. Config flags may be used for logging or UI display but MUST NOT create a code path in `IsolationGate` that returns `prerequisites_met` based solely on a flag value.

**Acceptance Criteria:**
REQ-W3.8.2.1 — `CODEX_ISOLATION_OVERRIDE=true` with no prerequisites configured still returns `ISOLATION_PREREQUISITES_NOT_MET`.
REQ-W3.8.2.2 — No IsolationGate code path returns `prerequisites_met` from a boolean flag alone.
REQ-W3.8.2.3 — A future configuration addition of a recognized prerequisite name MUST require that name to actually be verified against system state before passing.

### REQ-W3.8.3 — Isolation Status API
WHEN the isolation gate status is queried,
THEN the response MUST include: `isolation_status` (`not_configured`, `prerequisites_not_met`, `prerequisites_met`), `unmet_prerequisites` (list of names), `checked_at` (timestamp).

**Acceptance Criteria:**
REQ-W3.8.3.1 — `GET /api/v1/coding-agent/isolation-status` returns all three fields.
REQ-W3.8.3.2 — With no prerequisites, `isolation_status = "not_configured"`.

---

## Area W3.9 — FastAPI API Contracts

**Maps to tasks:** W3.T8, W3.T12

### REQ-W3.9.1 — Endpoint Set
WHEN Wave 3 is implemented,
THEN the following 20 endpoints MUST be available under `/api/v1/coding-agent/`. All require `actor_id = Settings.LOCAL_OPERATOR_ID`. Endpoints: capabilities, isolation-status, handoff create, handoff get, handoff prompt export, handoff prompt retrieve (idempotent), handoff report submit, handoff validation status, handoff cancel, execution plan create, authorization request (delegates to Wave 1), authorization status, manual prompt export, manual prompt retrieve (idempotent), mock execution, execution status, execution cancel, implementation report submit, artifact retrieve, audit history.

**Acceptance Criteria:**
REQ-W3.9.1.1 — All 20 endpoints appear in the OpenAPI schema exported at startup.
REQ-W3.9.1.2 — An unauthenticated request returns HTTP 403.

### REQ-W3.9.2 — HTTP Status Protocol
WHEN async operations are submitted,
THEN HTTP 202 is returned with a durable record ID and status URL. All status/poll endpoints return HTTP 200 for all states including `failed`. Synchronous errors (validation, auth, forbidden, conflict) use the existing `{"error": ..., "code": ...}` envelope consistent with Wave 2.

**Acceptance Criteria:**
REQ-W3.9.2.1 — Handoff creation returns HTTP 202.
REQ-W3.9.2.2 — Report submission returns HTTP 202.
REQ-W3.9.2.3 — Polling a failed async operation returns HTTP 200 with `error_code` in the body.
REQ-W3.9.2.4 — Synchronous errors use `{"error": ..., "code": ...}`.

### REQ-W3.9.3 — Idempotent Prompt Retrieval
WHEN an already-issued prompt is retrieved,
THEN the system MUST return the stored immutable prompt content without re-consuming authorization and without regenerating timestamps.

**Acceptance Criteria:**
REQ-W3.9.3.1 — `GET /api/v1/coding-agent/handoffs/{id}/prompt` returns the stored prompt text.
REQ-W3.9.3.2 — Repeated calls return identical content.
REQ-W3.9.3.3 — Retrieval does not trigger authorization consumption.

### REQ-W3.9.4 — Capabilities Endpoint
WHEN the capabilities endpoint is called,
THEN the response MUST include: `execution_modes_available`, `isolation_status`, `kiro_integration_type`, `codex_cli_installed` (boolean), `codex_cli_version` (string or null), `codex_cli_capability_status` (`unavailable`, `unverified`, or `verified`).

**Acceptance Criteria:**
REQ-W3.9.4.1 — All required fields are returned.
REQ-W3.9.4.2 — When CLI is not installed, `codex_cli_installed = false`, `codex_cli_version = null`, `codex_cli_capability_status = "unavailable"`.

### REQ-W3.9.5 — Artifact Retrieval Security
WHEN an artifact is retrieved,
THEN its SHA-256 hash MUST be verified before serving. A hash mismatch returns HTTP 409 `ARTIFACT_INTEGRITY_FAILURE`. Artifact retrieval requires the requesting actor to be `LOCAL_OPERATOR_ID`.

**Acceptance Criteria:**
REQ-W3.9.5.1 — `GET /api/v1/coding-agent/artifacts/{id}` streams the artifact as a file download.
REQ-W3.9.5.2 — A tampered artifact returns HTTP 409 `ARTIFACT_INTEGRITY_FAILURE`.

---

## Area W3.10 — Streamlit UI

**Maps to tasks:** W3.T9, W3.T12

### REQ-W3.10.1 — Mode Selection and Isolation Status
WHEN the user opens the coding agent page,
THEN the UI MUST display mode selection (manual/mock/real_execution_blocked), isolation status, and available actions. The UI MUST NOT show a "real execution available" or "real execution succeeded" status unless `isolation_status = "prerequisites_met"` (which cannot occur in Wave 3). The UI accesses data exclusively through the API layer; no direct database access from Streamlit.

**Acceptance Criteria:**
REQ-W3.10.1.1 — When isolation prerequisites are not met, the UI shows `real_execution_blocked` mode as unavailable.
REQ-W3.10.1.2 — The UI never shows a real execution success path in Wave 3.

### REQ-W3.10.2 — Kiro Handoff Screen
WHEN the user is on the Kiro handoff screen,
THEN the UI MUST display: the generated Kiro verification prompt (full text, copyable), an export button (download as `.txt`), the current handoff state, and instructions stating that Kiro is run by the user, not by DevFlow AI.

**Acceptance Criteria:**
REQ-W3.10.2.1 — Prompt text is displayed in a `st.code` block.
REQ-W3.10.2.2 — Export triggers download of the prompt as `.txt`.
REQ-W3.10.2.3 — Instructions clearly state Kiro is run independently by the user.

### REQ-W3.10.3 — Findings and Approval
WHEN findings are displayed,
THEN non-overridable findings have no override UI element. Advisory major findings have an acknowledgment field. Blockers are red, majors orange. The final approval button is not rendered until all non-overridable findings are resolved.

**Acceptance Criteria:**
REQ-W3.10.3.1 — Non-overridable findings show no override/bypass control.
REQ-W3.10.3.2 — Advisory major findings show an acknowledgment field requiring justification text.
REQ-W3.10.3.3 — Final approval button not rendered with unresolved non-overridable findings.

### REQ-W3.10.4 — Implementation Authorization and Audit
WHEN the implementation authorization screen is shown,
THEN it displays the authorization expiry, authorized task IDs, and labels this as distinct from specification approval. The audit timeline shows all Wave 3 events in chronological order with event type, actor, and timestamp.

**Acceptance Criteria:**
REQ-W3.10.4.1 — Authorization expiry is displayed.
REQ-W3.10.4.2 — The screen is labeled "Implementation Authorization" distinct from "Specification Approval".
REQ-W3.10.4.3 — All Wave 3 audit events appear in chronological order.

---

## Security Requirements

### REQ-W3.S.1 — Prompt Injection Defense
All imported report content MUST be sanitized (control chars stripped, length capped) before display or storage. No field from an imported report is passed to an AI provider prompt without hard delimiter wrapping. These measures reduce injection risk but are defense-in-depth; they do not eliminate prompt injection risk.

### REQ-W3.S.2 — Report Field Sanitization
String fields from imported reports displayed in the UI MUST be sanitized to remove control characters and HTML entities before rendering.

### REQ-W3.S.3 — No Instruction Execution
No field within an imported report is treated as an instruction. The system does not honor directives embedded in report fields (e.g., fields claiming to override instructions or approve specifications).

### REQ-W3.S.4 — No Wildcard Task Authorization
Wildcard `"*"` task IDs are rejected in all contexts (execution input, report validation, authorization binding).

### REQ-W3.S.5 — No Unrestricted Subprocess
The only subprocess that may be spawned is the Codex CLI binary (Wave 6 only). No unrestricted shell, OS command, or process invocation is permitted from any Wave 3 code path. Git config reading in Wave 3 uses file parsing, not subprocess execution.

### REQ-W3.S.6 — No Automatic Cloud Fallback
If mock execution or blocked real execution fails, the system transitions to `failed`. No automatic retry to a cloud provider is performed.

### REQ-W3.S.7 — No Automatic GitHub Operations
Wave 3 introduces no GitHub write, commit, push, merge, or PR creation path. All GitHub write operations are deferred to Wave 4.

### REQ-W3.S.8 — Offline Credential Scanning Preserved
The TruffleHog 3.90.0 offline Docker scan policy from Wave 2 continues to apply. No scanning policy is bypassed or relaxed.

### REQ-W3.S.9 — No Credentials in Artifacts or Logs
No credential (API key, token, password) appears in any artifact, log, audit event, or report. All credential fields use `SecretStr`. Credential patterns are redacted from stored logs.

### REQ-W3.S.10 — Trust Boundaries
The trust boundary is: DevFlow AI process (trusted) | user-imported reports (untrusted) | workspace files (untrusted). Every crossing from untrusted to trusted requires schema validation, field sanitization, and explicit field-by-field transfer (no `**untrusted_dict`).

---

## Threat Analysis

| Threat | Mitigation | Requirement |
|---|---|---|
| Prompt injection in imported report | Sanitization + hard delimiters (defense-in-depth, not elimination) | REQ-W3.S.1, REQ-W3.6.5 |
| Malicious report claiming PASS | Deterministic independent validation; self-reported verdict ≠ DevFlow verification | REQ-W3.6.4, REQ-W3.1.5 |
| Arbitrary command via workspace path | Canonical path check; approved roots; intermediate component inspection | REQ-W3.5.1, REQ-W3.5.2 |
| Symlink escape | Pre-resolve component check; symlink policy | REQ-W3.5.2 |
| UNC / device path attack (Windows) | Explicit UNC/device-path rejection | REQ-W3.5.1.2 |
| Credential leakage | `SecretStr` on all credential fields; log sanitization | REQ-W3.S.9 |
| Approval replay | Single-use CAS via Wave 1 `consume_atomic` | REQ-W3.4.2 |
| Stale execution input | `canonical_hash` computed server-side from persisted inputs | REQ-W3.4.4, REQ-W3.3.4 |
| Duplicate dispatch | Wave 1 step CAS (`READY → RUNNING`) | REQ-W3.4.5 |
| Config flag isolation bypass | IsolationGate checks system state, not flags | REQ-W3.8.2 |
| Non-overridable finding bypass | No override UI element for security/integrity findings | REQ-W3.2.5 |
| Startup interruption | Recovery service marks dispatching → interrupted; no auto-redispatch | REQ-W3.7.4 |
| Authorization cross-run | `consume_atomic` checks `workflow_step_run_id` match | REQ-W3.4.6 |
| Client-supplied hash injection | Server recomputes hash from persisted plan; client hash ignored | REQ-W3.3.1.5 |
| Partial task result claiming PASS | Missing task results prevent PASS verdict | REQ-W3.6.3 |
| TOCTOU in workspace validation | Component check before resolve; documented residual risk | REQ-W3.5.2.3, REQ-W3.5.3.3 |
