"""Execution API client requests and domain error reporting."""

import httpx
import pytest

from devflow_ui.api_client import APIClient


async def test_execution_client_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=[] if request.url.path.endswith("/steps") else {"id": 1})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "devflow_ui.api_client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle)),
    )
    client = APIClient("http://test")
    assert await client.start_run(1) == {"id": 1}
    assert await client.cancel_run(1) == {"id": 1}
    assert await client.list_steps(1) == []
    assert await client.get_step(1, 2) == {"id": 1}
    assert calls == [
        ("POST", "/api/v1/workflow-runs/1/start"),
        ("POST", "/api/v1/workflow-runs/1/cancel"),
        ("GET", "/api/v1/workflow-runs/1/steps"),
        ("GET", "/api/v1/workflow-runs/1/steps/2"),
    ]


async def test_execution_client_reports_conflict_and_resets_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            httpx.Response(
                409, json={"error": "Run cannot be started", "code": "RUN_NOT_STARTABLE"}
            ),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "devflow_ui.api_client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(lambda request: next(responses))),
    )
    client = APIClient("http://test")
    assert await client.start_run(1) is None
    assert client.last_error_code == "RUN_NOT_STARTABLE"
    assert await client.cancel_run(1) == {"id": 1}
    assert client.last_error_code is None
