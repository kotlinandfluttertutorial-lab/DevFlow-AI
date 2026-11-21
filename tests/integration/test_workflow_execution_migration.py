"""Migration 008 exercises isolated SQLite databases, including populated legacy tables."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from devflow_core.config import get_settings
from tests.conftest import ROOT


def test_migration_008_upgrade_downgrade_and_legacy_roundtrip(
    sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{sqlite_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "007_add_github_confirmations")
    with closing(sqlite3.connect(sqlite_path)) as db, db:
        db.execute(
            "INSERT INTO workflows (name,created_at,updated_at) "
            "VALUES ('legacy',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO workflow_runs (workflow_id,created_at,updated_at) "
            "VALUES (1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO approval_requests "
            "(workflow_run_id,step_name,action_description,requested_at,created_at,updated_at) "
            "VALUES (1,'legacy','approve',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO approval_requests "
            "(id,workflow_run_id,step_name,action_description,requested_at,created_at,updated_at) "
            "VALUES (100,1,'deleted','approve',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute("DELETE FROM approval_requests WHERE id=100")
        before = {
            table: db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            for table in ("workflow_runs", "approval_requests")
        }
    for _ in range(2):
        command.upgrade(config, "008_add_workflow_execution_engine")
        command.check(config)
        with closing(sqlite3.connect(sqlite_path)) as db, db:
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute(
                "SELECT template_snapshot_json,template_version,template_hash " "FROM workflow_runs"
            ).fetchone() == (None, None, None)
            assert db.execute(
                "SELECT operation,consumed_at,expires_at " "FROM approval_requests"
            ).fetchone() == (None, None, None)
            index_sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE name='uidx_approval_step_pending'"
            ).fetchone()[0]
            assert "UNIQUE INDEX" in index_sql
            assert "status = 'pending' AND operation IS NOT NULL" in index_sql
            assert (
                db.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='approval_requests'"
                ).fetchone()[0]
                >= 100
            )
            assert db.execute("PRAGMA foreign_key_list(approval_requests)").fetchall()
            for table in before:
                sql = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[
                    0
                ]
                old_check = before[table].split("CHECK (")[1].split(")")[0]
                assert f"CHECK ({old_check})" in sql
        command.downgrade(config, "007_add_github_confirmations")
        with closing(sqlite3.connect(sqlite_path)) as db:
            assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "007_add_github_confirmations",
            )
            assert db.execute("SELECT name FROM workflows").fetchall() == [("legacy",)]
            assert db.execute("SELECT step_name FROM approval_requests").fetchall() == [("legacy",)]
            assert (
                db.execute(
                    "SELECT name FROM sqlite_master WHERE name IN "
                    "('workflow_step_runs','uidx_approval_step_pending')"
                ).fetchall()
                == []
            )
            assert "template_hash" not in {
                row[1] for row in db.execute("PRAGMA table_info(workflow_runs)")
            }
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert (
                db.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='approval_requests'"
                ).fetchone()[0]
                >= 100
            )
    command.upgrade(config, "head")


def test_migration_008_downgrades_populated_phase2_rows(
    sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{sqlite_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    with closing(sqlite3.connect(sqlite_path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workflows (name,created_at,updated_at) "
            "VALUES ('phase2',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO workflow_runs (workflow_id,status,created_at,updated_at) "
            "VALUES (1,'waiting_approval',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO workflow_step_runs "
            "(workflow_run_id,step_key,step_version,executor_id,status,created_at,updated_at) "
            "VALUES (1,'gate',1,'mock','WAITING_APPROVAL',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO approval_requests "
            "(workflow_run_id,workflow_step_run_id,operation,step_name,action_description,"
            "requested_at,created_at,updated_at) "
            "VALUES (1,1,'implementation_authorization','gate','approve',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO workflow_step_runs "
                "(workflow_run_id,step_key,step_version,executor_id,status,created_at,updated_at) "
                "VALUES (1,'bad',1,'mock','SKIPPED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
    command.downgrade(config, "-1")
    with closing(sqlite3.connect(sqlite_path)) as db:
        assert db.execute("SELECT status FROM workflow_runs").fetchone() == ("waiting_approval",)
        assert db.execute("SELECT step_name FROM approval_requests").fetchone() == ("gate",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    command.upgrade(config, "head")
    command.check(config)
