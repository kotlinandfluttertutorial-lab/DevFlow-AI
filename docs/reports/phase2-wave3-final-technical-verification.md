# Wave 3 final technical verification

**Date** 2026-11-24 (Asia/Calcutta)
**Branch** feature/phase2-wave3-coding-agent-integration
**Baseline** 6d02e35, clean at preflight
**Target** develop, existing specification PR #25
**Result** v1.0.4 specification-only correction. No production source or operational database changed.

## Findings and evidence

| Finding | Correction | Executed evidence |
|---|---|---|
| R01 | Remove unconditional run/step uniqueness. Retain partial unique active-step index and add composite ownership FK. | The original DDL produced `UNIQUE constraint failed: execution_plans.workflow_run_id, execution_plans.workflow_step_run_id` after the original row became stale. Corrected DDL accepted the first active row, rejected simultaneous active row, allowed stale transition and replacement, retained two historical rows, accepted an independent active step and rejected cross-run ownership. An approved, unconsumed old binding could not satisfy the fresh binding predicate. |
| R02 | Specify typed `waiting_human` and lease-free `WAITING_HUMAN`, leaving existing `indeterminate` and approval behavior intact. | Existing orchestrator and recovery tests passed. The proposed SQLite CHECK admits the new state, the wait table rejects wrong reason/owner pairs and duplicate waits for one step attempt, and the populated downgrade restores the old CHECK. Runtime implementation of the new result contract is future Wave 3 work. |
| R03 | Join protected dispatch, authorization consumption, immutable prompt intent, attempt, report wait and audit in one transaction. Recover from stored bytes only. | An isolated transaction consumed a synthetic approval, inserted intent, changed plan/attempt and wrote audit, then rolled back. The approval stayed unconsumed and artifact, attempt and audit counts stayed zero. The commit path retained exact bytes and hash. Replay consumption returned zero. This is a SQLite transaction probe of the specification, not an executed Wave 3 recovery service. |
| R04 | Use `handoff_record_id` and `execution_plan_id` throughout. Validate same-handoff ownership on every write and read. | SQLite rejected a different handoff on an existing plan, missing plan on Codex prompt, a plan on a Kiro report, NULL hash, duplicate prompt and mutation of published hash. |
| R05 | Use `handoff_records(id)` for verification and trusted persisted run snapshots for capabilities. | The full DDL resolved all FK targets on a real 009 schema. SQLite rejected a dangling handoff FK, detached the verification FK when its handoff was deleted, defaulted a revision to NULL, and rejected duplicate or mutated capability snapshots. Existing runs stayed intact through populated upgrade and downgrade. Runtime fail-closed configuration checks are specified for implementation. |

## Existing orchestrator characterization

| Source | Observed contract |
|---|---|
| `src/devflow_api/schemas/executor.py:98` and Executor protocol below the result schema | The executor returns a validated result. There are four current outcomes. `indeterminate` requires an error, empty output/artifacts and a nonretryable error. There is no paused or report-wait variant. |
| `src/devflow_api/orchestration.py:126,188,249` | The run snapshots the workflow definition. Start/resume dispatches eligible steps from the immutable snapshot. The current scheduler has no human-report wait. |
| `src/devflow_api/orchestration.py:353,403,449` | Protected dispatch acquires an attempt lease. Heartbeats renew the fenced lease during execution. The executor wrapper validates results, handles timeout/cancellation and stops the heartbeat before settling the result. |
| `src/devflow_api/orchestration.py:533–603` | The retry predicate only accepts outcome failed, with retryable error and a safe/idempotent policy. Indeterminate maps to FAILED, retains result_status indeterminate, stores the structured error with attempt number, emits step_failed and derives run status. |
| `src/devflow_api/repositories/workflow_step_run.py:27–36,86,378,413` and model `workflow_step_run.py:12–21` | Transition allowlist and SQLite CHECK are independent. Finalization requires RUNNING, matching lease and attempt and clears the lease. A stale worker loses CAS. There is no WAITING_HUMAN in the current application. |
| `src/devflow_api/services/approval.py:131,185` and `repositories/workflow_step_run.py:271` | True approval gates persist a bound approval. The current protected wrapper owns the immediate transaction and calls consume_atomic with commit=False. It commits before returning, so calling it before a separate intent write would create a crash window. |
| `src/devflow_api/repositories/workflow_status.py:17,36`, `services/workflow_cancellation.py:22`, `services/recovery.py:26,52,102` | Run projection and cancellation settle workflow steps. Startup keeps live leases and interrupts expired RUNNING leases. Neither current approval reconciliation nor startup recovers a report wait. |

The required extension is defined in design §3.2.1 and §10.1. A typed two-reason wait needs a fenced CAS and distinct durable state. A report finalizes the existing fenced attempt only after validation, and retries of the same accepted report return the stored result. Ordinary approval semantics and the legacy protected-dispatch wrapper are preserved. Manual publication creates the report wait and releases the lease in the same transaction as the intent. Startup may finish artifact publication from the database bytes. It cannot launch a coding agent.

## Migration and verification commands

- `.venv/Scripts/python.exe .venv/wave3_spec_verify.py` exited **0**. The fixture invoked `python -m alembic upgrade 009_add_ai_specification_generation` with a temporary SQLite URL, seeded real legacy tables, and cloned the fixture via SQLite backup. It extracted the SQL blocks below directly from design §10.1. It executed the blocks in FK order with `PRAGMA foreign_keys=ON`, apart from the isolated physical batch rebuild which re-enabled enforcement after `foreign_key_check`. The physical rebuild used actual `sqlite_master` DDL, copied the rows and rebuilt indexes. The reverse path dropped the specification FK and Wave 3 objects, restored the original CHECK, preserved all three seeded step IDs and an existing approval FK, and ended with an empty `foreign_key_check`.
- `.venv/Scripts/python.exe -m pytest tests/unit/test_workflow_orchestration.py tests/unit/test_workflow_recovery.py tests/integration/test_specification_migration.py --no-cov -q` exited **0**, **72 passed in 35.42 s**. It includes `test_unsafe_or_indeterminate_not_retried[indeterminate]`, protected consumption, cancellation, timeout, lease recovery and legacy migration regression. These are existing source tests.
- The bound approval probe used the exact predicate below. The old approval row was present and approved, but the changed canonical binding rejected it. A fresh approved row succeeded once.

```sql
UPDATE approval_requests SET consumed_at=CURRENT_TIMESTAMP WHERE id=?
 AND workflow_run_id=1 AND workflow_step_run_id=1 AND operation='implementation_authorization'
 AND actor_id='operator' AND status='approved' AND consumed_at IS NULL
 AND authorization_expires_at>CURRENT_TIMESTAMP AND parameters_hash=?;
```

The following isolated fixture assertions were exercised on the full schema:

1. Original insert, stale transition and failed replacement returned the unconditional uniqueness error.
2. Corrected plan rows were exactly `[(1,"stale"),(2,"draft"),(3,"completed"),(4,"ready")]`. Cross-run insertion referencing a real step in another run returned a FK error.
3. Old approval consumption returned 0 with its consumed timestamp unchanged. Fresh approval returned 1. Rollback restored the consumed timestamp and left zero artifact, attempt or audit rows. Commit kept the exact bytes and SHA-256. Second consumption returned 0.
4. Artifact tests returned FK, CHECK, NOT NULL, UNIQUE and immutable intent errors for the respective bad writes.
5. Capability and verification tests rejected duplicate snapshot, update and dangling FK. Deleting an otherwise unreferenced handoff cleared its verification FK.
6. Manual wait fixture persisted `("WAITING_HUMAN",1,None)` for status, attempt and lease owner. A mismatched wait reason and owner failed CHECK. Duplicate step/attempt wait failed UNIQUE. After quiescing and downgrading, the old CHECK rejected WAITING_HUMAN.
7. Foreign-key checks were empty after applying the full DDL and after the downgrade. Legacy step IDs and an approval reference survived the rebuild.

The probe is in the ignored local virtual environment. Migration 010 itself and the new Wave 3 services are future implementation tasks. The dry run verifies the design and actual existing 009 schema, including the absence of a workflow_step_templates table. No GitHub Security CI result was inferred from local checks.

Offline scan used `docker run --rm --pull never --network none --mount "type=bind,source=$scanRoot,target=/scan,readonly" trufflesecurity/trufflehog:3.90.0 filesystem /scan --no-update --no-verification --fail --json`. The local read-only workspace snapshot scan exited 0 with 794 chunks and zero verified or unverified secrets.

## Verdict

Twelve implementation tasks and all 28 final acceptance criteria remain in scope. The specification retains manual and mock execution, blocks real execution throughout Wave 3, and keeps independent implementation authorization. No correctness blocker remains in the verified specification contracts. Production implementation must still deliver the new migration and lifecycle tests before release.

WAVE 3 FINAL TECHNICAL BLOCKERS RESOLVED

SPECIFICATION v1.0.4 READY FOR FREEZE

READY FOR KIRO PR #25 UPDATE

## Appendix — exact executed v1.0.4 DDL

Blocks below are copied verbatim from design §10.1 in execution order. The step status CHECK rebuild used the actual 009 CREATE statement and the documented one-value extension.

### handoff_records

```sql
CREATE UNIQUE INDEX uidx_wave3_step_run_owner ON workflow_step_runs(workflow_run_id, id);
CREATE TABLE handoff_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    specification_request_id INTEGER NOT NULL
        REFERENCES specification_requests(id) ON DELETE CASCADE,
    specification_version_id INTEGER NOT NULL
        REFERENCES specification_versions(id),
    artifact_digest TEXT NOT NULL,
    workflow_run_id INTEGER REFERENCES workflow_runs(id),
    workflow_step_run_id INTEGER REFERENCES workflow_step_runs(id),
    task_ids_json TEXT NOT NULL,
    prompt_text_hash TEXT,
    actor_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'handoff_created'
        CHECK (status IN ('handoff_created','awaiting_report','report_submitted',
                          'report_validated','failed','expired','cancelled',
                          'correction_requested')),
    prompt_exported_at TEXT,
    awaiting_since TEXT,
    expires_at TEXT,
    report_submitted_at TEXT,
    validated_at TEXT,
    failed_at TEXT,
    expired_at TEXT,
    cancelled_at TEXT,
    validation_error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (workflow_step_run_id IS NULL OR workflow_run_id IS NOT NULL),
    FOREIGN KEY (workflow_run_id, workflow_step_run_id) REFERENCES workflow_step_runs(workflow_run_id, id)
);
CREATE INDEX idx_hr_spec_version ON handoff_records(specification_version_id);
CREATE INDEX idx_hr_status ON handoff_records(status);
CREATE INDEX idx_hr_awaiting_since ON handoff_records(awaiting_since);
CREATE UNIQUE INDEX uidx_hr_active_per_version ON handoff_records(specification_version_id)
    WHERE status NOT IN ('report_validated','failed','expired','cancelled','correction_requested');
```

### execution_plans

```sql
CREATE TABLE execution_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id),
    workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),          -- NOT NULL: standalone plans not supported in Wave 3
    workflow_step_run_id INTEGER NOT NULL REFERENCES workflow_step_runs(id), -- NOT NULL: every plan must belong to a step
    specification_version_id INTEGER NOT NULL REFERENCES specification_versions(id),
    artifact_digest TEXT NOT NULL,
    task_ids_json TEXT NOT NULL,
    repo_identity TEXT NOT NULL,
    workspace_identity TEXT NOT NULL,
    baseline_commit   TEXT NOT NULL,   -- git commit SHA of the repo baseline; persisted before auth request
    deadline_at       TEXT,            -- optional execution deadline (UTC ISO 8601, may be NULL)
    execution_mode TEXT NOT NULL
        CHECK (execution_mode IN ('manual','mock','real_when_isolated')),  -- requested mode only; 'blocked' is an outcome state
    canonical_execution_input_hash TEXT NOT NULL,  -- not globally unique; scoped by step
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','ready','prompt_publishing','prompt_published','executing','completed','failed','cancelled','stale')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, handoff_record_id),
    FOREIGN KEY (workflow_run_id, workflow_step_run_id)
        REFERENCES workflow_step_runs(workflow_run_id, id)
);
CREATE INDEX idx_ep_handoff ON execution_plans(handoff_record_id);
CREATE INDEX idx_ep_hash ON execution_plans(canonical_execution_input_hash);
-- Only one ACTIVE plan per workflow step at a time (R01)
CREATE UNIQUE INDEX uidx_execution_plans_active_step
  ON execution_plans(workflow_step_run_id)
  WHERE status IN ('draft', 'ready', 'prompt_publishing', 'prompt_published', 'executing');
```

### handoff_artifacts

```sql
-- Owns Kiro prompts and Kiro reports (before execution attempt exists)
CREATE TABLE handoff_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id) ON DELETE CASCADE,
    execution_plan_id INTEGER REFERENCES execution_plans(id),  -- NOT NULL for codex_prompt artifacts; NULL for kiro_prompt/kiro_report
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('kiro_prompt','kiro_report','codex_prompt')),
    content_hash TEXT NOT NULL,          -- SHA-256 of content; required; never overwritten after publication
    content_size_bytes INTEGER NOT NULL,
    prompt_bytes BLOB,
    authorization_id INTEGER UNIQUE REFERENCES approval_requests(id),
    storage_key TEXT NOT NULL,          -- DevFlow-generated safe identifier; never client-supplied
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','prompt_publishing','published')),  -- for codex_prompt; kiro artifacts go directly to 'published'
    created_at TEXT NOT NULL,
    -- R04 ownership enforcement
    CHECK (artifact_type != 'codex_prompt' OR execution_plan_id IS NOT NULL),
    CHECK (artifact_type IN ('codex_prompt') OR execution_plan_id IS NULL),
    CHECK ((artifact_type = 'codex_prompt' AND prompt_bytes IS NOT NULL
            AND authorization_id IS NOT NULL AND length(prompt_bytes) = content_size_bytes)
        OR (artifact_type <> 'codex_prompt' AND prompt_bytes IS NULL AND authorization_id IS NULL)),
    FOREIGN KEY (execution_plan_id, handoff_record_id)
        REFERENCES execution_plans(id, handoff_record_id)
);
CREATE INDEX idx_ha_handoff ON handoff_artifacts(handoff_record_id);
CREATE INDEX idx_ha_plan ON handoff_artifacts(execution_plan_id);
CREATE INDEX idx_ha_hash ON handoff_artifacts(content_hash);
CREATE INDEX idx_ha_type ON handoff_artifacts(artifact_type);
-- R04: at most one immutable codex prompt per execution plan
CREATE UNIQUE INDEX uidx_codex_prompt_per_plan
  ON handoff_artifacts(execution_plan_id)
  WHERE artifact_type = 'codex_prompt' AND execution_plan_id IS NOT NULL;
CREATE TRIGGER trg_codex_prompt_intent_immutable
BEFORE UPDATE ON handoff_artifacts
WHEN OLD.artifact_type = 'codex_prompt' AND (
    NEW.artifact_type IS NOT OLD.artifact_type OR NEW.execution_plan_id IS NOT OLD.execution_plan_id
    OR NEW.handoff_record_id IS NOT OLD.handoff_record_id OR NEW.authorization_id IS NOT OLD.authorization_id
    OR NEW.prompt_bytes IS NOT OLD.prompt_bytes OR NEW.content_hash IS NOT OLD.content_hash
    OR NEW.content_size_bytes IS NOT OLD.content_size_bytes OR NEW.storage_key IS NOT OLD.storage_key
    OR (OLD.status = 'published' AND NEW.status <> 'published'))
BEGIN
    SELECT RAISE(ABORT, 'immutable Codex prompt intent');
END;
```

### handoff_findings

```sql
CREATE TABLE handoff_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('blocker','major','minor','informational')),
    is_non_overridable INTEGER NOT NULL DEFAULT 0 CHECK (is_non_overridable IN (0,1)),
    affected_artifact TEXT NOT NULL,
    description TEXT NOT NULL,          -- sanitized
    suggested_fix TEXT,                 -- sanitized
    override_justification TEXT,        -- min 20 chars when set
    acknowledged_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (handoff_record_id, finding_id)   -- unique finding_id within a handoff
);
CREATE INDEX idx_hf_handoff ON handoff_findings(handoff_record_id);
CREATE INDEX idx_hf_category ON handoff_findings(category);
```

### execution_attempts

```sql
CREATE TABLE execution_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_plan_id INTEGER NOT NULL REFERENCES execution_plans(id),
    execution_mode TEXT NOT NULL
        CHECK (execution_mode IN ('mock','manual','real')),
    status TEXT NOT NULL DEFAULT 'plan_prepared'
        CHECK (status IN ('plan_prepared','awaiting_impl_authorization',
                          'authorization_granted','dispatching','mock_execution',
                          'manual_handoff','real_execution_blocked','executing',
                          'awaiting_impl_report','impl_report_submitted',
                          'validating_report','completed','failed',
                          'cancelled','timeout','interrupted')),
    canonical_execution_input_hash TEXT NOT NULL,
    baseline_commit TEXT,
    deadline_at TEXT,
    issued_prompt_hash TEXT,            -- hash of the immutable prompt issued
    manual_handoff_created_at TEXT,
    report_submitted_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    timed_out_at TEXT,
    cancelled_at TEXT,
    interrupted_at TEXT,
    self_reported_verdict TEXT,
    devflow_verification_passed INTEGER CHECK (devflow_verification_passed IN (0,1)),
    error_code TEXT,
    error_message TEXT,                 -- sanitized
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_ea_plan ON execution_attempts(execution_plan_id);
CREATE INDEX idx_ea_status ON execution_attempts(status);
```

### execution_artifacts

```sql
-- Owns implementation artifacts produced during execution (reports, diffs, logs)
CREATE TABLE execution_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_attempt_id INTEGER NOT NULL
        REFERENCES execution_attempts(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('codex_report','diff','patch','test_report','log','other')),
    file_name TEXT NOT NULL,            -- sanitized; DevFlow-generated or sanitized client name
    content_hash TEXT NOT NULL,
    content_size_bytes INTEGER NOT NULL,
    storage_key TEXT NOT NULL,          -- DevFlow-generated safe storage identifier
    created_at TEXT NOT NULL
);
CREATE INDEX idx_exart_attempt ON execution_artifacts(execution_attempt_id);
CREATE INDEX idx_exart_hash ON execution_artifacts(content_hash);
```

### capability_records

```sql
CREATE TABLE capability_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    codex_cli_installed INTEGER NOT NULL CHECK (codex_cli_installed IN (0,1)),
    codex_cli_version TEXT,
    codex_cli_capability_status TEXT NOT NULL
        CHECK (codex_cli_capability_status IN ('unavailable','unverified','verified')),
    kiro_integration_type TEXT NOT NULL DEFAULT 'guided_handoff',
    isolation_status TEXT NOT NULL
        CHECK (isolation_status IN ('not_configured','prerequisites_not_met','prerequisites_met')),
    unmet_prerequisites_json TEXT,
    created_at TEXT NOT NULL
);
```

### workflow_wave3_capabilities

```sql
-- New table: immutable per-run snapshot of Wave 3 capabilities (R05)
CREATE TABLE workflow_wave3_capabilities (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_run_id       INTEGER NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  capabilities_snapshot TEXT NOT NULL,  -- JSON snapshot of enabled Wave 3 features
  spec_version          TEXT NOT NULL,  -- e.g. '1.0.3'
  created_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uidx_wave3_caps_per_run
  ON workflow_wave3_capabilities(workflow_run_id);
CREATE TRIGGER trg_wave3_capability_immutable
BEFORE UPDATE ON workflow_wave3_capabilities
BEGIN
    SELECT RAISE(ABORT, 'immutable Wave 3 capability snapshot');
END;

ALTER TABLE specification_versions
  ADD COLUMN kiro_verification_id INTEGER REFERENCES handoff_records(id) ON DELETE SET NULL;
```

### workflow_human_waits

```sql
CREATE TABLE workflow_human_waits (
    id INTEGER PRIMARY KEY,
    workflow_step_run_id INTEGER NOT NULL REFERENCES workflow_step_runs(id),
    step_attempt INTEGER NOT NULL CHECK (step_attempt > 0),
    token_hash TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL CHECK (reason IN ('WAITING_FOR_KIRO_REPORT','WAITING_FOR_IMPL_REPORT')),
    handoff_record_id INTEGER REFERENCES handoff_records(id),
    execution_attempt_id INTEGER REFERENCES execution_attempts(id),
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('waiting','submitted','expired','cancelled')),
    report_digest TEXT,
    UNIQUE(workflow_step_run_id, step_attempt),
    CHECK ((reason = 'WAITING_FOR_KIRO_REPORT' AND handoff_record_id IS NOT NULL AND execution_attempt_id IS NULL)
        OR (reason = 'WAITING_FOR_IMPL_REPORT' AND execution_attempt_id IS NOT NULL AND handoff_record_id IS NULL))
);
```
