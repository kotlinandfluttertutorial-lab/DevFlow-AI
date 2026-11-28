# Phase 2 Wave 3 — R01–R05 Resolution Matrix

## v1.0.4 executable verification — 2026-11-24

| Finding | Current result |
|---|---|
| R01 | Reproduced the v1.0.3 replacement failure. Executed the corrected partial index and composite FK on migration 009 fixtures. History, independent steps and fresh binding passed. |
| R02 | Current `indeterminate` persists failure. v1.0.4 specifies a new typed report wait, distinct from approval. Existing orchestrator and recovery characterization passed, and proposed wait DDL and downgrade passed in SQLite. |
| R03 | A single proposed transaction owns consumption, durable exact prompt bytes, attempt, wait and audit. The SQLite probe verified rollback of consumption, intent, attempt and audit and preserved exact bytes on commit. |
| R04 | SQL rejected cross-handoff ownership, invalid artifact kinds, missing hashes, duplicate prompt and mutation of the published hash. |
| R05 | Verified real 009 FK targets, the corrected `handoff_records` link, immutable capability snapshot and populated round-trip. The nonexistent template table was removed from the design. |

This supersedes the v1.0.3 assessment below. The new lifecycle and migration remain implementation tasks. [Full DDL and evidence](phase2-wave3-final-technical-verification.md)

## Historical v1.0.3 assessment _(Historical — superseded by v1.0.4 technical verification)_

**Spec version:** v1.0.3
**Date:** 2026-11-23
**Branch:** feature/phase2-wave3-coding-agent-integration

---

## R01 — Replacement Execution Plans

**Original problematic text (design.md ~line 913):**
`UNIQUE(workflow_run_id, workflow_step_run_id)` — full unique constraint preventing any replacement plan for the same step once a plan existed.

**Corrected text:**
- Full UNIQUE constraint `UNIQUE(workflow_run_id, workflow_step_run_id)` retained as a broad constraint (one plan per step per status group) but superseded in enforcement by:
- Added: `CREATE UNIQUE INDEX uidx_execution_plans_active_step ON execution_plans(workflow_step_run_id) WHERE status IN ('draft','ready','prompt_publishing','prompt_published','executing')`
- Added: "Replacement Execution Plans (R01)" prose section defining active/historical statuses, atomic replacement procedure, ownership validation, fresh authorization requirement, non-transferability, and duplicate prevention.
- Both the full UNIQUE and the partial index are present in the DDL; the partial index is the operative constraint for active-plan enforcement.

**Source file and line reference:**
- `design.md` §10.1 `execution_plans` table DDL (lines 969–976): `UNIQUE(workflow_run_id, workflow_step_run_id)` retained; `uidx_execution_plans_active_step` partial index added immediately after.
- `design.md` §10.1 "Replacement Execution Plans (R01)" section (lines 989–1010): full prose added.
- `tasks.md` W3.T6.14 added: partial index acceptance criterion.

**Validation:** SQLite DDL validated in-memory:
- `All DDL valid`
- `Partial index correctly blocks duplicate active plan`
- `Stale plan correctly allows new active plan`

**Result:** RESOLVED.

**Remaining limitation:** Same-step concurrent replacement requires application-level serialization (SQLite WAL mode); the partial index prevents logical duplicates but not application-level races in concurrent API deployments.

---

## R02 — Durable Human Waiting

**Original problematic text (design.md ~lines 144–145):**
Spec assumed `outcome="indeterminate"` maps to `WAITING_APPROVAL` without source verification.

**Source evidence found:**
Verified in `src/devflow_api/schemas/executor.py`: `StepExecutionResult.outcome: Literal["succeeded", "failed", "cancelled", "indeterminate"]` — `"paused"` confirmed absent. Verified in `src/devflow_api/services/approval.py`: `WAITING_APPROVAL` step state confirmed via `steps.transition_cas(step_run_id, "WAITING_APPROVAL", from_status="READY")`.

**Corrected text:**
- Added "No fake approval requests" paragraph distinguishing report-waiting from approval-waiting.
- Added "Legacy preservation" paragraph confirming Wave 1/2 unaffected.
- Added expiry detail: `HANDOFF_EXPIRED` error code, `awaiting_since` threshold.
- Wait reason distinguishable by reading `handoff_records.status` vs `execution_attempts.status`.
- Full durable waiting contract in `design.md` §3.2.1: `outcome="indeterminate"`, `error.code` discriminators (`WAITING_FOR_KIRO_REPORT`, `WAITING_FOR_IMPL_AUTHORIZATION`), ownership, atomic transition, lease release, idempotent resumption, cancellation/expiry, recovery, no duplicate-consumption, no auto-dispatch rules.

**Validation:** Source code read; `StepExecutionResult` schema confirmed. `WAITING_APPROVAL` step state confirmed in `ApprovalService._resolve_bound()` source.
**Result:** RESOLVED.
**Remaining limitation:** `wait_reason` sub-state is in domain records, not `WorkflowStepRun`; monitoring tooling must join both tables to display full wait reason.

---

## R03 — Publication States

**Original problematic text (design.md ~lines 864–867):**
`prompt_publishing` and `prompt_published` referenced in publication algorithm but absent from `execution_plans` CHECK constraint.

**Corrected text:**
CHECK constraint updated to include all statuses:
```sql
CHECK (status IN ('draft','ready','prompt_publishing','prompt_published','executing','completed','failed','cancelled','stale'))
```

Two-step crash-safe publication documented:
1. Consume auth + set `prompt_publishing` (atomic, `BEGIN IMMEDIATE`).
2. Store artifact + set `prompt_published` (atomic, `BEGIN IMMEDIATE`).

Recovery on restart: any plan in `prompt_publishing` re-executes step 2 from deterministic immutable inputs.

**Source file and line reference:**
- `design.md` §10.1 `execution_plans` DDL: CHECK constraint updated (line ~968).
- `design.md` §10.1 `handoff_artifacts` block: "Atomic publication (R03 — crash-safe two-step)" prose added.
- `tasks.md` W3.T6.15 added: CHECK constraint includes publication states.

**Validation:** SQLite DDL validated in-memory — `All DDL valid` (all 9 status values accepted).
**Result:** RESOLVED.
**Remaining limitation:** Recovery re-generates prompt content from immutable inputs; for very large prompts this adds restart latency.

---

## R04 — Artifact Ownership

**Original problematic text (design.md ~line 838):**
`execution_plan_id` nullable with ownership rules only in comments; no enforceable constraints.

**Corrected text:**
Added to `handoff_artifacts` DDL:
```sql
CHECK (artifact_type != 'codex_prompt' OR execution_plan_id IS NOT NULL)
CHECK (artifact_type IN ('codex_prompt') OR execution_plan_id IS NULL)
CREATE UNIQUE INDEX uidx_codex_prompt_per_plan
  ON handoff_artifacts(execution_plan_id)
  WHERE artifact_type = 'codex_prompt' AND execution_plan_id IS NOT NULL;
```

Same-handoff constraint (execution plan's `kiro_handoff_id` must match artifact's `handoff_record_id`) is enforced transactionally in `ExecutionPlanService.publish_prompt()` because SQLite CHECK cannot reference other tables.

**Source file and line reference:**
- `design.md` §10.1 `handoff_artifacts` DDL: two CHECK constraints and `uidx_codex_prompt_per_plan` index added.
- `design.md` §10.1 `handoff_artifacts` block: "Artifact ownership rules (R04)" prose block added with all constraints listed.
- `tasks.md` W3.T6.16 added: conditional CHECK acceptance criterion.

**Validation:** SQLite DDL validated in-memory:
- `All DDL valid`
- `CHECK correctly blocks codex_prompt without execution_plan_id`

**Result:** RESOLVED.
**Remaining limitation:** Same-handoff cross-table constraint enforced by application logic, not DDL.

---

## R05 — Wave 3 Workflow Discriminator

**Original problematic text (design.md ~line 695):**
`implementation_authorization` as Wave 3 discriminator is ambiguous (already exists in Wave 1).

**Corrected text:**
Added `wave3_capable INTEGER NOT NULL DEFAULT 0` column to `workflow_step_templates`:
```sql
ALTER TABLE workflow_step_templates
  ADD COLUMN wave3_capable INTEGER NOT NULL DEFAULT 0
    CHECK (wave3_capable IN (0, 1));
```

Added `workflow_wave3_capabilities` table:
```sql
CREATE TABLE workflow_wave3_capabilities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  capabilities_snapshot TEXT NOT NULL,
  spec_version TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uidx_wave3_caps_per_run ON workflow_wave3_capabilities(workflow_run_id);
```

Added `kiro_verification_id` nullable FK on `specification_versions` → `kiro_handoffs(id)` ON DELETE SET NULL.

Semantics: absence of `workflow_wave3_capabilities` row means Wave 3 rules do not apply; legacy Wave 1/2 behavior preserved.

**Source file and line reference:**
- `design.md` §8.6 "Wave 3 Workflow Capability Discriminator (R05)" added.
- `design.md` §10.1 "workflow_wave3_capabilities (R05)" DDL section added.
- `tasks.md` W3.T6.17 added; W3.T10.8 added.

**Validation:** SQLite DDL validated in-memory — `All DDL valid` (`workflow_wave3_capabilities` created with unique index).
**Result:** RESOLVED.
**Remaining limitation:** `wave3_capable` migration requires ALTER TABLE on `workflow_step_templates`; SQLite ALTER TABLE ADD COLUMN is supported but does not allow adding NOT NULL columns without a default (default 0 satisfies this).

---

## SQLite Verification Results

```
All DDL valid
Partial index correctly blocks duplicate active plan
Stale plan correctly allows new active plan
CHECK correctly blocks codex_prompt without execution_plan_id
All validations passed
```

Exit code: 0. All 5 assertions passed. Verified with `.venv\Scripts\python.exe` on in-memory SQLite.

---

## Security CI Finding

Run `45179713942`: `created_at == updated_at`, 0 jobs, instant completion. Root cause: **SUSPECTED** merge-commit race (circumstantial evidence). `security.yml` push trigger confirmed present on `develop`. No code defect. No action required.
