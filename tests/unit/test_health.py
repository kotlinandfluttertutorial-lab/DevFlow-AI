import tomllib
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from structlog.contextvars import get_contextvars

from tests.conftest import ROOT


async def test_health_and_openapi(test_client: httpx.AsyncClient) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    response = await test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": project["version"]}
    schema = (await test_client.get("/openapi.json")).json()
    assert schema["openapi"].startswith("3.")
    assert set(schema["paths"]) == {
        "/health",
        "/api/v1/projects",
        "/api/v1/projects/{id}",
        "/api/v1/learning",
        "/api/v1/learning/{id}",
        "/api/v1/integrations",
        "/api/v1/integrations/{name}/health",
        "/api/v1/workflows",
        "/api/v1/workflows/{id}",
        "/api/v1/workflows/{id}/runs",
        "/api/v1/workflow-runs/{run_id}",
        "/api/v1/workflow-runs/{run_id}/audit",
        "/api/v1/workflow-runs/{run_id}/start",
        "/api/v1/workflow-runs/{run_id}/cancel",
        "/api/v1/workflow-runs/{run_id}/steps",
        "/api/v1/workflow-runs/{run_id}/steps/{step_id}",
        "/api/v1/approvals",
        "/api/v1/approvals/{id}",
        "/api/v1/approvals/{id}/resolve",
        "/api/v1/repositories",
        "/api/v1/repositories/{id}",
        "/api/v1/repositories/{id}/branches",
        "/api/v1/repositories/{id}/branches/{branch}/status",
        "/api/v1/repositories/{id}/branches/{branch}/ci",
        "/api/v1/repositories/{id}/pull-requests",
        "/api/v1/repositories/{id}/pull-requests/{pr_number}",
        "/api/v1/github-confirmations",
    }


@pytest.mark.parametrize(
    "origin,status", [("http://localhost:8501", 200), ("http://other.invalid", 400)]
)
async def test_cors_preflight(test_client: httpx.AsyncClient, origin: str, status: int) -> None:
    response = await test_client.options(
        "/api/v1/projects", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
    )
    assert response.status_code == status
    assert response.headers.get("access-control-allow-origin") == (
        origin if status == 200 else None
    )


async def test_request_context_and_safe_error(
    api_app: FastAPI,
    test_client: httpx.AsyncClient,
    synthetic_secret: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @api_app.get("/probe")
    async def probe(request: Request) -> dict[str, str]:
        request_id = get_contextvars()["request_id"]
        assert request_id == request.state.request_id and isinstance(request_id, str)
        return {"request_id": request_id}

    @api_app.get("/fail")
    async def fail() -> None:
        raise RuntimeError(synthetic_secret)

    @api_app.get("/denied")
    async def denied() -> None:
        raise HTTPException(401, synthetic_secret, headers={"WWW-Authenticate": "Bearer"})

    ids = [UUID((await test_client.get("/probe")).json()["request_id"]) for _ in range(2)]
    assert len(set(ids)) == 2 and all(value.version == 4 for value in ids)
    response = await test_client.get("/fail", params={"private": synthetic_secret})
    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error", "code": "INTERNAL_ERROR"}
    assert response.headers["content-type"] == "application/json"
    denied_response = await test_client.get("/denied")
    assert denied_response.status_code == 401
    assert denied_response.headers["www-authenticate"] == "Bearer"
    assert synthetic_secret not in denied_response.text
    assert "request_id" not in get_contextvars()
    output = capsys.readouterr().out
    assert "traceback" in output and synthetic_secret not in output
