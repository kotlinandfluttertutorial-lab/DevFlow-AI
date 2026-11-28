# Phase 2 Wave 3 — ChatGPT Verification Index (v1.0.1 → v1.0.4)

**Package:** Phase 2 Wave 3 Specification Review — v1.0.4 (originally reviewed at v1.0.1; R01–R05 corrections applied through v1.0.4)
**Date:** 2026-11-23 (review); 2026-11-24 (v1.0.4 freeze)
**Branch:** `feature/phase2-wave3-coding-agent-integration`
**Git baseline:** `ee57dc7` (Wave 2 merged, develop HEAD)
**Supersedes:** v1.0.0 verification index (same date)

---

## What This Package Is

This is the corrected Wave 3 specification following a ChatGPT review of v1.0.0. All 10 review findings (F01–F10) have been addressed. The specification now covers manual execution as a first-class mode, corrected authorization binding against the actual Wave 1 source, corrected workspace validation algorithm, correct approval ordering (Kiro before final spec approval), durable human-wait state design, corrected artifact ownership model, non-overridable finding categories, and updated capability claims.

**This is specification only. No production code has been implemented.**

---

## Where to Start

1. `docs/reports/phase2-wave3-spec-resolution-v1.0.1.md` — Resolution matrix for all F01–F10 findings. Read this first to understand what changed.
2. `docs/reports/phase2-wave3-spec-readiness.md` — High-level architecture summary and readiness verdict.
3. `.kiro/specs/phase2-wave3-coding-agent-integration/requirements.md` — Full requirements (v1.0.1).
4. `.kiro/specs/phase2-wave3-coding-agent-integration/design.md` — Architecture and ADRs (v1.0.1).
5. `.kiro/specs/phase2-wave3-coding-agent-integration/tasks.md` — 12 tasks; 28-criterion acceptance suite (v1.0.1).
6. `docs/reports/phase2-wave2-security-closure-resolution.md` — Security CI failure investigation and offline scan results.

---

## File Index

| File | Purpose |
|---|---|
| `.kiro/specs/phase2-wave3-coding-agent-integration/requirements.md` | ~65 requirements, 10 areas + 10 security; corrected workflow ordering, manual mode, non-overridable findings, corrected report binding (v1.0.4) |
| `.kiro/specs/phase2-wave3-coding-agent-integration/design.md` | Architecture; corrected WorkspaceValidator algorithm; corrected authorization binding; 9-table data model; 20 API routes; 10 ADRs (v1.0.4) |
| `.kiro/specs/phase2-wave3-coding-agent-integration/tasks.md` | 12 tasks; W3.T12 with 28 acceptance sub-criteria (v1.0.4) |
| `docs/reports/phase2-wave3-spec-resolution-v1.0.1.md` | One-to-one resolution matrix for F01–F10 |
| `docs/reports/phase2-wave3-spec-readiness.md` | Readiness assessment (v1.0.1) |
| `docs/reports/phase2-wave3-chatgpt-verification.md` | This file — review index |
| `docs/reports/phase2-wave2-security-closure-resolution.md` | Security CI investigation; offline scan evidence |
| `docs/planning/phase2-roadmap.md` | Phase 2 six-wave roadmap (reference only) |

---

## What Changed from v1.0.0

### F01 — Manual mode
- `manual` is now a first-class mode (alongside `mock` and `real_when_isolated`).
- `real_execution_blocked` is an outcome state, not a mode.
- Manual mode has a complete lifecycle: plan → authorization (consumed at first export) → `awaiting_impl_report` → validation → `completed`.
- Added REQ-W3.1.2–W3.1.6; execution attempt state machine expanded.

### F02 — Authorization binding
- Corrected to use actual Wave 1 `request_bound_approval(run_id, step_run_id)` (not a caller-supplied hash).
- Binding payload embedded in `step.inputs_json`; `parameters_hash = canonical_hash(step.inputs_json)`.
- Global hash uniqueness constraint replaced with scoped `UNIQUE(workflow_run_id, workflow_step_run_id)`.
- Added REQ-W3.4.6 (complete atomic validation conditions).

### F03 — Report validation
- Both report schemas now include full binding fields (plan_id, spec_version, artifact_digest, authorized_task_ids, etc.).
- Added checks: MISSING_TASK_RESULTS, DUPLICATE_TASK_RESULTS, CONTRADICTORY_VERDICT, INCONSISTENT_TEST_COUNTS, REPORT_ALREADY_SUBMITTED.
- DevFlow verification result (`devflow_verification_passed`) separated from self-reported verdict.

### F04 — Workspace validation
- Algorithm corrected: intermediate symlink component check precedes `Path.resolve()`.
- `is_relative_to()` replaces string prefix matching.
- UNC/device paths explicitly rejected on Windows.
- TOCTOU risk documented as residual.
- Git config reading uses file parsing, not subprocess.

### F05 — Approval ordering
- Correct 17-stage workflow: Kiro verification (stages 4–9) precedes final spec approval (stage 10).
- System rejects final approval before Kiro validation recorded.

### F06 — Durable wait
- `awaiting_report` and `awaiting_impl_report` are non-running durable wait states.
- No executor coroutine or lease held during wait.
- Wave 1 `WAITING_APPROVAL` step state reused (no new step states added).
- Recovery explicitly preserves durable wait states; only interrupts active execution states.

### F07 — Artifact ownership
- Split into `handoff_artifacts` (Kiro prompts/reports; FK → handoff_records) and `execution_artifacts` (impl artifacts; FK → execution_attempts).
- 7 tables total (was 6).
- Unique constraint on `(handoff_record_id, finding_id)` in `handoff_findings`.

### F08 — Finding override
- 7 non-overridable finding categories defined (security, integrity_failure, digest_mismatch, etc.).
- No bypass UI for non-overridable findings.
- Advisory acknowledgment (with justification) permitted only for non-security major findings.

### F09 — Capability claims
- Kiro section: "guided handoff is the selected scope" (not a claim that no API exists).
- Codex CLI: capability marked "unavailable / unverified"; exact flags deferred to Wave 6.

### F10 — API endpoints
- Updated to 20 endpoints; added idempotent prompt GET, impl report submit, report validation status.
- Authorization request delegates to Wave 1; Wave 1 approve endpoint reused for granting.

---

## What to Review

### In `requirements.md`

1. **Workflow ordering (W3.1):** Does the 17-stage sequence correctly require Kiro validation before final approval? Is the prohibition on approving before Kiro validation (REQ-W3.1.1.3) correct?
2. **Manual mode lifecycle (W3.1.2–W3.1.6):** Is the authorization consumption at first export (REQ-W3.1.3.1) correct? Is re-read idempotency (REQ-W3.1.3.2) correctly specified?
3. **Non-overridable findings (W3.2.5):** Is the list of 7 non-overridable categories complete? Are any important categories missing?
4. **Authorization binding (W3.4.1):** Does the binding payload in `step.inputs_json` cover all required fields for a secure authorization? Is `kiro_handoff_id` appropriate as a binding field?
5. **Workspace validation (W3.5.1–W3.5.3):** Is the corrected algorithm description correct? Is the TOCTOU residual risk documented appropriately?
6. **Report validation (W3.6.1–W3.6.5):** Are the validation checks sufficient? Is the separation of `devflow_verification_passed` from `self_reported_verdict` correctly specified?
7. **Isolation gate (W3.8.2):** Is REQ-W3.8.2.3 (future prerequisite name must be verified against system state) correctly specified?
8. **Security requirements (W3.S.1–W3.S.10):** Does the clarification that sanitization is defense-in-depth (not elimination) in W3.S.1 correctly characterize the limitation?

### In `design.md`

1. **WorkspaceValidator (§6.1):** Is the corrected algorithm correct? Does `_check_intermediate_components()` correctly detect symlinks before `resolve()`?
2. **Authorization design (§8.1–8.3):** Does the Wave 1 interface description match the actual source (`request_bound_approval(run_id, step_run_id)`, `dispatch_protected_txn`)? Is the binding payload complete?
3. **State machines (§9.1–9.2):** Are the handoff and execution attempt state machines complete? Do all terminal states appear in both the diagram and the DB CHECK constraint?
4. **Data model (§10.1):** Is the split between `handoff_artifacts` and `execution_artifacts` correct? Does `execution_plans` have the correct scoped uniqueness constraint?
5. **ADR-W3-06 (§13):** Does the clarification "deterministic validation is structural/integrity-based, not a semantic guarantee" correctly characterize what DevFlow AI verifies?
6. **Non-overridable finding categories (§14.3):** Is the list complete and correct?

### In `tasks.md`

1. **W3.T12.1–W3.T12.28:** Are the 28 acceptance sub-criteria sufficient to cover all security invariants from F01–F10?
2. **W3.T12.3 (idempotent prompt re-read):** Is the test correctly specified? Does it verify no second `consume_atomic` is called?
3. **W3.T12.6 (real execution blocked):** Does the test correctly cover all configuration variants?
4. **W3.T12.27 (non-overridable findings):** Is the test sufficient to verify the enforcement mechanism?
5. **Task dependencies:** Does W3.T5 correctly depend on W3.T3, W3.T4, and W3.T6?

### Cross-document checks

1. **F02 correctness:** Does design.md §8.1 accurately describe the Wave 1 source interface? Can the described binding mechanism work without changes to Wave 1?
2. **F05 ordering:** Does the handoff state machine (design.md §3.1) correctly enforce that `handoff_created` requires spec in `approved` or `ready_for_approval`? Is this compatible with the Wave 2 spec state machine?
3. **F06 durable wait:** Does the design correctly explain how `KiroHandoffExecutor.execute()` maps to `WAITING_APPROVAL`? Is this compatible with the Wave 1 executor protocol without changes?
4. **Security workflow:** Does the security closure resolution report (phase2-wave2-security-closure-resolution.md) correctly document that Security CI is not currently passing on GitHub, only the offline scan is authoritative?

---

## Key Decisions for Verification

| Decision | Location | Verify |
|---|---|---|
| Authorization via `request_bound_approval(run_id, step_run_id)` | design.md §8.1 | Does this match the actual Wave 1 source? |
| Durable wait via `WAITING_APPROVAL` step state | design.md §3.2 | No new Wave 1 step state needed? |
| Intermediate symlink check before resolve | design.md §6.1 | Is the algorithm logically correct? |
| Non-overridable findings: 7 categories | design.md §14.3 | Is the list complete? |
| `real_execution_blocked` as outcome, not mode | requirements.md overview | Is the mode/state distinction clear throughout? |
| 20 API endpoints (updated from 17) | design.md §11 | Are idempotent GET endpoints correctly specified? |
| 7 tables (updated from 6) | design.md §10.1 | Is artifact ownership split correct? (Note: 9 tables as of v1.0.4; 2 more added in R02/R05 — `workflow_human_waits` and `workflow_wave3_capabilities`) |
