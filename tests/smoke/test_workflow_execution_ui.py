"""AppTest coverage for the Wave 1 execution page."""

from unittest.mock import AsyncMock

import pytest
from streamlit.testing.v1 import AppTest

from devflow_ui.api_client import APIClient
from tests.conftest import ROOT

PAGE = ROOT / "src/devflow_ui/pages/6_Workflows.py"


def run_data(status: str = "pending", step_status: str = "PENDING") -> dict[str, object]:
    return {
        "id": 2,
        "status": status,
        "pending_approvals": [],
        "steps": [
            {
                "id": 3,
                "step_key": "mock_step",
                "step_version": 1,
                "executor_id": "mock",
                "status": step_status,
                "attempt": 0,
                "output": {"echo": {}, "marker": "mock-v1"},
                "artifact_refs": [],
                "error": None,
            }
        ],
    }


def prepare(monkeypatch: pytest.MonkeyPatch, run: dict[str, object]) -> AsyncMock:
    monkeypatch.setattr(
        APIClient,
        "list_workflows",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "Workflow", "status": "active"},
            ]
        ),
    )
    runs = AsyncMock(return_value=[run])
    monkeypatch.setattr(APIClient, "list_runs", runs)
    return runs


def test_execution_ui_renders_steps_and_mock_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare(monkeypatch, run_data())
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert any("Mock execution" in caption.value for caption in at.caption)
    assert at.dataframe[0].value["Step"].tolist() == ["mock_step"]
    assert not at.button(key="start_run_2").disabled
    assert not at.button(key="cancel_run_2").disabled


@pytest.mark.parametrize("action", ["start", "cancel"])
def test_execution_ui_actions_call_api_and_refresh(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    initial = run_data()
    terminal = run_data(
        "completed" if action == "start" else "cancelled",
        "SUCCEEDED" if action == "start" else "CANCELLED",
    )
    runs = prepare(monkeypatch, initial)
    operation = AsyncMock(return_value=terminal)
    monkeypatch.setattr(APIClient, f"{action}_run", operation)
    at = AppTest.from_file(PAGE).run()
    runs.side_effect = [[initial], [terminal]]
    at.button(key=f"{action}_run_2").click().run()
    assert not at.exception
    operation.assert_awaited_once_with(2)
    assert at.button(key="start_run_2").disabled and at.button(key="cancel_run_2").disabled


def test_running_ui_disables_duplicate_start(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare(monkeypatch, run_data("running", "RUNNING"))
    at = AppTest.from_file(PAGE).run()
    assert not at.exception and at.button(key="start_run_2").disabled
    assert not at.button(key="cancel_run_2").disabled


def test_execution_ui_action_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare(monkeypatch, run_data())
    operation = AsyncMock(return_value=None)
    monkeypatch.setattr(APIClient, "start_run", operation)
    at = AppTest.from_file(PAGE).run()
    at.button(key="start_run_2").click().run()
    assert not at.exception
    assert at.error[0].value == "Could not start the run."


@pytest.mark.parametrize("events", [None, [], [{"id": 1, "event_type": "step_ready"}]])
def test_execution_ui_audit_states(monkeypatch: pytest.MonkeyPatch, events: object) -> None:
    prepare(monkeypatch, run_data())
    audit = AsyncMock(return_value=events)
    monkeypatch.setattr(APIClient, "list_run_audit", audit)
    at = AppTest.from_file(PAGE).run()
    audit.assert_not_awaited()
    at.toggle(key="audit_run_2").set_value(True).run()
    assert not at.exception
    audit.assert_awaited_once_with(2)
    if events is None:
        assert "Backend unavailable" in at.warning[0].value
    elif not events:
        assert at.info[0].value == "No audit events yet."
    else:
        assert len(at.dataframe) == 2


def test_execution_ui_results_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare(monkeypatch, run_data("completed", "SUCCEEDED"))
    at = AppTest.from_file(PAGE).run()
    at.toggle(key="results_run_2").set_value(True).run()
    assert not at.exception and at.json
    failed = run_data("failed", "FAILED")
    failed["steps"] = [
        {
            "step_key": "mock_step",
            "status": "FAILED",
            "executor_id": "mock",
            "attempt": 1,
            "error": {"code": "MOCK_FAILURE", "message": "Mock step failed"},
        }
    ]
    prepare(monkeypatch, failed)
    at.run()
    assert not at.exception
    assert "MOCK_FAILURE" in at.error[0].value


def test_execution_ui_preserves_approval_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_data("waiting_approval", "WAITING_APPROVAL")
    run["pending_approvals"] = [
        {
            "id": 9,
            "status": "pending",
            "action_description": "Approve mock step",
            "expires_at": "2026-11-19T12:00:00Z",
        }
    ]
    prepare(monkeypatch, run)
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(APIClient, "resolve_approval", resolve)
    at = AppTest.from_file(PAGE).run()
    at.button(key="approve_9").click().run()
    assert not at.exception
    resolve.assert_awaited_once_with(9, "approved")
    assert at.error[0].value == "Could not resolve the approval."
