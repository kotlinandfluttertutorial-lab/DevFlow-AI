"""Persisted confirmation races and migration-level retention rules."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devflow_api.adapters.live.github import GitHubAdapter
from devflow_api.database import _configure_sqlite, get_db
from devflow_api.main import create_app
from devflow_api.models import Base, GitHubConfirmation, MCPApproval
from devflow_api.repositories.github_confirmation import GitHubConfirmationRepository
from devflow_api.schemas.github import BranchSummary
from devflow_core.config import Settings, get_settings
from devflow_core.exceptions import ConfirmationAlreadyUsedError, ConfirmationMismatchError
from tests.conftest import ROOT


@pytest.fixture
async def file_sessions(sqlite_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{sqlite_path.as_posix()}")
    event.listen(engine.sync_engine, "connect", _configure_sqlite)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_concurrent_consumption_only_one_succeeds(
    file_sessions: async_sessionmaker[AsyncSession],
) -> None:
    id = str(uuid4())
    now = datetime.now(UTC)
    fields = {
        "repo_full_name": "owner/repo",
        "operation": "branch_create",
        "parameters_hash": "hash",
        "actor_id": "operator",
        "workflow_run_id": None,
    }
    async with file_sessions() as session:
        await GitHubConfirmationRepository(session).create(
            fields | {"id": id, "issued_at": now, "expires_at": now + timedelta(minutes=5)}
        )
    async with file_sessions() as first, file_sessions() as second:
        results = await asyncio.gather(
            *(
                GitHubConfirmationRepository(session).consume_atomic(
                    id,
                    now,
                    repo_full_name="owner/repo",
                    operation="branch_create",
                    parameters_hash="hash",
                    actor_id="operator",
                    workflow_run_id=None,
                )
                for session in (first, second)
            ),
            return_exceptions=True,
        )
    assert sum(isinstance(result, GitHubConfirmation) for result in results) == 1
    assert sum(isinstance(result, ConfirmationAlreadyUsedError) for result in results) == 1


async def test_concurrent_consumption_safe(
    file_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(GITHUB_TOKEN=SecretStr("synthetic")))

    async def session() -> AsyncIterator[AsyncSession]:
        async with file_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    monkeypatch.setattr(GitHubAdapter, "verify_repository_access", AsyncMock())
    monkeypatch.setattr(GitHubAdapter, "resolve_ref", AsyncMock(return_value="abc"))
    writes = AsyncMock(
        return_value=BranchSummary.model_validate({"name": "test", "commit": {"sha": "abc"}})
    )
    monkeypatch.setattr(GitHubAdapter, "create_branch", writes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        project = (await client.post("/api/v1/projects", json={"name": "Project"})).json()
        repo = (
            await client.post(
                "/api/v1/repositories",
                json={
                    "project_id": project["id"],
                    "owner": "owner",
                    "repo": "repo",
                },
            )
        ).json()
        params = {"name": "test", "base_ref": "main"}
        confirmation = (
            await client.post(
                "/api/v1/github-confirmations",
                json={
                    "repo_full_name": "owner/repo",
                    "operation": "branch_create",
                    "parameters": params,
                },
            )
        ).json()
        path = f"/api/v1/repositories/{repo['id']}/branches"
        body = params | {"confirmation_id": confirmation["id"]}
        responses = await asyncio.gather(client.post(path, json=body), client.post(path, json=body))
    assert sorted(response.status_code for response in responses) == [201, 409]
    writes.assert_awaited_once()


@pytest.mark.parametrize("invalidate", ["actor", "ttl", "missing"])
async def test_atomic_consumption_rechecks_persisted_binding_and_expiry(
    file_sessions: async_sessionmaker[AsyncSession], invalidate: str
) -> None:
    now = datetime.now(UTC)
    id = str(uuid4())
    expected: type[Exception] = ConfirmationAlreadyUsedError
    async with file_sessions() as session:
        repo = GitHubConfirmationRepository(session)
        if invalidate != "missing":
            await repo.create(
                {
                    "id": id,
                    "repo_full_name": "owner/repo",
                    "operation": "branch_create",
                    "parameters_hash": "hash",
                    "actor_id": "other" if invalidate == "actor" else "operator",
                    "issued_at": now - timedelta(minutes=5),
                    "expires_at": now + timedelta(seconds=-1 if invalidate == "ttl" else 30),
                }
            )
            if invalidate == "actor":
                expected = ConfirmationMismatchError
        with pytest.raises(expected):
            await repo.consume_atomic(
                id,
                now,
                repo_full_name="owner/repo",
                operation="branch_create",
                parameters_hash="hash",
                actor_id="operator",
                workflow_run_id=None,
            )
        if invalidate != "missing":
            row = await repo.get_by_id(id)
            assert row is not None and row.status == "ISSUED" and row.used_at is None


@pytest.fixture
def migrated(sqlite_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{sqlite_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "006_add_mcp_approvals")
    command.upgrade(config, "head")
    return config


def _objects(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master")}


@pytest.mark.integration
def test_migration_full_round_trip(migrated: Config, sqlite_path: Path) -> None:
    trigger = "trg_workflow_runs_before_delete_expire_github_confirmations"
    with closing(sqlite3.connect(sqlite_path)) as db:
        assert {"github_confirmations", "mcp_approvals", trigger} <= _objects(db)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "008_add_workflow_execution_engine",
        )
        command.downgrade(migrated, "006_add_mcp_approvals")
        assert not {"github_confirmations", trigger} & _objects(db)
        assert "mcp_approvals" in _objects(db)
        command.downgrade(migrated, "base")
        assert not {"workflows", "mcp_approvals", "repository_configs"} & _objects(db)
        command.upgrade(migrated, "head")
        assert {"github_confirmations", "mcp_approvals", trigger} <= _objects(db)
    command.check(migrated)


@pytest.fixture
def confirmation_db(migrated: Config, sqlite_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(sqlite_path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO workflows (id,name,created_at,updated_at) "
            "VALUES (1,'W',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO workflow_runs (id,workflow_id,created_at,updated_at) "
            "VALUES (1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.executemany(
            "INSERT INTO github_confirmations (id,repo_full_name,operation,parameters_hash,"
            "actor_id,workflow_run_id,issued_at,expires_at,status) "
            "VALUES (?,'owner/repo','branch_create','hash','operator',?,"
            "CURRENT_TIMESTAMP,'2099-01-01',?)",
            [("bound", 1, "ISSUED"), ("unbound", None, "ISSUED"), ("used", 1, "USED")],
        )
        db.commit()
        yield db


@pytest.mark.integration
@pytest.mark.parametrize("cascade", [False, True])
async def test_workflow_run_deletion_expires_github_confirmation(
    confirmation_db: sqlite3.Connection, sqlite_path: Path, cascade: bool
) -> None:
    db = confirmation_db
    if cascade:
        db.execute("DELETE FROM workflows WHERE id=1")
    else:
        db.execute("DELETE FROM workflow_runs WHERE id=1")
    query = "SELECT id,status,workflow_run_id FROM github_confirmations ORDER BY id"
    assert db.execute(query).fetchall() == [
        ("bound", "EXPIRED", None),
        ("unbound", "ISSUED", None),
        ("used", "USED", None),
    ]
    assert db.execute("SELECT event_type FROM workflow_audit_log").fetchall() == [
        ("github_confirmation_expired",),
    ]
    db.rollback()
    assert db.execute(query).fetchall()[0] == ("bound", "ISSUED", 1)
    assert db.execute("SELECT count(*) FROM workflow_audit_log").fetchone() == (0,)
    db.execute("DELETE FROM workflow_runs WHERE id=1")
    db.commit()
    engine = create_async_engine(f"sqlite+aiosqlite:///{sqlite_path.as_posix()}")
    try:
        async with AsyncSession(engine) as session:
            repo = GitHubConfirmationRepository(session)
            with pytest.raises(ConfirmationAlreadyUsedError):
                await repo.consume_atomic(
                    "bound",
                    datetime.now(UTC),
                    repo_full_name="owner/repo",
                    operation="branch_create",
                    parameters_hash="hash",
                    actor_id="operator",
                    workflow_run_id=None,
                )
    finally:
        await engine.dispose()


def test_mcp_approval_model_importable() -> None:
    assert MCPApproval.__tablename__ == "mcp_approvals"
