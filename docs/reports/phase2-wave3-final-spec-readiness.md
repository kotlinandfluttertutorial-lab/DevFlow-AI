# DevFlow AI — Phase 2 Wave 3 Final Specification Readiness Report

**Version:** 1.0.2 (Final)
**Branch:** `feature/phase2-wave3-coding-agent-integration`
**Date:** 2026-11-23
**Supersedes:** `phase2-wave3-spec-readiness.md` (v1.0.1)

---

## 1. Final Version

Specification v1.0.2 is the frozen version of the Wave 3 specification. All targeted integration corrections (C1–C7) have been applied.

| File | Version |
|---|---|
| `requirements.md` | 1.0.2 |
| `design.md` | 1.0.2 |
| `tasks.md` | 1.0.2 |

---

## 2. Git State

| Item | Value |
|---|---|
| Branch | `feature/phase2-wave3-coding-agent-integration` |
| HEAD before this commit | `2d8997b` |
| Branched from | `develop` at `ee57dc7` |
| Wave 2 PR | #24 merged 2026-11-23 |
| Wave 2 spec | v1.6.1 (frozen) |
| Migration head | `009_add_ai_specification_generation` (Wave 3 migration 010 designed, not yet implemented) |

---

## 3. Targeted Correction Matrix (C1–C7)

### C1 — Execution Mode Enum Reconciliation

| Item | Detail |
|---|---|
| **Problem** | REQ-W3.3.1 listed only `mock` and `real_when_isolated`. Canonical set is `manual`, `mock`, `real_when_isolated`. `blocked` is an outcome, not a mode. |
| **Fix applied — requirements.md** | REQ-W3.3.1 updated to `manual \| mock \| real_when_isolated`; note added distinguishing requested mode from `real_execution_blocked` outcome; REQ-W3.3.1.6 added rejecting `blocked` as input. |
| **Fix applied — design.md** | §4.1 heading note added; `CodexExecutionInput.execution_mode` updated to `Literal["manual", "mock", "real_when_isolated"]`; `execution_plans` CHECK constraint updated to `IN ('manual','mock','real_when_isolated')`; manual mode subprocess prohibition noted. |
| **Fix applied — tasks.md** | W3.T4.11 added rejecting `blocked` mode; W3.T6.11 added verifying CHECK constraint. |
| **Verification** | Search `requirements.md`, `design.md`, `tasks.md`: no occurrence of `blocked` as a requested mode value. All typed contracts use the 3-value enum. |

### C2 — Immutable Execution Plan Persistence

| Item | Detail |
|---|---|
| **Problem** | `execution_plans` table missing `baseline_commit` and `deadline_at`; FKs were nullable allowing standalone plans; no crash-safe first-transition rule. |
| **Fix applied — design.md** | `execution_plans` DDL updated: `baseline_commit TEXT NOT NULL`, `deadline_at TEXT`, `workflow_run_id NOT NULL`, `workflow_step_run_id NOT NULL`; persistence requirement block added; crash-safe transition documented; auth-consumption guard added. |
| **Fix applied — requirements.md** | REQ-W3.4.7 added with 5 acceptance criteria covering: (a) `baseline_commit`/`deadline_at` persisted before auth request, (b) non-null workflow ownership, (c) crash-safe first transition, (d) auth not consumed for blocked real execution, (e) replacement plan required after input changes. |
| **Fix applied — tasks.md** | W3.T6.9–W3.T6.13 added; W3.T7.11–W3.T7.12 added. |
| **Verification** | `execution_plans` DDL contains `baseline_commit TEXT NOT NULL`, `deadline_at TEXT`, both FKs `NOT NULL`. REQ-W3.4.7 present with all 5 ACs. |

### C3 — Durable Waiting Contract

| Item | Detail |
|---|---|
| **Problem** | Design proposed `outcome="paused"` which does not exist in actual `StepExecutionResult`. Contract was undefined for wait reason, ownership, duplicate prevention, and recovery. |
| **Source verified** | `src/devflow_api/schemas/executor.py`: `StepExecutionResult.outcome: Literal["succeeded", "failed", "cancelled", "indeterminate"]` — `"paused"` confirmed absent. `WAITING_APPROVAL` step state confirmed in `ApprovalService._resolve_bound()` source. |
| **Fix applied — design.md** | §3.2 updated; §3.2.1 "Durable Waiting Contract (Source-Verified)" added with: `outcome="indeterminate"` as the backward-compatible extension; `WAITING_FOR_KIRO_REPORT` / `WAITING_FOR_IMPL_AUTHORIZATION` as `error.code` discriminators; full ownership, atomic transition, lease release, validated submission, idempotent resumption, cancellation/expiry, recovery, no-duplicate-consumption, no-auto-dispatch rules specified. |
| **Fix applied — requirements.md** | REQ-W3.7.3.4–REQ-W3.7.3.6 added: wait reason distinguishable; idempotent resumption; no auto-dispatch for `real_when_isolated`. |
| **Fix applied — tasks.md** | W3.T4.12–W3.T4.13 added. |
| **Verification** | §3.2.1 cites actual source field names. `outcome="indeterminate"` is the only valid backward-compatible choice. No new step state introduced. |

### C4 — Workspace Metadata Security

| Item | Detail |
|---|---|
| **Problem** | Design §6.2 followed `.git` indirection paths without equivalent boundary validation. No bounds on metadata reads. No policy on config includes or alternates. |
| **Fix applied — design.md** | §6.3 "Git Metadata Path Validation" added: 8 rules covering boundary enforcement, symlink rejection, bounded reads (HEAD/gitdir/commondir ≤ 4 KB; config ≤ 64 KB; packed-refs ≤ 1 MB), no config includes, no alternates, standard layout requirement, submodule deferral, TOCTOU residual documented. |
| **Fix applied — requirements.md** | REQ-W3.5.6 added with 8 acceptance criteria: `gitdir:` outside root → `UNSUPPORTED_REPO_LAYOUT`; `.git` file support scoped; bounded reads → `GIT_METADATA_TOO_LARGE`; `[include]` rejection → `GIT_CONFIG_INCLUDES_UNSUPPORTED`; alternates rejection → `GIT_ALTERNATES_UNSUPPORTED`; `.gitmodules` → `UNSUPPORTED_REPO_LAYOUT`; standard layout validated; TOCTOU documented. |
| **Fix applied — tasks.md** | W3.T1.11–W3.T1.16 added (6 new ACs). |
| **Verification** | §6.3 fully specified. All error codes defined. TOCTOU limitation preserved as documented residual. |

### C5 — Kiro Approval Ordering

| Item | Detail |
|---|---|
| **Problem** | Spec must require valid Kiro verification evidence bound to current version/digest before final specification approval. Changing spec must invalidate stale evidence. Must be verified at backend, not only UI. |
| **Fix applied — requirements.md** | REQ-W3.2.9 added: backend `SpecificationApprovalService` MUST reject approval requests when Kiro verification is absent or digest-mismatched; `KIRO_VERIFICATION_REQUIRED` error code; legacy Wave 2 unaffected. REQ-W3.2.10 added: new version row created with `kiro_verification_id = NULL`; stale evidence per version. |
| **Fix applied — design.md** | §8.5 "Kiro Approval Ordering Enforcement at SpecificationApprovalService" added: prerequisite check steps specified; rejection with `KIRO_VERIFICATION_REQUIRED`; `_eligible()` method identified as enforcement point; stale evidence invalidation rule; Wave 2 compatibility preserved. |
| **Fix applied — tasks.md** | W3.T7.13 added. |
| **Source-backed:** | `SpecificationApprovalService._eligible()` (from `src/devflow_api/services/specification_approval.py`) is the existing backend gate where review/digest prerequisites are checked. The Kiro prerequisite check is added to the same gate. |
| **Verification** | §8.5 present. REQ-W3.2.9/W3.2.10 present. `KIRO_VERIFICATION_REQUIRED` error code defined. |

### C6 — Artifact Ownership

| Item | Detail |
|---|---|
| **Problem** | Prompt lookup based only on `handoff_id` could return another plan's prompt. `content_hash` was missing from `handoff_artifacts`. No immutable content storage, atomic publication, or crash-safe recovery rule. |
| **Fix applied — design.md** | `handoff_artifacts` DDL updated: `execution_plan_id INTEGER REFERENCES execution_plans(id)` added; `content_hash TEXT NOT NULL` confirmed; `status` column added for publication state; `idx_ha_plan` index added. Ownership rules block added: codex_prompt lookup requires `execution_plan_id`; lookup by `handoff_id` alone prohibited; content immutable after `published`; publication atomic via `BEGIN IMMEDIATE`; crash-safe recovery via `'prompt_publishing'` intermediate state. |
| **Fix applied — tasks.md** | W3.T6.12–W3.T6.13 added; W3.T7.14 added. |
| **Verification** | `handoff_artifacts` DDL contains `execution_plan_id` and `content_hash NOT NULL`. Ownership rules block present. |

### C7 — Version Bump to v1.0.2

| Item | Detail |
|---|---|
| **Fix applied** | `requirements.md`: version `1.0.1 → 1.0.2`; status updated; version history entry added. `design.md`: version `1.0.1 → 1.0.2`; status updated; version history block added. `tasks.md`: version `1.0.1 → 1.0.2`; status updated; version history block added. |
| **Verification** | All three files have `Version: 1.0.2` in front matter. |

---

## 4. Source-Backed Approval Contract

From `src/devflow_api/services/approval.py`:

```python
# ApprovalService.request_bound_approval(run_id: int, step_run_id: int, *, commit: bool = True)
# → Transitions step READY → WAITING_APPROVAL via transition_cas()
# → parameters_hash = canonical_hash(json.loads(step.inputs_json or "{}"))
# → Checks step.input_digest == digest (no mismatch tolerated)
# → Creates ApprovalRequest with operation, parameters_hash, actor_id
```

```python
# ApprovalService._resolve_bound(..., decision="approved")
# → Sets authorization_expires_at via set_authorization_expires_at()
# → Transitions step WAITING_APPROVAL → READY via transition_cas()
# → dispatch_protected_txn (via WorkflowStepRunRepository) transitions READY → RUNNING
#   and calls consume_atomic(approval_id, operation, parameters_hash, workflow_step_run_id, actor_id)
# → CAS: sets consumed_at atomically if status=approved, consumed_at IS NULL,
#         authorization_expires_at > now(), all binding fields match
```

The `implementation_authorization` operation is an `ApprovalOperation` literal in `src/devflow_api/schemas/workflow_template.py` (confirmed by design §8.2 source inspection). Wave 3 uses this existing operation literal.

---

## 5. Source-Backed Durable Waiting Contract

From `src/devflow_api/schemas/executor.py`:

```python
class StepExecutionResult(ExecutorModel):
    outcome: Literal["succeeded", "failed", "cancelled", "indeterminate"]
```

**`"paused"` does NOT exist.** The backward-compatible extension for Wave 3 durable human-wait:

- `KiroHandoffExecutor.execute()` → `outcome="indeterminate"`, `error.code="WAITING_FOR_KIRO_REPORT"`, `error.retryable=False`, `error.category="internal"`
- `CodexCLIExecutor.execute()` (manual, awaiting impl auth) → `outcome="indeterminate"`, `error.code="WAITING_FOR_IMPL_AUTHORIZATION"`, `error.retryable=False`

The `WAITING_APPROVAL` step state confirmed at line in `approval.py`: `steps.transition_cas(step_run_id, "WAITING_APPROVAL", from_status="READY")`.

No new `StepExecutionResult.outcome` variant added. No new `WorkflowStepRun` status added.

---

## 6. Final Migration 010 Schema Summary

**7 new tables** in `010_add_coding_agent_integration.py`:

| Table | Key Fields | Key Constraints |
|---|---|---|
| `handoff_records` | id, spec_request_id, spec_version_id, artifact_digest, task_ids_json, status, awaiting_since | UNIQUE(spec_version_id) WHERE non-terminal; CHECK status enum |
| `handoff_artifacts` | id, handoff_record_id, execution_plan_id, artifact_type, content_hash NOT NULL, status | idx_ha_plan; content_hash NOT NULL |
| `handoff_findings` | id, handoff_record_id, finding_id, category, is_non_overridable | UNIQUE(handoff_record_id, finding_id) |
| `execution_plans` | id, handoff_record_id, workflow_run_id NOT NULL, workflow_step_run_id NOT NULL, baseline_commit NOT NULL, deadline_at, execution_mode, canonical_input_hash, status | UNIQUE(workflow_run_id, workflow_step_run_id); CHECK execution_mode IN ('manual','mock','real_when_isolated') |
| `execution_attempts` | id, execution_plan_id, execution_mode, status, baseline_commit, deadline_at, self_reported_verdict, devflow_verification_passed | CHECK status enum (15 values); CHECK devflow_verification_passed |
| `execution_artifacts` | id, execution_attempt_id, artifact_type, file_name, content_hash, storage_key | idx_exart_attempt, idx_exart_hash |
| `capability_records` | id, checked_at, codex_cli_installed, codex_cli_capability_status, isolation_status | CHECK enum fields |

**Index count:** 21 indexes across 7 tables.

**FK list (new FKs):**
- `handoff_records.specification_request_id → specification_requests(id)`
- `handoff_records.specification_version_id → specification_versions(id)`
- `handoff_artifacts.handoff_record_id → handoff_records(id)`
- `handoff_artifacts.execution_plan_id → execution_plans(id)`
- `handoff_findings.handoff_record_id → handoff_records(id)`
- `execution_plans.handoff_record_id → handoff_records(id)`
- `execution_plans.workflow_run_id NOT NULL → workflow_runs(id)`
- `execution_plans.workflow_step_run_id NOT NULL → workflow_step_runs(id)`
- `execution_plans.specification_version_id → specification_versions(id)`
- `execution_attempts.execution_plan_id → execution_plans(id)`
- `execution_artifacts.execution_attempt_id → execution_attempts(id)`

---

## 7. Workspace Metadata Policy Summary

The `WorkspaceValidator` enforces two layers:

**Layer 1 — Workspace path validation (§6.1):**
- Intermediate component symlink check before `Path.resolve()`
- UNC/device path rejection on Windows
- `is_relative_to()` for containment
- TOCTOU risk documented as residual

**Layer 2 — Git metadata path validation (§6.3):**
- All git metadata paths validated against approved root before reading
- Symlinks in metadata paths subject to `WORKSPACE_FOLLOW_SYMLINKS` policy
- Bounded reads: HEAD/gitdir ≤ 4 KB; config ≤ 64 KB; packed-refs ≤ 1 MB
- No config includes (`[include]`/`[includeIf]` → `GIT_CONFIG_INCLUDES_UNSUPPORTED`)
- No external alternates (`objects/info/alternates` → `GIT_ALTERNATES_UNSUPPORTED`)
- Submodules deferred (`UNSUPPORTED_REPO_LAYOUT`)
- Worktrees supported only if `gitdir:` target within approved root

---

## 8. Artifact Ownership Policy Summary

- Each `codex_prompt` artifact is owned by exactly one `execution_plans` row via `execution_plan_id FK NOT NULL`.
- Lookup by `handoff_id` alone is prohibited for `codex_prompt` artifacts.
- A handoff may reference multiple independently authorized plans; each plan has its own prompt artifact.
- `kiro_prompt` and `kiro_report` artifacts are owned by the handoff only (`execution_plan_id = NULL`).
- `content_hash TEXT NOT NULL` on all `handoff_artifacts` rows.
- Content is immutable after `status = 'published'`.
- Publication is atomic via `BEGIN IMMEDIATE`.
- `status = 'prompt_publishing'` is an intermediate state detected on restart for crash-safe recovery.

---

## 9. Final Task Grouping (W3.T1–W3.T12)

```
Group A — Foundation (no Wave 3 dependencies):
  W3.T6  Data model: migration 010 (7 tables, 13 ACs)
  W3.T1  IsolationGate + WorkspaceValidator + config (16 ACs, +6 for git metadata)

Group B — Executors and validators (depend on T6, T1):
  W3.T2  KiroHandoffExecutor (10 ACs)
  W3.T3  ReportValidator: KiroVerificationReport (10 ACs)
  W3.T4  CodexCLIExecutor (13 ACs, +3 for durable wait and mode correction)
  W3.T5  ReportValidator: CodexImplementationReport (10 ACs)

Group C — Services and API (depend on Group B):
  W3.T7  ExecutionPlanService + CodingAgentAuthorizationService (14 ACs, +4 for new corrections)
  W3.T10 WorkflowOrchestrator integration (6 ACs)
  W3.T8  FastAPI routes — 20 endpoints (10 ACs)

Group D — UI and tests (depend on Group C):
  W3.T9  Streamlit UI (8 ACs)
  W3.T11 Offline security tests (12 ACs)
  W3.T12 Full acceptance test suite (28 sub-criteria)
```

---

## 10. Requirement Count Summary (v1.0.2)

| Metric | Count |
|---|---|
| Requirement areas (functional) | 10 (W3.1–W3.10) |
| Security requirements | 10 (REQ-W3.S.1–W3.S.10) |
| Named requirements (REQ-W3.X.Y) | ~70 (65 from v1.0.1 + 5 new: REQ-W3.2.9, REQ-W3.2.10, REQ-W3.4.7, REQ-W3.5.6, REQ-W3.7.3.4-6) |
| Acceptance criteria total | ~130 (110 from v1.0.1 + ~20 new from C1–C6) |
| Implementation tasks | 12 (W3.T1–W3.T12; unchanged count) |
| Acceptance test sub-criteria (W3.T12) | 28 (unchanged; all preserved) |
| Database tables (migration 010) | 7 (unchanged) |
| New database columns vs v1.0.1 | +3 (`execution_plans.baseline_commit`, `execution_plans.deadline_at`, `handoff_artifacts.execution_plan_id`) |
| API endpoints | 20 (unchanged) |
| ADRs | 10 (all Decided; unchanged) |

---

## 11. Remaining Limitations

| Limitation | Detail |
|---|---|
| TOCTOU residual | Application-level path checking is not atomic. Wave 6 OS-level sandboxing is the real mitigation. |
| Worktree support | Supported only if `gitdir:` target resolves within approved root; otherwise `UNSUPPORTED_REPO_LAYOUT`. |
| Submodule support | Deferred. Any workspace with `.gitmodules` returns `UNSUPPORTED_REPO_LAYOUT`. |
| Real coding-agent execution | Unconditionally blocked in Wave 3. Requires Wave 6 OS isolation prerequisites. |
| Codex CLI flags | Abstract contract only; exact flags unverified (CLI not installed). Deferred to Wave 6. |
| GitHub PR creation | Deferred to Wave 4. No git write operations in Wave 3. |
| Kiro programmatic API | Guided handoff only. No confirmed stable API for programmatic invocation. |

---

## 12. PR Readiness Verdict

**READY FOR PR CREATION.**

All 7 targeted corrections applied. Source-verified durable waiting contract specified against actual `StepExecutionResult`. Execution mode enum consistent across all three spec files. `execution_plans` schema complete with `baseline_commit`, `deadline_at`, and non-null workflow ownership. Git metadata security fully specified. Kiro approval ordering enforced at backend service layer. Artifact ownership policy clear and unambiguous.

No Wave 3 source code was written (scope boundary respected). No Wave 1/Wave 2 contract modified.

**Merge requires explicit human approval. No auto-merge.**
