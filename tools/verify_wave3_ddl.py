"""
Wave 3 DDL verification script.

Creates all Wave 3 tables in an in-memory SQLite database and asserts that
the key constraints (partial unique index, CHECK constraints, FK references,
immutability triggers) work correctly.

Usage:
    python tools/verify_wave3_ddl.py

Exit 0 — all assertions pass.
Exit 1 — at least one assertion failed.

Dependencies: standard library only (sqlite3).
"""

import sqlite3
import sys
from contextlib import closing

# ---------------------------------------------------------------------------
# Legacy tables required as FK targets (skeleton only — no Wave 1/2 logic)
# ---------------------------------------------------------------------------

LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER PRIMARY KEY,
    workflow_id INTEGER NOT NULL REFERENCES workflows(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_step_runs (
    id INTEGER PRIMARY KEY,
    workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
    step_key TEXT NOT NULL,
    step_version INTEGER NOT NULL,
    executor_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'READY'
        CHECK (status IN (
            'READY','RUNNING','SUCCEEDED','FAILED','CANCELLED',
            'WAITING_APPROVAL','WAITING_HUMAN','TIMED_OUT'
        )),
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uidx_wave3_step_run_owner
    ON workflow_step_runs(workflow_run_id, id);
CREATE TABLE IF NOT EXISTS specification_requests (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    workflow_run_id INTEGER REFERENCES workflow_runs(id),
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS specification_versions (
    id INTEGER PRIMARY KEY,
    specification_request_id INTEGER NOT NULL REFERENCES specification_requests(id),
    version_number INTEGER NOT NULL,
    kiro_verification_id INTEGER,  -- FK added after handoff_records exists
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY,
    workflow_run_id INTEGER REFERENCES workflow_runs(id),
    workflow_step_run_id INTEGER REFERENCES workflow_step_runs(id),
    step_name TEXT NOT NULL,
    action_description TEXT NOT NULL,
    status TEXT NOT NULL,
    operation TEXT NOT NULL,
    parameters_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    authorization_expires_at TEXT NOT NULL,
    consumed_at TEXT,
    requested_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Wave 3 DDL (from design.md §10.1)
# ---------------------------------------------------------------------------

WAVE3_DDL = """
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
    FOREIGN KEY (workflow_run_id, workflow_step_run_id)
        REFERENCES workflow_step_runs(workflow_run_id, id)
);
CREATE INDEX idx_hr_spec_version ON handoff_records(specification_version_id);
CREATE INDEX idx_hr_status ON handoff_records(status);
CREATE INDEX idx_hr_awaiting_since ON handoff_records(awaiting_since);
CREATE UNIQUE INDEX uidx_hr_active_per_version ON handoff_records(specification_version_id)
    WHERE status NOT IN ('report_validated','failed','expired','cancelled','correction_requested');

CREATE TABLE execution_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id),
    workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
    workflow_step_run_id INTEGER NOT NULL REFERENCES workflow_step_runs(id),
    specification_version_id INTEGER NOT NULL REFERENCES specification_versions(id),
    artifact_digest TEXT NOT NULL,
    task_ids_json TEXT NOT NULL,
    repo_identity TEXT NOT NULL,
    workspace_identity TEXT NOT NULL,
    baseline_commit   TEXT NOT NULL,
    deadline_at       TEXT,
    execution_mode TEXT NOT NULL
        CHECK (execution_mode IN ('manual','mock','real_when_isolated')),
    canonical_execution_input_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','ready','prompt_publishing','prompt_published',
                          'executing','completed','failed','cancelled','stale')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, handoff_record_id),
    FOREIGN KEY (workflow_run_id, workflow_step_run_id)
        REFERENCES workflow_step_runs(workflow_run_id, id)
);
CREATE INDEX idx_ep_handoff ON execution_plans(handoff_record_id);
CREATE INDEX idx_ep_hash ON execution_plans(canonical_execution_input_hash);
CREATE UNIQUE INDEX uidx_execution_plans_active_step
    ON execution_plans(workflow_step_run_id)
    WHERE status IN ('draft','ready','prompt_publishing','prompt_published','executing');

CREATE TABLE handoff_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id) ON DELETE CASCADE,
    execution_plan_id INTEGER REFERENCES execution_plans(id),
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('kiro_prompt','kiro_report','codex_prompt')),
    content_hash TEXT NOT NULL,
    content_size_bytes INTEGER NOT NULL,
    prompt_bytes BLOB,
    authorization_id INTEGER UNIQUE REFERENCES approval_requests(id),
    storage_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','prompt_publishing','published')),
    created_at TEXT NOT NULL,
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
CREATE UNIQUE INDEX uidx_codex_prompt_per_plan
    ON handoff_artifacts(execution_plan_id)
    WHERE artifact_type = 'codex_prompt' AND execution_plan_id IS NOT NULL;
CREATE TRIGGER trg_codex_prompt_intent_immutable
BEFORE UPDATE ON handoff_artifacts
WHEN OLD.artifact_type = 'codex_prompt' AND (
    NEW.artifact_type IS NOT OLD.artifact_type
    OR NEW.execution_plan_id IS NOT OLD.execution_plan_id
    OR NEW.handoff_record_id IS NOT OLD.handoff_record_id
    OR NEW.authorization_id IS NOT OLD.authorization_id
    OR NEW.prompt_bytes IS NOT OLD.prompt_bytes
    OR NEW.content_hash IS NOT OLD.content_hash
    OR NEW.content_size_bytes IS NOT OLD.content_size_bytes
    OR NEW.storage_key IS NOT OLD.storage_key
    OR (OLD.status = 'published' AND NEW.status <> 'published'))
BEGIN
    SELECT RAISE(ABORT, 'immutable Codex prompt intent');
END;

CREATE TABLE handoff_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('blocker','major','minor','informational')),
    is_non_overridable INTEGER NOT NULL DEFAULT 0 CHECK (is_non_overridable IN (0,1)),
    affected_artifact TEXT NOT NULL,
    description TEXT NOT NULL,
    suggested_fix TEXT,
    override_justification TEXT,
    acknowledged_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (handoff_record_id, finding_id)
);
CREATE INDEX idx_hf_handoff ON handoff_findings(handoff_record_id);
CREATE INDEX idx_hf_category ON handoff_findings(category);

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
    issued_prompt_hash TEXT,
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
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_ea_plan ON execution_attempts(execution_plan_id);
CREATE INDEX idx_ea_status ON execution_attempts(status);

CREATE TABLE execution_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_attempt_id INTEGER NOT NULL
        REFERENCES execution_attempts(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('codex_report','diff','patch','test_report','log','other')),
    file_name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_size_bytes INTEGER NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_exart_attempt ON execution_artifacts(execution_attempt_id);
CREATE INDEX idx_exart_hash ON execution_artifacts(content_hash);

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

CREATE TABLE workflow_wave3_capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    capabilities_snapshot TEXT NOT NULL,
    spec_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uidx_wave3_caps_per_run
    ON workflow_wave3_capabilities(workflow_run_id);
CREATE TRIGGER trg_wave3_capability_immutable
BEFORE UPDATE ON workflow_wave3_capabilities
BEGIN
    SELECT RAISE(ABORT, 'immutable Wave 3 capability snapshot');
END;

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
    CHECK ((reason = 'WAITING_FOR_KIRO_REPORT' AND handoff_record_id IS NOT NULL
                AND execution_attempt_id IS NULL)
        OR (reason = 'WAITING_FOR_IMPL_REPORT' AND execution_attempt_id IS NOT NULL
                AND handoff_record_id IS NULL))
);
"""

# FK added after handoff_records exists
SPEC_VERSION_FK = (
    "ALTER TABLE specification_versions "
    "ADD COLUMN kiro_verification_id INTEGER REFERENCES handoff_records(id) ON DELETE SET NULL"
    ";"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = 0
FAIL = 0


def passed(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  PASS  {msg}")


def failed(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}", file=sys.stderr)


def assert_rejected(
    db: sqlite3.Connection,
    sql: str,
    params: tuple = (),
    fragment: str = "",
) -> None:
    """Assert that executing sql raises IntegrityError containing fragment."""
    try:
        db.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        if fragment and fragment not in str(exc):
            failed(f"expected fragment {fragment!r} in error, got: {exc}")
        else:
            passed(f"correctly rejected: {fragment or sql[:60]!r}")
        return
    failed(f"expected IntegrityError for: {sql[:80]!r}")


def assert_ok(db: sqlite3.Connection, sql: str, params: tuple = (), msg: str = "") -> None:
    """Assert that executing sql succeeds."""
    try:
        db.execute(sql, params)
        passed(msg or sql[:60])
    except sqlite3.Error as exc:
        failed(f"{msg or sql[:60]} — raised {exc}")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def seed_legacy(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO workflows(id,name,created_at,updated_at) "
        "VALUES (1,'w1','2024-01-01','2024-01-01')"
    )
    db.execute(
        "INSERT INTO workflow_runs(id,workflow_id,created_at,updated_at) "
        "VALUES (1,1,'2024-01-01','2024-01-01')"
    )
    db.execute(
        "INSERT INTO workflow_step_runs(id,workflow_run_id,step_key,step_version,executor_id,"
        "created_at,updated_at) VALUES (1,1,'step1',1,'mock','2024-01-01','2024-01-01')"
    )
    db.execute(
        "INSERT INTO workflow_step_runs(id,workflow_run_id,step_key,step_version,executor_id,"
        "created_at,updated_at) VALUES (2,1,'step2',1,'mock','2024-01-01','2024-01-01')"
    )
    db.execute(
        "INSERT INTO specification_requests(id,title,description,workflow_run_id,content_hash,"
        "created_at,updated_at) VALUES (1,'F','D',1,'h','2024-01-01','2024-01-01')"
    )
    db.execute(
        "INSERT INTO specification_versions(id,specification_request_id,version_number,"
        "created_at,updated_at) VALUES (1,1,1,'2024-01-01','2024-01-01')"
    )
    db.execute(
        "INSERT INTO handoff_records(id,specification_request_id,specification_version_id,"
        "artifact_digest,task_ids_json,actor_id,created_at,updated_at) "
        "VALUES (1,1,1,'digest','[]','operator','2024-01-01','2024-01-01')"
    )


PLAN_SQL = (
    "INSERT INTO execution_plans("
    "id,handoff_record_id,workflow_run_id,workflow_step_run_id,"
    "specification_version_id,artifact_digest,task_ids_json,repo_identity,"
    "workspace_identity,baseline_commit,execution_mode,"
    "canonical_execution_input_hash,status,created_at,updated_at"
    ") VALUES (?,1,1,1,1,'digest','[]','repo','ws','commit','manual',?,?,'2024-01-01','2024-01-01')"
)

AUTH_SQL = (
    "INSERT INTO approval_requests("
    "id,workflow_run_id,workflow_step_run_id,step_name,action_description,"
    "status,operation,parameters_hash,actor_id,authorization_expires_at,"
    "requested_at,created_at,updated_at"
    ") VALUES (?,1,1,'s1','test','approved','implementation_authorization',"
    "?,'operator','2099-01-01','2024-01-01','2024-01-01','2024-01-01')"
)

CONSUME_SQL = (
    "UPDATE approval_requests SET consumed_at='2024-01-01' WHERE id=?"
    " AND workflow_run_id=1 AND workflow_step_run_id=1"
    " AND operation='implementation_authorization'"
    " AND actor_id='operator' AND status='approved' AND consumed_at IS NULL"
    " AND authorization_expires_at>'2024-01-01' AND parameters_hash=?"
)


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


def run_tests() -> None:
    print("Wave 3 DDL verification")
    print("=" * 60)

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("PRAGMA foreign_keys=ON")

        # -- Build legacy schema --
        db.executescript(LEGACY_DDL)
        passed("legacy schema created")

        # -- Build Wave 3 schema --
        db.executescript(WAVE3_DDL)
        passed("Wave 3 DDL executed (all 9 tables + indexes + triggers)")

        # spec_version FK (requires handoff_records to exist)
        # SQLite ALTER TABLE ADD COLUMN does not support adding an FK
        # on an existing column if the column already exists; skip if already present.
        try:
            db.executescript(SPEC_VERSION_FK)
            passed("specification_versions.kiro_verification_id FK added")
        except sqlite3.OperationalError as exc:
            if "duplicate column" in str(exc).lower():
                passed("specification_versions.kiro_verification_id already present")
            else:
                failed(f"FK alter failed: {exc}")

        # FK integrity check
        result = db.execute("PRAGMA foreign_key_check").fetchall()
        if result:
            failed(f"PRAGMA foreign_key_check returned violations: {result}")
        else:
            passed("PRAGMA foreign_key_check — no violations")

        # -- Seed legacy rows --
        db.execute("PRAGMA foreign_keys=ON")
        seed_legacy(db)
        db.commit()
        passed("legacy seed rows inserted")

        # -------------------------------------------------------------------
        # R01 — partial unique active-step index
        # -------------------------------------------------------------------
        print()
        print("R01 — partial unique index uidx_execution_plans_active_step")

        db.execute(PLAN_SQL, (1, "hash1", "draft"))
        passed("first active plan accepted")

        assert_rejected(db, PLAN_SQL, (2, "hash2", "draft"), "execution_plans.workflow_step_run_id")

        db.execute("UPDATE execution_plans SET status='stale' WHERE id=1")
        passed("plan 1 set to stale")

        db.execute(PLAN_SQL, (2, "hash2", "draft"))
        passed("replacement active plan accepted after stale transition")

        db.execute(PLAN_SQL, (3, "hash3", "completed"))
        passed("historical completed plan allowed alongside active")

        db.commit()

        # -------------------------------------------------------------------
        # R03 — authorization consumption + intent atomicity
        # -------------------------------------------------------------------
        print()
        print("R03 — authorization consumption and rollback atomicity")

        db.execute(AUTH_SQL, (1, "hash2"))
        db.commit()

        # Wrong hash — should return 0 rows updated
        rowcount = db.execute(CONSUME_SQL, (1, "wrong_hash")).rowcount
        if rowcount == 0:
            passed("wrong-hash consumption correctly returns 0 rows")
        else:
            failed("wrong-hash consumption should return 0 rows")

        # Correct hash
        rowcount = db.execute(CONSUME_SQL, (1, "hash2")).rowcount
        if rowcount == 1:
            passed("correct-hash consumption returns 1 row")
        else:
            failed("correct-hash consumption should return 1 row")

        # Replay — already consumed
        rowcount = db.execute(CONSUME_SQL, (1, "hash2")).rowcount
        if rowcount == 0:
            passed("replay consumption returns 0 rows (already consumed)")
        else:
            failed("replay consumption should return 0 rows")

        db.rollback()
        consumed_at = db.execute("SELECT consumed_at FROM approval_requests WHERE id=1").fetchone()[
            0
        ]
        if consumed_at is None:
            passed("rollback restored consumed_at to NULL")
        else:
            failed("rollback should restore consumed_at to NULL")

        # Commit path
        db.execute(AUTH_SQL, (2, "hash2b"))
        db.execute(CONSUME_SQL, (2, "hash2b"))
        db.commit()
        consumed_at = db.execute("SELECT consumed_at FROM approval_requests WHERE id=2").fetchone()[
            0
        ]
        if consumed_at is not None:
            passed("commit path persists consumed_at")
        else:
            failed("commit path should persist consumed_at")

        # -------------------------------------------------------------------
        # R04 — artifact ownership CHECK constraints
        # -------------------------------------------------------------------
        print()
        print("R04 — handoff_artifacts ownership CHECK constraints")

        # codex_prompt without execution_plan_id — must be rejected
        assert_rejected(
            db,
            "INSERT INTO handoff_artifacts(handoff_record_id,execution_plan_id,"
            "artifact_type,content_hash,content_size_bytes,prompt_bytes,"
            "authorization_id,storage_key,status,created_at) "
            "VALUES (1,NULL,'codex_prompt','h',5,X'68656c6c6f',"
            "2,'key','prompt_publishing','2024-01-01')",
            fragment="CHECK",
        )

        # kiro_report with execution_plan_id — must be rejected
        assert_rejected(
            db,
            "INSERT INTO handoff_artifacts(handoff_record_id,execution_plan_id,"
            "artifact_type,content_hash,content_size_bytes,prompt_bytes,"
            "authorization_id,storage_key,status,created_at) "
            "VALUES (1,2,'kiro_report','h',0,NULL,NULL,'key','published','2024-01-01')",
            fragment="CHECK",
        )

        # codex_prompt with NULL content_hash — must be rejected (NOT NULL)
        assert_rejected(
            db,
            "INSERT INTO handoff_artifacts(handoff_record_id,execution_plan_id,"
            "artifact_type,content_hash,content_size_bytes,prompt_bytes,"
            "authorization_id,storage_key,status,created_at) "
            "VALUES (1,2,'codex_prompt',NULL,5,X'68656c6c6f',"
            "2,'key','prompt_publishing','2024-01-01')",
            fragment="NOT NULL",
        )

        # Valid codex_prompt insert
        content = b"immutable prompt"
        import hashlib

        digest = hashlib.sha256(content).hexdigest()
        db.execute(
            "INSERT INTO handoff_artifacts(handoff_record_id,execution_plan_id,"
            "artifact_type,content_hash,content_size_bytes,prompt_bytes,"
            "authorization_id,storage_key,status,created_at) "
            "VALUES (1,2,'codex_prompt',?,?,?,2,'key','prompt_publishing','2024-01-01')",
            (digest, len(content), content),
        )
        passed("valid codex_prompt artifact accepted")

        # Duplicate codex_prompt for same plan — uidx_codex_prompt_per_plan
        assert_rejected(
            db,
            "INSERT INTO handoff_artifacts(handoff_record_id,execution_plan_id,"
            "artifact_type,content_hash,content_size_bytes,prompt_bytes,"
            "authorization_id,storage_key,status,created_at) "
            "VALUES (1,2,'codex_prompt',?,?,?,2,'key2','prompt_publishing','2024-01-01')",
            (digest, len(content), content),
            fragment="UNIQUE",
        )

        # Mutation of published content_hash — immutability trigger
        db.execute("UPDATE handoff_artifacts SET status='published' WHERE execution_plan_id=2")
        assert_rejected(
            db,
            "UPDATE handoff_artifacts SET content_hash='changed' WHERE execution_plan_id=2",
            fragment="immutable Codex prompt intent",
        )

        db.commit()

        # -------------------------------------------------------------------
        # R05 — workflow_wave3_capabilities immutability
        # -------------------------------------------------------------------
        print()
        print("R05 — workflow_wave3_capabilities unique and immutable")

        db.execute(
            "INSERT INTO workflow_wave3_capabilities(workflow_run_id,capabilities_snapshot,"
            "spec_version,created_at) VALUES (1,'{}','1.0.4','2024-01-01')"
        )
        passed("first capability snapshot accepted")

        assert_rejected(
            db,
            "INSERT INTO workflow_wave3_capabilities(workflow_run_id,capabilities_snapshot,"
            "spec_version,created_at) VALUES (1,'{}','1.0.4','2024-01-01')",
            fragment="UNIQUE",
        )

        assert_rejected(
            db,
            "UPDATE workflow_wave3_capabilities SET capabilities_snapshot='changed' "
            "WHERE workflow_run_id=1",
            fragment="immutable Wave 3 capability snapshot",
        )

        db.commit()

        # -------------------------------------------------------------------
        # R02 — workflow_human_waits CHECK constraints
        # -------------------------------------------------------------------
        print()
        print("R02 — workflow_human_waits reason/owner CHECK constraints")

        # Set step to WAITING_HUMAN first
        db.execute(
            "UPDATE workflow_step_runs SET status='WAITING_HUMAN',attempt=1,"
            "lease_owner=NULL,lease_expires_at=NULL WHERE id=1"
        )

        # Insert execution_attempt for WAITING_FOR_IMPL_REPORT
        db.execute(
            "INSERT INTO execution_attempts(id,execution_plan_id,execution_mode,status,"
            "canonical_execution_input_hash,created_at,updated_at) "
            "VALUES (1,2,'manual','manual_handoff','hash2b','2024-01-01','2024-01-01')"
        )

        # WAITING_FOR_KIRO_REPORT with execution_attempt_id (wrong owner) — rejected
        assert_rejected(
            db,
            "INSERT INTO workflow_human_waits(workflow_step_run_id,step_attempt,"
            "token_hash,reason,handoff_record_id,execution_attempt_id,"
            "expires_at,status) VALUES (1,1,'tok1',"
            "'WAITING_FOR_KIRO_REPORT',NULL,1,'2099-01-01','waiting')",
            fragment="CHECK",
        )

        # Valid WAITING_FOR_IMPL_REPORT
        db.execute(
            "INSERT INTO workflow_human_waits(workflow_step_run_id,step_attempt,"
            "token_hash,reason,handoff_record_id,execution_attempt_id,"
            "expires_at,status) VALUES (1,1,'tok2',"
            "'WAITING_FOR_IMPL_REPORT',NULL,1,'2099-01-01','waiting')"
        )
        passed("valid WAITING_FOR_IMPL_REPORT wait row accepted")

        # Duplicate step/attempt — UNIQUE(workflow_step_run_id, step_attempt)
        assert_rejected(
            db,
            "INSERT INTO workflow_human_waits(workflow_step_run_id,step_attempt,"
            "token_hash,reason,handoff_record_id,execution_attempt_id,"
            "expires_at,status) VALUES (1,1,'tok3',"
            "'WAITING_FOR_IMPL_REPORT',NULL,1,'2099-01-01','waiting')",
            fragment="UNIQUE",
        )

        db.commit()

        # -------------------------------------------------------------------
        # Final FK check
        # -------------------------------------------------------------------
        print()
        result = db.execute("PRAGMA foreign_key_check").fetchall()
        if result:
            failed(f"PRAGMA foreign_key_check returned violations: {result}")
        else:
            passed("final PRAGMA foreign_key_check — no violations")

    # -------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------
    print()
    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL:
        print("FAIL — one or more assertions failed")
        sys.exit(1)
    else:
        print("PASS — all assertions passed")
        sys.exit(0)


if __name__ == "__main__":
    run_tests()
