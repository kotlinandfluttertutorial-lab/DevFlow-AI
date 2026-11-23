# Phase 2 Wave 3 — Specification Resolution v1.0.1

**Generated:** 2026-11-23
**Resolves:** ChatGPT review findings F01–F10 against Wave 3 spec v1.0.0
**Output:** Wave 3 spec v1.0.1

---

## Resolution Matrix

### F01 — First-Class Manual Execution Mode

| Field | Value |
|---|---|
| **Finding** | `blocked` defined as a requested execution mode; manual mode not first-class; `awaiting_impl_report` state missing |
| **Affected requirements** | REQ-W3.1.1, REQ-W3.3.1–W3.3.3 (v1.0.0) |
| **Affected design sections** | §4.1 CodexExecutionInput; §9 lifecycle states |
| **Affected tasks** | W3.T4, W3.T12 |
| **Correction** | Three requested modes: `manual`, `mock`, `real_when_isolated`. `real_execution_blocked` is an outcome state, not a mode. Manual mode has complete lifecycle: plan → authorization → prompt export (consumes authorization via `dispatch_protected_txn`) → `awaiting_impl_report` → `impl_report_submitted` → `validating_report` → `completed`/`failed`. Added REQ-W3.1.2–W3.1.6. Added `awaiting_impl_report` and `impl_report_submitted` to execution attempt state machine. Added `manual_handoff` state. |
| **Acceptance evidence** | W3.T12.1–W3.T12.6; REQ-W3.1.3.1 (authorization consumed at first export); REQ-W3.1.4.3 (no subprocess in manual); execution attempt CHECK constraint includes new states |
| **Remaining limitation** | Manual external execution is outside DevFlow AI OS isolation guarantees (documented) |
| **Verdict** | RESOLVED |

---

### F02 — Complete Authorization Binding

| Field | Value |
|---|---|
| **Finding** | Spec proposed calling `request_bound_approval(operation, parameters_hash)` — but actual Wave 1 source signature is `request_bound_approval(run_id, step_run_id)` with no caller-supplied hash. Spec also had a global uniqueness constraint on `canonical_execution_input_hash` which prevents legitimate retries. |
| **Affected requirements** | REQ-W3.4.1–W3.4.5 (v1.0.0) |
| **Affected design sections** | §8 implementation authorization |
| **Affected tasks** | W3.T7 |
| **Correction** | Corrected to use actual Wave 1 interfaces: `request_bound_approval(run_id, step_run_id)` derives `parameters_hash` from `canonical_hash(step.inputs_json)`. Binding payload embedded in `step.inputs_json` (design.md §8.3) includes all 10 required fields. `dispatch_protected_txn` is the single consumption path. Global hash uniqueness replaced with scoped idempotency: `UNIQUE(workflow_run_id, workflow_step_run_id)` in `execution_plans`; same step + same hash returns existing plan. Added REQ-W3.4.6 (complete atomic validation). Manual mode consumes at first export; subsequent prompt retrievals are idempotent reads. Added REQ-W3.1.3.2 (re-read does not re-consume). |
| **Acceptance evidence** | W3.T7.3–W3.T7.9; W3.T12.3, W3.T12.12, W3.T12.13; `execution_plans` UNIQUE constraint in migration 010; design.md §8.1 showing actual source interface |
| **Remaining limitation** | The `implementation_authorization` operation must be declared in `WorkflowTemplate.StepDefinition.protected_operation` for each workflow step — this requires the operator to configure the step template correctly |
| **Verdict** | RESOLVED |

---

### F03 — Report Validation Correctness

| Field | Value |
|---|---|
| **Finding** | Report binding fields incomplete; empty/partial task results not blocking PASS; no contradictory-verdict check; no test-count consistency check; no coverage-range check; no duplicate-task-result check; no replay check; self-reported verdict conflated with DevFlow verification |
| **Affected requirements** | REQ-W3.6.1–W3.6.4 (v1.0.0) |
| **Affected design sections** | §4.4, §7.1, §7.2 |
| **Affected tasks** | W3.T3, W3.T5 |
| **Correction** | `CodexImplementationReport` now includes `execution_plan_id`, `specification_version_id`, `artifact_digest`, `authorized_task_ids`, `repo_identity`, `baseline_commit`, `execution_mode`. Added checks: all authorized tasks must be present (MISSING_TASK_RESULTS); duplicate task IDs (DUPLICATE_TASK_RESULTS); contradictory verdict (CONTRADICTORY_VERDICT); test count consistency (INCONSISTENT_TEST_COUNTS); coverage range (INVALID_COVERAGE_RANGE); replay (REPORT_ALREADY_SUBMITTED). Added REQ-W3.1.5 separating DevFlow verification from self-reported verdict. `KiroVerificationReport` adds `specification_request_id` and `verification_scope`. Validation pipeline extended to 14 (Kiro) and 19 (Codex) steps. |
| **Acceptance evidence** | W3.T12.14–W3.T12.18; W3.T5.1–W3.T5.10; REQ-W3.6.3–W3.6.4 |
| **Remaining limitation** | Deterministic validation is structural/integrity-based, not semantic; it cannot verify that code quality or test correctness claims are true |
| **Verdict** | RESOLVED |

---

### F04 — Workspace Validation Security

| Field | Value |
|---|---|
| **Finding** | Algorithm called `Path.resolve()` then checked `is_symlink()` — incorrect because `resolve()` follows symlinks so `is_symlink()` after resolve always returns False. String prefix used for containment instead of `is_relative_to()`. UNC/device-path policy not defined. TOCTOU risk not documented. Git config reading used subprocess. |
| **Affected requirements** | REQ-W3.5.1–W3.5.4 (v1.0.0) |
| **Affected design sections** | §6.1–6.2 WorkspaceValidator |
| **Affected tasks** | W3.T1 |
| **Correction** | Algorithm corrected: intermediate component inspection precedes `Path.resolve()` (design.md §6.1). `is_relative_to()` used for containment. UNC and device paths rejected on Windows (REQ-W3.5.1.2). Platform-specific case sensitivity (REQ-W3.5.3). Git identity reads `.git/config` file without subprocess (design.md §6.2). `.git` file (worktree) supported. TOCTOU risk documented as residual (REQ-W3.5.3.3). |
| **Acceptance evidence** | W3.T1.5 (intermediate symlink check), W3.T1.7 (UNC rejection), W3.T1.8 (`is_relative_to`), W3.T1.9 (no subprocess); W3.T12.21 (intermediate symlink), W3.T12.22 (junction) |
| **Remaining limitation** | TOCTOU risk persists as a residual application-level risk; OS-level isolation (Wave 6) is the definitive protection |
| **Verdict** | RESOLVED |

---

### F05 — Correct Specification Approval Ordering

| Field | Value |
|---|---|
| **Finding** | v1.0.0 had `specification approved (Wave 2)` before Kiro verification at stage 4 and again post-verification at stage 9 — a duplicate mandatory approval sequence. Final approval should come after Kiro verification, not before it. |
| **Affected requirements** | REQ-W3.1.1 (v1.0.0) |
| **Affected design sections** | §3 handoff state machine, §9 lifecycle |
| **Affected tasks** | W3.T2 |
| **Correction** | REQ-W3.1.1 rewritten with 17 ordered stages. Stage 3 is optional preliminary AI review (permitted before Kiro). Stage 10 is final human specification approval (required after Kiro validation at stage 8). Stage 10 must not precede stage 8. Added REQ-W3.1.1.3 (system rejects final approval before Kiro validation recorded). Design.md §3.1 shows `handoff_created` state now requires spec version in `approved` or `ready_for_approval` state (allowing handoff creation before final approval). |
| **Acceptance evidence** | REQ-W3.1.1.3; W3.T12.1 (all 17 stages in correct order); W3.T2.1 (spec version state check at handoff creation) |
| **Remaining limitation** | Integration with Wave 2 approval states requires that Wave 2's `SpecificationApprovalService` eligibility check is not bypassed when creating a handoff |
| **Verdict** | RESOLVED |

---

### F06 — Durable Workflow Waiting and Resumption

| Field | Value |
|---|---|
| **Finding** | Spec did not define how the executor handles multi-hour/day waits without holding a lease; no explicit non-running state design; `KiroHandoffExecutor.execute()` behavior during wait not specified |
| **Affected requirements** | REQ-W3.7.3 (v1.0.0) |
| **Affected design sections** | §3 KiroHandoffExecutor, §9.4 recovery |
| **Affected tasks** | W3.T2, W3.T10 |
| **Correction** | `awaiting_report` and `awaiting_impl_report` are non-running durable wait states. `KiroHandoffExecutor.execute()` returns a result mapping to `WAITING_APPROVAL` step state. No executor coroutine is held. No lease is active. The Wave 1 `WAITING_APPROVAL` step state is reused (no new step state added). Resumption occurs via fresh HTTP requests. Recovery explicitly preserves `awaiting_report` and `awaiting_impl_report` (does not interrupt them). Added REQ-W3.2.2.4 (no lease held during await). Added REQ-W3.7.3 (durable wait spec). Added REQ-W3.7.4.2 (recovery does not interrupt durable waits). |
| **Acceptance evidence** | W3.T12.23 (no active worker lease), W3.T12.24 (restart while waiting), W3.T10.5 (recovery does not interrupt `awaiting_impl_report` or `awaiting_report`) |
| **Remaining limitation** | The exact return value from `KiroHandoffExecutor.execute()` that maps to `WAITING_APPROVAL` must be verified against the actual Wave 1 `StepExecutionResult` schema during implementation |
| **Verdict** | RESOLVED |

---

### F07 — Persistence and Artifact Ownership

| Field | Value |
|---|---|
| **Finding** | v1.0.0 `report_artifacts` required `execution_attempt_id`, but Kiro verification occurs before execution attempts exist. Single `report_artifacts` table mixed Kiro-phase and execution-phase artifacts. |
| **Affected requirements** | REQ-W3.5.5 (v1.0.0); design §10 |
| **Affected design sections** | §10 data model |
| **Affected tasks** | W3.T6 |
| **Correction** | Split into two ownership tables: `handoff_artifacts` (FK → `handoff_records`) for Kiro prompts, Kiro reports, and manual Codex prompts; `execution_artifacts` (FK → `execution_attempts`) for Codex reports, diffs, test reports, logs. Total tables: 7 (was 6). Storage keys are DevFlow-generated safe identifiers; client-supplied names are sanitized or rejected. Unique constraint `(handoff_record_id, finding_id)` in `handoff_findings`. `execution_plans` uses scoped uniqueness `(workflow_run_id, workflow_step_run_id)`. Added `self_reported_verdict` and `devflow_verification_passed` columns to `execution_attempts`. |
| **Acceptance evidence** | W3.T6.6 (scoped uniqueness), W3.T6.7 (finding uniqueness), W3.T6.1 (7 tables); W3.T12.19 (Kiro report in handoff_artifacts) |
| **Remaining limitation** | Raw report content in `handoff_artifacts` must be protected from ordinary API exposure (artifact retrieval requires actor authorization check) |
| **Verdict** | RESOLVED |

---

### F08 — Finding Override Policy

| Field | Value |
|---|---|
| **Finding** | REQ-W3.2.5 contradictorily stated blockers prevent approval but a "user override with recorded justification" is permitted — allowing security/integrity blockers to be overridden by any justification |
| **Affected requirements** | REQ-W3.2.5 (v1.0.0) |
| **Affected design sections** | §14.3 non-overridable finding categories |
| **Affected tasks** | W3.T3, W3.T9 |
| **Correction** | REQ-W3.2.5 rewritten with explicit non-overridable category list (security, integrity_failure, digest_mismatch, invalid_authorization, isolation_failure, invalid_report_schema, missing_mandatory_evidence). These categories have no override UI element. Advisory acknowledgment (minimum 20-character justification, audit-logged) permitted only for major findings without security/integrity sub-category and for minor/informational. `handoff_findings.is_non_overridable` column tracks this. Design.md §14.3 documents the full list. UI: no override button for non-overridable findings. |
| **Acceptance evidence** | REQ-W3.2.5.1–W3.2.5.4; W3.T3.6; W3.T12.27; W3.T9.3 |
| **Remaining limitation** | The finding category taxonomy comes from Kiro (the external reviewer). DevFlow AI maps finding metadata to non-overridable categories based on a declared vocabulary; a Kiro reviewer who does not use the declared categories will not trigger non-overridable enforcement |
| **Verdict** | RESOLVED |

---

### F09 — Kiro and Codex Capability Claims

| Field | Value |
|---|---|
| **Finding** | Spec claimed "no stable programmatic Kiro API exists" (categorical claim); Codex CLI flags documented as verified invocation contract but were from docs, not installed CLI |
| **Affected requirements** | Capability Investigation section (v1.0.0) |
| **Affected design sections** | ADR-W3-02, ADR-W3-03; §4 CodexCLIExecutor |
| **Affected tasks** | W3.T4 |
| **Correction** | Capability section rewritten: Kiro section now states "guided handoff is the **selected implementation scope**" rather than claiming the API does not exist. Codex CLI section now states "capability is **unavailable / unverified** on this machine"; abstract contract retained; exact flags explicitly marked as "not verified against installed CLI"; deferred to Wave 6. Design.md §4.6 "Abstract Real Execution Contract" explicitly notes flags are abstract and unverified. ADR-W3-02 and ADR-W3-03 updated. |
| **Acceptance evidence** | Capability Investigation section v1.0.1; design.md §4.6; REQ-W3.3.5.1 (abstract contract schema); ADR-W3-03 |
| **Remaining limitation** | Until the CLI is installed at Wave 6, the exact invocation flags remain unverified |
| **Verdict** | RESOLVED |

---

### F10 — API and UI Consistency

| Field | Value |
|---|---|
| **Finding** | 17-endpoint count was fixed but incomplete for the corrected lifecycle; manual-mode-specific endpoints (manual prompt retrieve, impl report submit) were missing; authorization request endpoint was not clearly distinguished from Wave 1 |
| **Affected requirements** | REQ-W3.9.1 (v1.0.0) |
| **Affected design sections** | §11 route table |
| **Affected tasks** | W3.T8 |
| **Correction** | Route table updated to 20 endpoints (design.md §11): added `/executions/{id}/prompt` (idempotent GET), `/executions/{id}/submit-impl-report`, `/executions/{id}/report-validation-status`, separated `handoff/{id}/prompt` (GET, idempotent) from `handoff/{id}/export-prompt` (POST, starts export). Authorization request endpoint (POST `/execution-plans/{id}/request-authorization`) delegates to Wave 1 `request_bound_approval`; it does NOT create a new approval record. Wave 1 approve endpoint reused for granting authorization. UI: Streamlit uses API layer only; no direct DB access; non-overridable findings have no override button; self-reported and DevFlow verification shown separately. |
| **Acceptance evidence** | REQ-W3.9.1.1 (20 endpoints); W3.T8.4 (idempotent prompt GET); W3.T12.3 (re-read does not consume); W3.T9.8 (self-reported verdict separate) |
| **Remaining limitation** | The 20-endpoint count will be verified against the OpenAPI schema export during W3.T8 implementation |
| **Verdict** | RESOLVED |

---

## Counts Recalculated

| Metric | v1.0.0 | v1.0.1 |
|---|---|---|
| Requirement areas | 10 + security | 10 + security (unchanged) |
| Named requirements | ~60 | ~65 (REQ-W3.1.2–W3.1.6 added; REQ-W3.2.5 split; REQ-W3.4.6 added; REQ-W3.6.3 split; REQ-W3.6.4–W3.6.5 revised) |
| Security requirements | REQ-W3.S.1–W3.S.10 | REQ-W3.S.1–W3.S.10 (unchanged) |
| Tasks | 12 | 12 (unchanged) |
| Acceptance criteria total | 92 | 110 (W3.T12 expanded from 15 to 28 sub-criteria; task ACs expanded) |
| Database tables (migration 010) | 6 | 7 (handoff_artifacts split from report_artifacts) |
| API endpoints | 17 | 20 |
| ADRs | 10 | 10 (all revised) |
| State machines | 2 | 2 (execution attempt state machine expanded with manual states) |

---

## Final Validation Summary

| Check | Result |
|---|---|
| Requirements-to-design traceability | PASS — all req areas map to design sections |
| Requirements-to-task traceability | PASS — all req areas in traceability table |
| Task-to-test traceability | PASS — all tasks have acceptance criteria; W3.T12 covers all areas |
| State-machine consistency | PASS — handoff and execution attempt state machines are distinct; Wave 1 step states reused |
| Schema-to-state consistency | PASS — all states representable in DB CHECK constraints |
| API-to-lifecycle consistency | PASS — 20 endpoints cover all lifecycle transitions |
| Approval binding vs Wave 1 source | PASS — `request_bound_approval(run_id, step_run_id)` and `dispatch_protected_txn` as actually implemented |
| Canonical digest compatibility | PASS — `canonical_hash(step.inputs_json)` consistent with Wave 2 `text_hash()` approach |
| Migration dependency | PASS — 010 chains from 009; no existing tables altered |
| Security invariants | PASS — no real execution; no config bypass; non-overridable findings; correct workspace algorithm |
| Task dependencies | PASS — Group A → Group B → Group C → Group D; W3.T5 depends on W3.T3, W3.T4, W3.T6 |
| Security workflow CI | NOT VERIFIED on GitHub (infrastructure issue); offline scan PASS |
