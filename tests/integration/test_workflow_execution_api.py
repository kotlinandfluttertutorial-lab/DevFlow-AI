"""Workflow engine HTTP integration, validation, and redaction."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from devflow_api.executors.mock import MockExecutor
from devflow_api.main import create_app
from devflow_api.schemas.executor import ExecutionContext, StepExecutionResult
from devflow_core.config import Settings
from tests.conftest import ROOT
from tests.unit.test_workflow_execution_repositories import Factory

pytest_plugins = ["tests.unit.test_workflow_execution_repositories"]


async def new_run(
    client: httpx.AsyncClient, *, protected: bool = False
) -> tuple[int, dict[str, object]]:
    definition = {
        "template_version": 1,
        "steps": [
            {
                "key": "step",
                "version": 1,
                "title": "Mock step",
                "executor_id": "mock",
                "tool_selection": {"executor_id": "mock"},
                "approvable": protected,
                "protected_operation": "implementation_authorization" if protected else None,
            }
        ],
    }
    response = await client.post(
        "/api/v1/workflows",
        json={
            "name": "Execution",
            "definition_json": json.dumps(definition),
        },
    )
    assert response.status_code == 201
    workflow_id = response.json()["id"]
    response = await client.post(f"/api/v1/workflows/{workflow_id}/runs")
    assert response.status_code == 201
    return workflow_id, response.json()


async def test_create_start_detail_and_terminal_contract(test_client: httpx.AsyncClient) -> None:
    workflow_id, run = await new_run(test_client)
    run_id = run["id"]
    assert run["status"] == "pending"
    result = (await test_client.post(f"/api/v1/workflow-runs/{run_id}/start", json={})).json()
    assert result["status"] == "completed"
    step = result["steps"][0]
    assert step["status"] == "SUCCEEDED" and step["attempt"] == 1
    assert step["output"] == {"echo": {}, "marker": "mock-v1"}
    assert "inputs_json" not in step and "lease_owner" not in step
    assert (await test_client.get(f"/api/v1/workflow-runs/{run_id}/steps")).json() == [step]
    assert (
        await test_client.get(f"/api/v1/workflow-runs/{run_id}/steps/{step['id']}")
    ).json() == step
    assert (await test_client.get(f"/api/v1/workflows/{workflow_id}/runs")).json() == [result]
    terminal = await test_client.post(f"/api/v1/workflow-runs/{run_id}/start")
    assert terminal.status_code == 409 and terminal.json()["code"] == "RUN_NOT_STARTABLE"
    assert (await test_client.post(f"/api/v1/workflow-runs/{run_id}/cancel")).json() == result


async def test_invalid_template_is_validated_at_run_creation(
    test_client: httpx.AsyncClient,
) -> None:
    response = await test_client.post(
        "/api/v1/workflows", json={"name": "Invalid", "definition_json": "{}"}
    )
    assert response.status_code == 201
    workflow_id = response.json()["id"]
    response = await test_client.post(f"/api/v1/workflows/{workflow_id}/runs")
    assert response.status_code == 422 and response.json()["code"] == "INVALID_WORKFLOW_TEMPLATE"
    assert (await test_client.get(f"/api/v1/workflows/{workflow_id}/runs")).json() == []
    await test_client.put(f"/api/v1/workflows/{workflow_id}", json={"definition_json": None})
    response = await test_client.post(f"/api/v1/workflows/{workflow_id}/runs")
    assert response.status_code == 201 and response.json()["steps"] == []


async def test_approval_http_flow_uses_server_operator(
    test_client: httpx.AsyncClient, api_app: FastAPI
) -> None:
    api_app.state.settings = Settings(LOCAL_OPERATOR_ID="server_operator")
    _, run = await new_run(test_client, protected=True)
    run_id = run["id"]
    response = await test_client.post(f"/api/v1/workflow-runs/{run_id}/start")
    assert response.status_code == 200
    approval = response.json()["pending_approvals"][0]
    assert approval["actor_id"] == "server_operator" and approval["expires_at"] is not None
    response = await test_client.post(
        f"/api/v1/approvals/{approval['id']}/resolve",
        json={
            "decision": "approved",
            "resolved_by": "display name",
        },
    )
    assert response.status_code == 200 and response.json()["authorization_expires_at"] is not None
    response = await test_client.post(f"/api/v1/workflow-runs/{run_id}/start")
    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert response.json()["approvals"][0]["consumed_at"] is not None


async def test_cancel_waiting_run_expires_approval(test_client: httpx.AsyncClient) -> None:
    _, run = await new_run(test_client, protected=True)
    run_id = run["id"]
    waiting = (await test_client.post(f"/api/v1/workflow-runs/{run_id}/start")).json()
    approval_id = waiting["pending_approvals"][0]["id"]
    cancelled = await test_client.post(f"/api/v1/workflow-runs/{run_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["steps"][0]["status"] == "CANCELLED"
    assert (
        await test_client.post(f"/api/v1/workflow-runs/{run_id}/cancel")
    ).json() == cancelled.json()
    expired = await test_client.post(
        f"/api/v1/approvals/{approval_id}/resolve", json={"decision": "approved"}
    )
    assert expired.status_code == 409 and expired.json()["code"] == "APPROVAL_EXPIRED"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/workflow-runs/999",
        "/api/v1/workflow-runs/999/steps",
        "/api/v1/workflow-runs/999/steps/1",
        "/api/v1/workflow-runs/999/audit",
    ],
)
async def test_missing_execution_resources_return_404(
    test_client: httpx.AsyncClient, path: str
) -> None:
    assert (await test_client.get(path)).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/workflow-runs/0",
        "/api/v1/workflow-runs/abc",
        "/api/v1/workflow-runs/1/steps/-1",
        "/api/v1/workflows/0/runs",
    ],
)
async def test_invalid_execution_identifiers_return_422(
    test_client: httpx.AsyncClient, path: str
) -> None:
    assert (await test_client.get(path)).status_code == 422


async def test_step_parent_binding_and_audit_pagination(test_client: httpx.AsyncClient) -> None:
    _, first = await new_run(test_client)
    _, second = await new_run(test_client)
    steps = first["steps"]
    assert isinstance(steps, list)
    step_id = steps[0]["id"]
    assert (
        await test_client.get(f"/api/v1/workflow-runs/{second['id']}/steps/{step_id}")
    ).status_code == 404
    await test_client.post(f"/api/v1/workflow-runs/{first['id']}/start")
    path = f"/api/v1/workflow-runs/{first['id']}/audit"
    all_events = (await test_client.get(path)).json()
    created = next(event for event in all_events if event["event_type"] == "step_created")
    assert json.loads(created["payload_json"])["step_key"] == "step"
    dispatched = next(event for event in all_events if event["event_type"] == "step_dispatched")
    assert json.loads(dispatched["payload_json"])["idempotency_key"] == f"{first['id']}:step:1"
    first_page = (await test_client.get(path, params={"limit": 2})).json()
    rest = (await test_client.get(path, params={"offset": 2})).json()
    assert first_page + rest == all_events
    assert (await test_client.get(path, params={"limit": 0})).status_code == 422
    assert (await test_client.get(path, params={"offset": -1})).status_code == 422


async def test_action_bodies_reject_client_authority(test_client: httpx.AsyncClient) -> None:
    _, run = await new_run(test_client)
    for action in ("start", "cancel"):
        response = await test_client.post(
            f"/api/v1/workflow-runs/{run['id']}/{action}", json={"actor_id": "attacker"}
        )
        assert response.status_code == 422


async def test_execution_results_redact_credentials(
    test_client: httpx.AsyncClient,
    api_app: FastAPI,
    synthetic_secret: str,
) -> None:
    config: Settings = api_app.state.settings
    api_app.state.settings = config.model_copy(
        update={"CHATGPT_API_KEY": SecretStr(synthetic_secret)}
    )
    definition = {
        "template_version": 1,
        "steps": [
            {
                "key": "echo",
                "version": 1,
                "title": "Echo",
                "executor_id": "mock",
                "tool_selection": {"executor_id": "mock"},
                "inputs": {
                    "api_token": "unconfigured confidential input",
                    "text": synthetic_secret,
                },
            }
        ],
    }
    workflow = (
        await test_client.post(
            "/api/v1/workflows",
            json={
                "name": "Redaction",
                "definition_json": json.dumps(definition),
            },
        )
    ).json()
    run = (await test_client.post(f"/api/v1/workflows/{workflow['id']}/runs")).json()
    response = await test_client.post(f"/api/v1/workflow-runs/{run['id']}/start")
    assert response.status_code == 200
    assert synthetic_secret not in response.text
    assert "unconfigured confidential input" not in response.text
    assert response.json()["steps"][0]["output"]["echo"] == {
        "api_token": "[REDACTED]",
        "text": "[REDACTED]",
    }


@pytest.mark.parametrize("action", ["start", "cancel"])
async def test_concurrent_execution_requests_preserve_authorization_and_fencing(
    execution_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    original = MockExecutor.execute

    async def controlled(self: MockExecutor, context: ExecutionContext) -> StepExecutionResult:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(self, context)

    monkeypatch.setattr(MockExecutor, "execute", controlled)
    app = create_app(Settings(DATABASE_URL=str(execution_factory.kw["bind"].url)))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            _, run = await new_run(client, protected=True)
            path = f"/api/v1/workflow-runs/{run['id']}"
            waiting = (await client.post(f"{path}/start")).json()
            approval_id = waiting["pending_approvals"][0]["id"]
            assert (
                await client.post(
                    f"/api/v1/approvals/{approval_id}/resolve", json={"decision": "approved"}
                )
            ).status_code == 200
            active = asyncio.create_task(client.post(f"{path}/start"))
            await asyncio.wait_for(entered.wait(), 2)
            second = await client.post(f"{path}/{action}")
            release.set()
            finished = await asyncio.wait_for(active, 2)
            assert second.status_code == finished.status_code == 200
            final = (await client.get(path)).json()
            assert final["status"] == ("completed" if action == "start" else "cancelled")
            assert final["steps"][0]["attempt"] == 1 and calls == 1
            assert final["approvals"][0]["consumed_at"] is not None


async def test_openapi_execution_contract_matches_export(api_app: FastAPI) -> None:
    exported = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    actual = api_app.openapi()
    assert exported["paths"] == actual["paths"]
    assert exported["components"] == actual["components"]
    assert not any("/merge" in path for path in actual["paths"])
