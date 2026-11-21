"""Workflow execution and approval status through the local API."""

import asyncio

import streamlit as st

from devflow_core.config import get_settings
from devflow_ui.api_client import APIClient

st.header("Workflows")
st.caption("Mock execution · MockExecutor")
client = APIClient(get_settings().DEVFLOW_API_BASE_URL)
with st.spinner("Loading workflows…"):
    workflows = asyncio.run(client.list_workflows())
if workflows is None:
    st.warning("Backend unavailable. Workflows could not be loaded.")
    st.stop()
if not workflows:
    st.info("No workflows yet.")
    st.stop()

for workflow in workflows:
    st.subheader(str(workflow["name"]))
    st.write(f"Workflow status {workflow['status']}")
    with st.spinner("Loading runs…"):
        runs = asyncio.run(client.list_runs(int(str(workflow["id"]))))
    if runs is None:
        st.warning("Backend unavailable. Runs could not be loaded.")
        continue
    if not runs:
        st.info("No runs yet.")
    pending_count = sum(
        len(approvals)
        for run in runs
        if isinstance(approvals := run.get("pending_approvals"), list)
    )
    st.write(f"Pending approvals {pending_count}")
    for run in reversed(runs):
        run_id = int(str(run["id"]))
        st.write(f"Run {run_id} · {run['status']}")
        steps = run.get("steps")
        if isinstance(steps, list):
            terminal = run["status"] in {"completed", "failed", "cancelled"}
            active = any(
                item.get("status") == "RUNNING" for item in steps if isinstance(item, dict)
            )
            with st.container(horizontal=True):
                start = st.button(
                    "Start/Resume", key=f"start_run_{run_id}", disabled=terminal or active
                )
                cancel = st.button("Cancel", key=f"cancel_run_{run_id}", disabled=terminal)
                st.button("Refresh", key=f"refresh_run_{run_id}")
            if start or cancel:
                with st.spinner("Starting workflow…" if start else "Cancelling workflow…"):
                    updated = asyncio.run(
                        client.start_run(run_id) if start else client.cancel_run(run_id)
                    )
                if updated is None:
                    message = "Could not start the run." if start else "Could not cancel the run."
                    if client.last_error_code:
                        message += f" ({client.last_error_code})"
                    st.error(message)
                else:
                    st.rerun()
            if steps:
                st.dataframe(
                    [
                        {
                            "Step": str(item.get("step_key", "")),
                            "Status": str(item.get("status", "")),
                            "Attempt": item.get("attempt", 0),
                            "Executor": str(item.get("executor_id", "")),
                            "Version": item.get("step_version", 1),
                        }
                        for item in steps
                        if isinstance(item, dict)
                    ],
                    hide_index=True,
                    alt=f"Step progress for run {run_id}",
                )
                for item in steps:
                    if not isinstance(item, dict):
                        continue
                    error = item.get("error")
                    if isinstance(error, dict):
                        st.error(
                            f"{item.get('step_key')} · {error.get('code')} · {error.get('message')}"
                        )
                if st.toggle("Show step results", key=f"results_run_{run_id}"):
                    for item in steps:
                        if isinstance(item, dict) and item.get("status") == "SUCCEEDED":
                            st.write(str(item.get("step_key")))
                            st.json(
                                {
                                    "output": item.get("output"),
                                    "artifacts": item.get("artifact_refs", []),
                                }
                            )
            else:
                st.caption("This run has no execution steps.")
            if run.get("error_message"):
                st.error(str(run["error_message"]))
            if st.toggle("Show audit history", key=f"audit_run_{run_id}"):
                with st.spinner("Loading audit history…"):
                    events = asyncio.run(client.list_run_audit(run_id))
                if events is None:
                    st.warning("Backend unavailable. Audit history could not be loaded.")
                elif not events:
                    st.info("No audit events yet.")
                else:
                    st.dataframe(events, hide_index=True, alt=f"Audit history for run {run_id}")
        approvals = run.get("pending_approvals")
        if not isinstance(approvals, list) or run["status"] in {"completed", "failed", "cancelled"}:
            continue
        for approval in approvals:
            if approval["status"] != "pending":
                continue
            st.write(str(approval["action_description"]))
            if approval.get("expires_at"):
                st.caption(f"Approval expires {approval['expires_at']}")
            approval_id = int(str(approval["id"]))
            approve, reject = st.columns(2)
            decision = None
            if approve.button("Approve", key=f"approve_{approval_id}"):
                decision = "approved"
            if reject.button("Reject", key=f"reject_{approval_id}"):
                decision = "rejected"
            if decision is not None:
                with st.spinner("Resolving approval…"):
                    resolved = asyncio.run(client.resolve_approval(approval_id, decision))
                if resolved is None:
                    message = "Could not resolve the approval."
                    if client.last_error_code:
                        message += f" ({client.last_error_code})"
                    st.error(message)
                else:
                    st.rerun()
