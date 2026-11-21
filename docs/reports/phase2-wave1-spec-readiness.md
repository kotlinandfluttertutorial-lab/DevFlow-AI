# Phase 2 Wave 1 — Specification Readiness Report

## Implementation verification update — 2026-11-19

Follow-up, 2026-11-21: same branch and baseline HEAD confirmed; Ruff, formatting, strict MyPy, migration head 008, and six focused acceptance tests passed again. Offline-only verification remains the user's explicit constraint. No new implementation changes or online verification occurred; the full-suite evidence below is dated 2026-11-19.

**PHASE 2 WAVE 1 IMPLEMENTATION BLOCKED** — exact CI online secret verification remains unexecuted. Following automatic approval review's rejection of possible candidate-credential egress, the user explicitly selected **Keep verification offline**; no online verification is authorized or planned. Offline TruffleHog 3.90.0 passed (652 chunks, zero verified/unverified findings). Release review must accept this documented offline-only evidence as an exception to the exact-workflow gate before the verdict can change. No known blocking implementation defect remains after fixing aiosqlite DEBUG parameter disclosure.

On `feature/phase2-wave1-workflow-engine-implementation`, baseline HEAD `2386a784d39c1bac3526923342985d70d4ce9a36`, T1–T10 implementation/semantic acceptance verification and T11 reporting are complete. Final regression: **574 passed**, **93.11% combined coverage**, **94.96% line coverage**, **84.52% branch coverage**. Ruff, format, strict MyPy (165 files), sole migration head 008, isolated migration round trips/ORM comparison/foreign keys, OpenAPI export/equality, CI integration selection (25 tests), Bandit, pip-audit (61 dependencies, no known vulnerabilities), and pip check passed. Live isolated FastAPI/Streamlit approval-to-completion, failure/reject/cancel/expiry, and new-process recovery checks passed. No operational database was changed; temporary services were stopped.

See [final verification report](phase2-wave1-final-verification-report.md) for the 61-requirement acceptance matrix, findings, detailed evidence, limitations, operational migration instructions, and explicit `git add -f` approach for required reports under the existing `/docs/` ignore rule. Groups A–C reports and the original blocker report are preserved. No push, PR, merge, deployment, or next-phase work occurred.

The sections below are historical specification-preparation evidence. Their old branch/head/environment/open-issue statements are not current implementation-readiness claims; the final verification report supersedes them for implementation assessment.

**Report date:** 2026-11-18 (updated for v2.1.4 final edge-case corrections)
**Spec version:** 2.1.4 (final edge-case corrections; supersedes v2.1.3 — see `docs/reports/phase2-wave1-v2.1.4-final-resolution.md` and prior reports)
**Phase:** 2 — Wave 1 (Workflow Execution Engine)
**Task type:** Specification only (no implementation)
**Repository:** `kotlinandfluttertutorial-lab/DevFlow-AI`
**Local path:** `J:\Android\GenAI\DevFlow-AI`

---

## 1. Current Branch

- **Current branch:** `feature/phase2-wave1-workflow-execution-engine`
- **Base commit:** `2d077ecb9f96ff767160e6c1564b2c6f8d4e0a9e` (= Phase 1 `develop` tip)
- **Created from:** `develop` (fast-forward was a no-op; `develop` was already at `origin/develop`)
- **Working tree:** clean (only an untracked `.venv-fresh` created for test verification; not committed)
- **No push, PR, or merge performed** (per task Git restrictions).

## 2. Phase 1 Baseline Verification

| Check | Result | Evidence |
|---|---|---|
| `git status` clean | PASS | clean tree on `develop` before branching |
| `git fetch --all --prune` | PASS | exit 0 |
| PR #13 merged into `develop` | PASS | `gh pr view 13`: state `MERGED`, base `develop`, mergeCommit.oid `2d077ec...`, mergedAt 2026-11-16 |
| Expected merge commit is ancestor of `origin/develop` | PASS | `git merge-base --is-ancestor 2d077ec... origin/develop` exit 0; commit is the tip of `origin/develop` |
| No existing Phase 2 branches/PRs | PASS | `git branch -a` + `gh pr list --search "phase 2"`: none |
| Latest migration verified | PASS | Alembic head = `007_add_github_confirmations` (down_revision `006_add_mcp_approvals`); confirmed via `alembic heads` and direct file read |
| Existing tests pass | PASS | `pytest -o addopts="" -q` → **313 passed** in 44.57s (develop baseline) |
| Repo conventions reviewed | PASS | ADRs, `.kiro/specs/` structure, Phase 1 models, `pyproject.toml` quality gates, Phase 1 roadmap |

**Phase 1 baseline:** v1.5.9 at commit `2d077ec` is the merged, green baseline. PR #13 (Wave 5 GitHub Orchestration) is merged; CI was green (commit `6d6dc2f` notes "all 6/6 CI checks pass").

**Local environment note (non-blocking):** the committed `.venv` is corrupted (pip itself fails; `typing_extensions` absent), and the system Python 3.12.10 install is incomplete (missing the `xml.dom` stdlib subpackage, which prevents `coverage` from importing). A fresh isolated `.venv-fresh` runs the full suite green with coverage disabled. CI remains the authoritative coverage/quality gate. No repository file was modified to obtain this result.

## 3. Specification Files Created or Updated

All files are **new** (no existing file modified; Phase 0/1 specs untouched). v2.0.0 → v2.1.1 reconciliation applied (passes v2.1.0 and v2.1.1); see `docs/reports/phase2-wave1-spec-resolution.md`.

| File | Deliverable | Status |
|---|---|---|
| `.kiro/specs/phase2-wave1-workflow-execution-engine/requirements.md` | (1) Requirements + (4) acceptance criteria/test matrix | Updated to v2.1.1 |
| `.kiro/specs/phase2-wave1-workflow-execution-engine/design.md` | (2) Architecture & design + (5) data model/migration plan + (6) API contracts + (7) security/threat model + (8) state-transition spec + executor contracts | Updated to v2.1.1 |
| `.kiro/specs/phase2-wave1-workflow-execution-engine/tasks.md` | (3) Task breakdown with dependencies | Updated to v2.1.1 |
| `docs/planning/phase2-roadmap.md` | (9) Phase 2 six-wave roadmap | Updated to v2.1.1 |
| `docs/reports/phase2-wave1-spec-readiness.md` | (10) Specification readiness report | Updated to v2.1.1 |
| `docs/reports/phase2-wave1-spec-resolution.md` | Reconciliation report (findings, resolutions, traceability, risks) | Updated to v2.1.1 (adds F14, F15) |
| `.agents/tasks/phase2-wave1-preflight.md` | Preflight evidence | Created |

**Deliverable coverage (task §6, ten required):** all ten delivered. Migration revision number is **not invented arbitrarily** — `008_add_workflow_execution_engine` is the next logical revision from the verified head `007_add_github_confirmations`, and the spec instructs the implementer to re-confirm the head before authoring (task §6 requirement satisfied).

## 4. Requirements Coverage

Task W1.1–W1.8 map to spec requirement areas E1–E8, each with EARS WHEN/THEN/MUST requirements and numbered acceptance criteria:

| Task item | Spec area | Requirements | Key coverage |
|---|---|---|---|
| W1.1 Definition & execution model | E1 | REQ-E1.1–E1.7 | `WorkflowTemplate`/`StepDefinition` in `definition_json` (no new template table); `WorkflowStepRun`; dependencies; I/O; tool selection; versioning; durable timestamps; CAS transitions |
| W1.2 Orchestration service | E2 | REQ-E2.1–E2.8 | create_run/start_or_resume/cancel/on_approval_resolved/recover; select eligible steps; dispatch; persist; advance; pause for approval; stop safely; orchestrator–executor interface (executors stateless) |
| W1.3 Approval checkpoints | E3 | REQ-E3.1–E3.5 | four operations; canonical digest; `consume_atomic` single-use CAS; replay/step/operation/actor binding; TTL on pending; deletion semantics; local single-user boundary |
| W1.4 Lifecycle & recovery | E4 | REQ-E4.1–E4.10 | eight states; transition map; terminal states; restart recovery; duplicate-dispatch CAS; leases; timeouts; cancellation; bounded safe retries; idempotency; stale detection; audit |
| W1.5 Executor contracts | E5 | REQ-E5.1–E5.4 | versioned `Executor` Protocol; capability/IO schemas; context; working-dir/timeout/cancellation; structured errors; artifact refs; `MockExecutor` only; no shell |
| W1.6 Security architecture | E6 | REQ-E6.1–E6.8 | loopback; approved dirs; env allowlist; credential redaction; no parent-env inheritance; no unrestricted execution; approval enforcement; audit integrity (honest); indeterminate outcomes; **explicit non-claim of OS sandboxing** + prerequisites list |
| W1.7 API & Streamlit | E7 | REQ-E7.1–E7.8 | create/start/cancel/view/inspect-failures/audit/approve-reject; minimal Streamlit; OpenAPI regen; no `/merge` |
| W1.8 Testing & acceptance | E8 | REQ-E8.1–E8.3 | full named-test matrix (state, deps, approvals, concurrency, recovery, cancel, timeout, idempotency, SQLite rollback, audit, API, mock, backward-compat, security negatives, migration round-trip); quality gates |

## 5. Design and Task Consistency

Consistency review (task §8) performed across requirements/design/tasks/roadmap:

- **Migration 008** referenced uniformly; chains from verified head `007`; reversible; `approval_requests.status` CHECK unchanged; `workflow_runs.status` CHECK unchanged. ✓
- **Eight-state lifecycle** lives on `workflow_step_runs`; run keeps Phase 1 six states, derived. Consistent in requirements (REQ-E4.1), design §3.3/§8, tasks. ✓
- **Approval consumption** via `consumed_at` CAS (no `consumed` status literal). Verified zero literal `'consumed'` status values; consistent across docs. ✓
- **Four approval operations** enumerated in requirements/roadmap; referenced in design. ✓
- **Mock-only executor**; no `subprocess`/shell/env; guard tests specified. ✓
- **No `/merge` endpoint**; all `/merge` mentions are explicit absence statements. ✓
- **Actor** = `Settings.LOCAL_OPERATOR_ID`, server-side only; never request body. ✓
- **Orchestrator-owns-state**: executors cannot import repositories/services/models; specified and guard-tested. ✓
- **SQLite patterns**: `BEGIN IMMEDIATE`, `busy_timeout`, CAS `rowcount`, executor call outside DB transaction; consistent in design §10 and tasks. ✓
- **Backward compatibility**: Phase 1 constraints/endpoints/schemas preserved; Phase 1 tests pass unmodified (no constraint-test edits needed due to `consumed_at` design). ✓

Two acceptance-criteria numbering typos in `requirements.md` (E4.4.4 → E4.3.4, E4.4.10.2 → E4.10.2) were corrected in v2.0.0; a further duplicate AC ID (REQ-E6.8 had two `E6.8.1`) and a task-name typo (`start_orume` → `start_or_resume`) were corrected in the v2.1.0 pass.

**v2.1.0 reconciliation:** the v2.0.0 spec was re-reviewed against the 12 correction items in task §2 and the actual Phase 1 services. Twelve findings were resolved (pre-dispatch `READY→FAILED`/`PENDING→FAILED` transitions; retry split into transient `RUNNING→READY`; disambiguated interruption triggers; approval re-gating and single-use consumption races; run terminal derivation only when all steps terminal; immutable template snapshot per run; consistent attempt/budget semantics; pending vs approved validity; lease/attempt fencing; canonical schema-module naming; task/AC-ID fixes; added negative/concurrency tests). Resolved defaults were applied. Phase 1 compatibility was reconciled against the actual `WorkflowService.WORKFLOW_TRANSITIONS` and `ApprovalService` signatures (run `waiting_approval` transitions go through the approval service, not `transition_run`; legacy `NULL`-template runs preserved so Phase 1 tests pass unmodified).

**v2.1.1 final corrections:** two further findings resolved. **(F14)** Approval consumption moved to **before** the protected executor is invoked — lease acquisition, `attempt` increment, `READY → RUNNING`, and `consume_atomic` are one atomic transaction; a failed consume rolls back (step unexecuted) and re-gates; the executor runs only after commit (outside the DB transaction); consumed approvals are never restored on cancellation/failure/timeout/indeterminate; protected-op retries re-gate for fresh authorization; stale completions cannot re-consume. **(F15)** Deterministic failure-stop settle policy — when a step terminally fails, no new dispatch; in-flight steps finish (retry suppressed); all remaining non-terminal non-running steps (`PENDING`/`READY`/`WAITING_APPROVAL`/`INTERRUPTED`) settled to `FAILED` (`skipped`, `STEP_BLOCKED_BY_RUN_FAILURE`) via existing transitions (no ninth step status); pending approvals on settled `WAITING_APPROVAL` steps expired; run reaches `failed` only after all settled. New `RUNNING → WAITING_APPROVAL` (approvable retry) and `WAITING_APPROVAL → FAILED` (settle) transitions added (between existing states). Negative/concurrency/settle tests added. See `docs/reports/phase2-wave1-spec-resolution.md`.

**No remaining contradictions, unsafe assumptions, or implementation blockers** within the Wave 1 scope. Remaining items are documented decisions for approval, not blockers (§8; design §18).

## 6. Security Decisions

1. **Local single-user trust boundary** is explicit; no multi-user authentication is claimed. `actor_id` = `Settings.LOCAL_OPERATOR_ID` (server-side only).
2. **Approvals are digest-bound and single-use** (`consume_atomic` CAS on `consumed_at`); an approval cannot authorize a later, different, or unrelated action; replay is rejected via `rowcount == 0`.
3. **`pending` approvals expire** by TTL; `approved` approvals are deliberately NOT auto-expired (explicit decision, flagged for review) so a user's explicit consent is not silently invalidated.
4. **No `consumed` status value** is added to `approval_requests` — single-use semantics via `consumed_at` preserves the Phase 1 CHECK constraint and Phase 1 tests.
5. **Executors are sandboxed at the application level only**: no subprocess/shell, no `os.environ`, working-dir allowlist (empty by default), explicit env. The spec **explicitly does not claim OS-level sandboxing** and lists the OS-level isolation prerequisites required before real coding-agent execution (process isolation, OS-enforced FS allowlist, network egress control, resource limits, credential vault) — gated to Wave 6.
6. **Indeterminate outcomes** (future 5xx/transport errors) are never auto-retried and never auto-restore a consumed approval; a fresh approval is required.
7. **Audit is append-only** and not cryptographically signed; the spec makes no false claim of cryptographic integrity.
8. **No automatic PR merge** — no `/merge` endpoint exists or is planned (permanent exclusion; merge requires separate explicit approval).

## 7. Migration Strategy

- **Verified Alembic head:** `007_add_github_confirmations`.
- **Planned migration:** `008_add_workflow_execution_engine`, `down_revision = "007_add_github_confirmations"`. Implementer re-confirms head before authoring.
- **Adds (additive):** NEW table `workflow_step_runs` (eight-state CHECK, `UNIQUE(run,step_key)`, lease/timestamp/idempotency columns, indexes); ALTER `approval_requests` ADD `operation`, `parameters_hash`, `actor_id`, `workflow_step_run_id` (ON DELETE SET NULL), `consumed_at` + indexes.
- **Does NOT change:** `approval_requests.status` CHECK (stays `pending|approved|rejected|expired`); `workflow_runs.status` CHECK (stays six values). Migrations 001–007 untouched.
- **Reversible:** `downgrade()` drops columns/indexes and the new table, restoring the prior schema exactly; round-trip 001→008 tested (REQ-E8.3).
- **No trigger needed** for approvals: `approval_requests.workflow_run_id` is `ON DELETE CASCADE` (Phase 1), so run deletion removes approvals before the step FK matters; no NULL-to-NULL bypass (unlike Phase 1 MCP/GitHub `SET NULL` + trigger pattern).

## 8. Open Issues (decisions for ChatGPT review, not blockers)

1. `Settings.APPROVAL_TTL_SECONDS` default (proposed 3600).
2. `Settings.RUN_CONCURRENCY` (proposed 1 in Wave 1 for determinism).
3. Lease TTL / heartbeat (proposed 60s / 15s).
4. Whether `approved` approvals should be TTL-expirable (REQ-E3.4.2 deliberately says no) — confirm.
5. Template snapshot on run (`template_version` + `definition_hash` to detect drift) — confirm approach.
6. `template_version` storage location (proposed `WorkflowRun.result_json` metadata; no new column) — confirm.
7. Whether Wave 1 auto-resumes after recovery or requires an explicit `start` — confirm.
8. Executor AST/import guard test scope (hard gate vs advisory) — confirm.
9. Migration revision `008` derived from verified head `007`; confirm no Phase 1 migration added between preflight and implementation.

## 9. Recommended Implementation Order

Critical path (from `tasks.md`): **T1 → T2 → T3 → T5 → T6 → T8 → T10 → T11**, with T4 (executor) parallel to T2/T3, T7 (recovery) after T5, T9 (UI) after T8.

1. **T1** Workflow template schema + validation.
2. **T2** `WorkflowStepRun` model + migration 008 (re-confirm head first).
3. **T3** Repositories (step-run CAS/lease + approval `consume_atomic`).
4. **T4** Executor contract + `MockExecutor` + registry (parallel).
5. **T5** `WorkflowOrchestrator` (create/start/dispatch/succeed/fail).
6. **T6** Approval checkpoints in the orchestrator.
7. **T7** Recovery service + leases.
8. **T8** Routers + OpenAPI regeneration.
9. **T9** Streamlit minimal UI.
10. **T10** Full test suite (all REQ-E8.1 named tests).
11. **T11** Wave 1 readiness report + final verification.

Resolve the open decisions in §8 with ChatGPT review before coding.

## 10. Verification Results

**Preflight:** PASS (see §2 and `.agents/tasks/phase2-wave1-preflight.md`).

**Consistency review (task §8):** PASS — no missing requirements, contradictions, unsafe assumptions, or implementation blockers within Wave 1 scope; cross-document consistency verified for migration number, state model, approval semantics, executor constraints, security claims, and backward compatibility.

**Quality-gate applicability (not run — spec-only task):** `ruff check .`, `mypy --strict src/`, `pytest --cov=src --cov-branch --cov-fail-under=80`, and `alembic upgrade/downgrade` round-trip are defined as the Wave 1 completion gate (`tasks.md`) and are to be satisfied by the implementation (T1–T11), not by this specification task.

**Git restrictions honored:** only the feature branch was created; only specification/report files were added; no production source code modified; no migrations authored; no Codex run; no push; no PR; no merge.

## 11. Final Readiness Verdict

All ten required specification deliverables are produced (v2.1.1, final corrections), internally consistent, and aligned with the repository's existing spec structure and conventions. Preflight passed. Phase 1 backward compatibility is preserved by design (no CHECK-constraint changes; additive columns, one new table, and one new run-snapshot extension; legacy `NULL`-template runs preserved). Phase 1 service compatibility is reconciled against the actual `WorkflowService`/`ApprovalService` code. Security claims are honest (explicit non-claim of OS sandboxing; prerequisites listed). Migration strategy is grounded in the verified Alembic head. v2.1.1 adds two final invariants: protected executors are never invoked before their approval is atomically consumed (lease+consume in one transaction, executor outside it, no restore on any outcome), and the failure-stop policy deterministically settles all remaining non-terminal non-running steps to `FAILED` (`skipped`) so the run reaches `failed` only after settle — no ninth step status introduced. Resolved defaults are applied; remaining items are documented decisions for approval, not blockers. The reconciliation report (`docs/reports/phase2-wave1-spec-resolution.md`) records all fifteen findings (F1–F15), resolutions, changed files/sections, design rationale, remaining risks, and the requirements-to-tests traceability.

**PHASE 2 WAVE 1 SPEC v2.1.4 — AWAITING CHATGPT REVIEW**
