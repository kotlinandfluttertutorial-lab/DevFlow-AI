# Phase 2 Wave 1 final verification report

Date: 2026-11-19. Specification: v2.1.4. Branch: `feature/phase2-wave1-workflow-engine-implementation`. Baseline HEAD: `2386a784d39c1bac3526923342985d70d4ce9a36`; Phase 1 baseline: `2d077ecb9f96ff767160e6c1564b2c6f8d4e0a9e`.

## Verdict and scope

Follow-up verification, 2026-11-21: confirmed the same implementation branch and baseline HEAD, retained all existing work, and honored the user's offline-only verification decision. Ruff, formatting (175 files), strict MyPy (165 files), sole migration head 008, and all six focused acceptance tests passed again (2.35s). `git diff --check` passed. No online secret verification, publication, or additional implementation change occurred. The 574-test full-suite and coverage figures below remain evidence from 2026-11-19; the full suite was not rerun for this documentation-only follow-up.

**PHASE 2 WAVE 1 IMPLEMENTATION BLOCKED**

Implementation and acceptance tests pass, but the exact CI online secret-verification command remains unexecuted. Automatic approval review initially rejected possible credential egress; the user subsequently explicitly chose **Keep verification offline**. That constraint is respected, and no online verification will run under this authorization. A pinned-tool offline scan passed with no candidates. PR readiness is withheld rather than treating the missing online workflow result as a pass. Remaining review action: accept the documented offline-only security evidence as a release-gate exception, or retain the blocked verdict. No push, PR creation, merge, deployment, real executor integration, or Wave 2 work occurred.

Preflight confirmed Python 3.12.10 in the project `.venv`, required v2.1.4 specification files, and sole Alembic head `008_add_workflow_execution_engine`. Complete requirements, design, tasks, final-resolution report, Group A–C reports, actual implementations, CI/security workflows, and relevant test assertions were inspected. Existing staged/unstaged/untracked work was preserved. Phase 0/1 specification files and migrations 001–007 were not modified. The original blocker report and Group A report retain their prior SHA256 values (respectively `EBABB2573E3B27F658ECCEB605CA0322A0B260119DC8177BAF6B486B14115724` and `211E878A2B3898B45738DE516FDBFAE93CD8542E4C6C7B620DE6308EC9E1390B`). Group A–C reports were not edited.

## T1–T11 status

| Task | Verified status |
|---|---|
| T1 templates | Implemented; schema, DAG, metadata, and JSON validation tests pass. |
| T2 persistence/migration | Implemented; isolated migrations, legacy records, ORM comparison, constraints, and foreign keys verified. |
| T3 repositories | Implemented; 64 state pairs, leases, attempts, atomic consume, fencing, expiry uniqueness, CA/CB behavior tested. |
| T4 executor | Implemented; MockExecutor only, deterministic and guarded against side effects. |
| T5 orchestration | Implemented; dependencies, input resolution, immutable snapshots, retry/failure-stop/cancellation tested. |
| T6 approvals | Implemented; binding, validity, pre-execution consumption, replay, replacement, and concurrency tested. |
| T7 recovery | Implemented; automatic reconciliation, explicit resume, persisted restart, and concurrency verified. |
| T8 API | Implemented; request/error/projection contracts, redaction, concurrency, and OpenAPI verified. |
| T9 UI | Implemented; AppTest and live Streamlit agent API checks verified. |
| T10 acceptance | Complete semantic acceptance verification and six added assertions; 574 tests pass. Exact online security workflow remains blocked separately. |
| T11 readiness | Reporting complete; PR recommendation blocked pending online security verification. |

## Findings and changes in Group D

**Resolved security finding:** aiosqlite DEBUG diagnostics include raw SQL parameter values outside the structured-log redaction pipeline. A newly added real gated-execution test failed by detecting a synthetic credential in captured driver logs. `configure_logging()` now sets the aiosqlite logger to WARNING before its one-time configuration guard. The same test passes at root DEBUG level, and stored audit payloads remain redacted. Application DEBUG logs remain available; raw driver operation diagnostics are suppressed. No raw secret was copied into this report.

New `tests/unit/test_workflow_acceptance.py` adds six acceptance checks: credential absence from audit/logs, explicit empty executor environment/no parent inheritance, append-only audit update/delete rejection, no single-step-delete repository interface, UTC timestamp decorators, and populated run-level failure message/timestamp. Existing assertions were not weakened. Only `src/devflow_core/logging.py` changed in production code during Group D.

Other Group D deliverables: this report and updated `docs/reports/phase2-wave1-spec-readiness.md`. Earlier Groups A–C implementation files remain in the working tree.

## Quality gates and CI-equivalent checks

| Check | Observed result |
|---|---|
| Ruff check | Passed. |
| Ruff format check | Passed; 175 project files (temporary copied scan snapshot excluded by cleanup, not an ignore-rule change). |
| Strict MyPy `src/ tests/` | Passed, 165 files. |
| First Group D full run | 573 passed in 294.36s; 93.15% combined branch-aware coverage. |
| Final full run (includes sixth acceptance assertion) | **574 passed in 273.35s (4m 33s)**; **93.11%** combined coverage; **94.96%** line coverage and **84.52%** branch coverage. No failures or skips. |
| CI integration marker selection | 25 passed, 548 deselected in 9.10s; `pytest -m integration -v --no-cov`. |
| Alembic heads | Sole `008_add_workflow_execution_engine (head)`. |
| OpenAPI exporter | Executed successfully; API test compares actual paths/components with exported JSON and asserts merge absence. |
| `git diff --check` | Passed; only normal existing LF/CRLF conversion notices. |
| `pip check` | Passed, no broken requirements. |
| CI SAST | Bandit 1.9.4: `-c pyproject.toml -r src/ -ll -f json`; passed, no findings at the configured threshold. |
| CI dependency scan | pip-audit 2.10.1: `-r requirements/base.txt --format json`; passed, no known vulnerabilities in 61 resolved dependencies. |
| Secret detection | TruffleHog 3.90.0 native Windows release, SHA256 checked against release checksums; offline `--no-update filesystem <snapshot> --no-verification --fail --json`: exit 0, 652 chunks / 5,461,553 bytes, zero verified/unverified secrets. |
| Exact CI online secret verification | Not executed: automatic approval review rejected candidate-secret egress; user explicitly selected **Keep verification offline**. No further online verification is authorized. |

The initial sandboxed pip-audit invocation could not bootstrap pip; the authorized network-enabled rerun passed. Docker was unavailable, so the same pinned TruffleHog version was downloaded as a verified native Windows binary. Sandboxed offline TruffleHog terminated before completing; the safer offline-only elevated rerun produced explicit `finished scanning` evidence and exit 0. The online rejection was not bypassed. Offline zero candidates does not establish validity of any actual credential or replace the exact online CI result.

Final coverage XML records 3729/3927 covered lines and 715/846 covered branches; the separate CI branch threshold of 80% passes. Full command: `.venv/Scripts/python.exe -m pytest --cov=src --cov-branch --cov-fail-under=80 --cov-report=term-missing --cov-report=xml:.group-d-coverage.xml --basetemp=J:/Android/GenAI/DevFlow-AI/.pytest-group-d-final -o cache_dir=J:/Android/GenAI/DevFlow-AI/.pytest-cache-group-d`. Local checks ran on Windows/Python 3.12.10. Hosted Ubuntu CI was not run, since creating/pushing a PR is prohibited; platform parity is not claimed.

## Database and compatibility evidence

`test_migration_008_upgrade_downgrade_and_legacy_roundtrip` upgrades isolated SQLite from base through 007, seeds Phase 1 workflow/run/approval records and an AUTOINCREMENT high-water mark, then exercises repeated 008→007→008 cycles. It checks the partial unique predicate, NULL legacy snapshot/binding columns, unchanged status CHECKs, preserved records/high-water mark, and `PRAGMA foreign_key_check`. Alembic `command.check()` compares migrated schema and ORM metadata. The populated Phase 2 downgrade test checks supported downgrade cleanup and upgrade afterward. All passed; no operational database was migrated or downgraded.

File-backed independent-session tests verify partial approval uniqueness excludes Phase 1, database foreign keys, run-deletion cascades, narrow SQLite unique-conflict classification via supported exception attributes, explicit rollback before re-read, propagation of unrelated integrity failures, bounded SQLITE_BUSY handling, owner/attempt fencing, and legal atomic two-transition failure-stop normalization. Independent readers cannot observe its intermediate running state. Persisted structured error categories determine failure-stop; substring matching is not used.

Phase 0/1 regressions are included in the complete suite, including workflow CRUD/legacy zero-step creation, approval resolution without Phase 2 binding, model constraints, existing adapters/confirmation services, and all UI pages. The earlier Group C full suite had 568 tests; Group D adds six cases. Existing tests are preserved, including the previously documented additive four-route health assertion and migration fixture updates from earlier groups.

## Security verification evidence

- Registry defaults to exactly `['mock']` and rejects other executor IDs. AST/import tests prohibit shell/subprocess/network/filesystem dependencies and state-layer imports in executors; runtime traps on open/socket/Popen pass. These are guards on shipped mock code, not an OS sandbox or an arbitrary-code isolation guarantee.
- Protected executor invocation follows a committed lease-and-consume transaction. Tests acquire an independent write transaction during execution, inspect consumed approval before invocation, inject failed consume, and assert no dispatch/attempt increment on refusal.
- Binding tests cover operation, digest, step, actor, pending/expired state, authorization expiry, and replay. Protected retries require fresh authorization; cancellation/failure/timeout/indeterminate never restore consumed_at.
- CAS tests discard old-owner/old-attempt and post-cancel completions without reconsumption or output overwrite. Concurrent dispatch/start/approval/re-gate/resolve/cancel tests use independent file-backed sessions.
- API bodies cannot inject server actor or execution inputs; the configured local operator remains the trust boundary. No multi-user authentication or production-grade isolation is claimed.
- Public responses omit raw inputs/snapshots; nested sensitive fields and configured credential values are redacted, including output/error/audit projections. Public step/idempotency identifiers remain readable. Audit update/delete methods refuse mutation. Audit is append-only by application convention, not cryptographically signed or safe from local database administrators.
- Startup recovery never invokes an executor; test spies and actual new-process audit evidence confirm explicit resume is necessary.
- Workflow orchestration imports no Git/publishing adapter. Existing Phase 1 adapter routes remain available for compatibility, but the Wave 1 engine cannot automatically push, create a PR, merge, deploy, or invoke real agents. OpenAPI has no merge route.

## End-to-end observed results

An isolated database under the verification directory was migrated from base to 008. Temporary FastAPI and Streamlit processes bound only to `127.0.0.1:18866` and `127.0.0.1:18865`; configured external credentials were blank. HTTP created the definition/run, and Streamlit agent API drove the successful execution and approval buttons. Agent API schema was fetched once; returned status/tree/actions and service logs were inspected.

| Scenario | Observed result |
|---|---|
| Protected happy path | Created pending run → UI Start/Resume → waiting approval, attempt 0 → UI Approve → READY → UI Start/Resume → completed/SUCCEEDED, attempt 1. Authorization consumed. |
| Results/artifacts/audit | UI toggles displayed mock echo, artifact digest, and 15 audit events with readable step/idempotency identifiers. Terminal start/cancel controls disabled. |
| Failure | Mock indeterminate mode reached failed, attempt 1, structured error, no retry. UI rendered failed status. |
| Rejection | Approval resolve rejected; run/step cancelled and no executor dispatch. UI rendered cancelled state. |
| Cancellation | Cancelled unstarted run via API; attempt remained 0 and step cancelled. UI rendered terminal state. |
| Expired approval | Isolated fixture set past pending expiry; resolve returned 409 `APPROVAL_EXPIRED`; exactly one fresh pending request replaced it and step remained gated. UI showed pending action. |
| Persisted recovery/restart | Seeded a valid persisted expired-lease RUNNING attempt 1, stopped API process tree, launched a fresh API process on same DB. Observed READY attempt 1 and step_interrupted/recovery audit, with no step_dispatched event. Explicit resume completed attempt 2. UI refreshed to completed. |
| Digest-negative fixture | Initial hand-seeded stale row omitted its input digest. Startup reconciled it, then explicit dispatch correctly refused it as INPUT_DIGEST_MISMATCH, attempt unchanged. Corrected fixture included canonical digest and passed the restart scenario. No implementation or assertion was relaxed. |

The UI returned ready snapshots without application exceptions; logs contained no new traceback or deprecation warning. Temporary API/UI process trees were checked against the project Python executable and stopped. Only verification-created databases, downloaded tools, snapshots, scripts, logs, caches, and pytest temporary directories were eligible for cleanup.

## Operational migration instructions

1. Stop the local API/UI and any writer. Back up the operational SQLite database and applicable sidecars using the project's normal backup procedure.
2. Activate the project environment; set `DATABASE_URL` explicitly to the intended database and keep local services bound to 127.0.0.1. Confirm `python -m alembic heads` shows only 008.
3. Run `.venv/Scripts/python.exe -m alembic upgrade head`; verify `.venv/Scripts/python.exe -m alembic current` reports `008_add_workflow_execution_engine`.
4. Start the API. Lifespan requires the migrated execution table and reconciles stale leases/approval state without dispatching. Inspect run/approval/audit state and explicitly start/resume when appropriate. A spent protected authorization requires fresh approval.
5. Start the UI. Observe the mock-execution label and verify API health before beginning runs.

Downgrade was tested only on isolated databases. Dropping 008 removes Phase 2 execution/snapshot/binding data; it is not a routine operational rollback plan. Restore from a verified backup if a real rollback is needed.

## Remaining risks, staging, and recommendation

The remaining verification limitation is the exact online TruffleHog security command. The user explicitly requires offline-only verification; this does not itself waive the original exact-workflow acceptance gate. The offline result is recorded for release review, and no additional authorization request or online verification is planned. No unresolved critical/high implementation finding was identified after the driver-logging fix. Branch-aware coverage does not prove every interleaving; the local-operator and mock-only scope remains essential. Synchronous mock execution can hold an HTTP request open; the minimal UI shows only the default 100-event audit page. No OS process/filesystem/network isolation, credential vault, multi-user authorization, real-agent execution, or cryptographic audit integrity is delivered.

Required reports are covered by the existing `/docs/` ignore rule and are not automatically included by `git add .`. Before an eventual separately authorized implementation commit, explicitly stage the reports with:

```powershell
git add -f -- docs/reports/phase2-wave1-group-a-implementation-report.md docs/reports/phase2-wave1-group-b-implementation-report.md docs/reports/phase2-wave1-group-c-implementation-report.md docs/reports/phase2-wave1-final-verification-report.md docs/reports/phase2-wave1-spec-readiness.md
git add -- docs/openapi.json src/devflow_core/logging.py tests/unit/test_workflow_acceptance.py
git diff --cached --stat
git diff --cached --check
```

Review and stage the remaining Group A–C implementation/tests deliberately as well; pre-existing staged entries and later unstaged edits can differ. These staging commands are instructions only and were not executed. No broad ignore-rule change or unrelated report overwrite was made. The original implementation-blocker report is preserved as historical evidence.

The specification's named-test list is implemented by semantic tests and parametrized cases, rather than duplicating each prose test name verbatim. The matrix below identifies the actual tests and direct inspections; documentation-only isolation requirements and future subprocess contracts are marked as such. Phase 1 historical report claims do not supersede this verification report.

## Requirements-to-tests traceability

Every REQ below and every numbered acceptance ID in its section is included. Test names omit the `test_` prefix. Evidence is semantic: parametrized cases consolidate multiple specification names; no passing alias tests were added merely to match names.

File keys: TEMPLATE=`tests/unit/test_workflow_template.py`; REPO=`tests/unit/test_workflow_execution_repositories.py`; ORCH=`tests/unit/test_workflow_orchestration.py`; EXEC=`tests/unit/test_workflow_executors.py`; RECOVERY=`tests/unit/test_workflow_recovery.py`; ACCEPT=`tests/unit/test_workflow_acceptance.py`; API=`tests/integration/test_workflow_execution_api.py`; UI=`tests/smoke/test_workflow_execution_ui.py`; MIGRATION=`tests/integration/test_workflow_execution_migration.py`.

| Requirement and acceptance IDs | Tests / direct verification |
|---|---|
| REQ-E1.1: E1.1.1, E1.1.2, E1.1.3, E1.1.4, E1.1.5 | TEMPLATE: valid_workflow_template_roundtrip, invalid_template_graph, invalid_step_metadata; API: invalid_template_is_validated_at_run_creation; model inspection confirms deferred definition_json unchanged. |
| REQ-E1.2: E1.2.1, E1.2.2, E1.2.3, E1.2.4 | REPO: create_snapshot_and_steps_roll_back_atomically; ORCH: run_creation_audit_failure_rolls_back_all_rows; API: create_start_detail_and_terminal_contract. |
| REQ-E1.3: E1.3.1, E1.3.2, E1.3.3 | ORCH: linear_chain_dispatches_in_order_and_resolves_inputs, diamond_dependency_gates_until_all_succeeded, missing_reference_fails_pending_step; REPO: failure_stop_settlement_and_inflight_completion. |
| REQ-E1.4: E1.4.1, E1.4.2, E1.4.3 | ORCH: linear_chain_dispatches_in_order_and_resolves_inputs, missing_reference_fails_pending_step, malformed_execution_results_fail_safely; API: create_start_detail_and_terminal_contract. |
| REQ-E1.5: E1.5.1, E1.5.2, E1.5.3 | ORCH: snapshot_survives_live_workflow_edit; REPO: template_snapshot_immutable_after_live_workflow_edit; API: create_start_detail_and_terminal_contract. |
| REQ-E1.6: E1.6.1, E1.6.2, E1.6.3 | ACCEPT: execution_timestamps_use_utc_decorator; RECOVERY: recovery_interrupts_expired_lease_without_dispatch; live process restart preserves attempt and persisted state; static inspection uses datetime.now(UTC). |
| REQ-E1.7: E1.7.1, E1.7.2, E1.7.3 | REPO: step_transition_contract (all 64 state pairs), concurrent_dispatch_only_one_succeeds, cas_uses_begin_immediate, consume_and_lease_acquired_in_one_transaction. |
| REQ-E1.8: E1.8.1, E1.8.2, E1.8.3, E1.8.4 | REPO: template_snapshot_immutable_after_live_workflow_edit, legacy_snapshot_and_vacuous_completion; ORCH: snapshot_survives_live_workflow_edit, legacy_zero_step_run_vacuously_completes; MIGRATION. |
| REQ-E2.1: E2.1.1, E2.1.2, E2.1.3, E2.1.4, E2.1.5 | ORCH: invalid_template_creation_rolls_back, run_creation_audit_failure_rolls_back_all_rows, legacy_zero_step_run_vacuously_completes; API: invalid_template_is_validated_at_run_creation; Phase 1 workflow router tests. |
| REQ-E2.2: E2.2.1, E2.2.2, E2.2.3, E2.2.4 | ORCH: concurrent_start_single_active_executor_per_run, pre_dispatch_validation_fails_without_execution, protected_executor_not_invoked_until_consumed_and_committed, linear_chain_dispatches_in_order_and_resolves_inputs. |
| REQ-E2.3: E2.3.1, E2.3.2, E2.3.3 | ORCH: linear_chain_dispatches_in_order_and_resolves_inputs, malformed_execution_results_fail_safely; API: create_start_detail_and_terminal_contract; live results/artifacts. |
| REQ-E2.4: E2.4.1, E2.4.2, E2.4.3, E2.4.4, E2.4.5, E2.4.6 | ORCH: multiple_gates_derive_and_protected_execution, failed_consume_leaves_step_unexecuted_and_regates, authorization_expiry_and_replay_regate, expiry_before_and_after_sweep_returns_same_error_and_regates. |
| REQ-E2.5: E2.5.1, E2.5.2, E2.5.3, E2.5.4, E2.5.5 | ORCH: multiple_gates_derive_and_protected_execution, rejection_cancels_all_steps_and_expires_other_gates, existing_resolve_endpoint_expiry_semantics; API: approval_http_flow_uses_server_operator. |
| REQ-E2.6: E2.6.1, E2.6.2, E2.6.3 | ORCH: failed_workflow_settles_waiting_approval, failure_stop_suppresses_inflight_retry_and_settles_gates; ACCEPT: terminal_failure_populates_run_error; REPO: failure_stop_settlement_and_inflight_completion. |
| REQ-E2.7: E2.7.1, E2.7.2, E2.7.3, E2.7.4 | ORCH: cancel_inflight_keeps_approval_spent_and_fences_result, cancel_pending_run_is_idempotent, late_success_after_cancellation_is_discarded; API: cancel_waiting_run_expires_approval. |
| REQ-E2.8: E2.8.1, E2.8.2, E2.8.3 | EXEC: executors_do_not_import_repositories_services_models, schemas_do_not_import_executors_package; strict MyPy; inspection of one-way Executor protocol. |
| REQ-E2.9: E2.9.1, E2.9.2, E2.9.3, E2.9.4 | ORCH: multiple_gates_derive_and_protected_execution, approve_one_gate_can_execute_while_other_waits; REPO: derive_concurrent_calls_and_multi_gate_precedence; legacy workflow service tests. |
| REQ-E2.10: E2.10.1, E2.10.2, E2.10.3, E2.10.4, E2.10.5, E2.10.6, E2.10.7, E2.10.8, E2.10.9, E2.10.10 | REPO: derive_concurrent_calls_and_multi_gate_precedence, derive_run_status_idempotent_on_terminal_run, derive_illegal_transition_raises_not_silent_noop, derive_uses_structured_failure_category, failure_stop_all_terminal_while_run_waiting_approval_executes_two_step_transition (also observes uncommitted state from independent reader), failure_stop_two_step_rollback_on_second_transition_error, concurrent_approval_resolution_cannot_bypass_failure_stop. |
| REQ-E3.1: E3.1.1, E3.1.2, E3.1.3, E3.1.4 | MIGRATION; REPO: consume_validation_rejects_every_binding, partial_unique_index_excludes_phase1; ORCH: multiple_gates_derive_and_protected_execution; existing Phase 1 approval tests. |
| REQ-E3.2: E3.2.1, E3.2.2, E3.2.3 | TEMPLATE: canonical_digest_preserves_content_and_types; REPO: consume_validation_rejects_every_binding; ORCH: mismatched_approval_regates_without_execution; single shared canonical_hash implementation. |
| REQ-E3.3: E3.3.1, E3.3.2, E3.3.3, E3.3.4, E3.3.5, E3.3.6, E3.3.7, E3.3.8, E3.3.9, E3.3.10 | REPO: consume_validation_rejects_every_binding (replay/digest/step/operation/actor/status/expiry), concurrent_consume_only_one_dispatch_proceeds, consume_and_lease_acquired_in_one_transaction; ORCH: failed_consume_leaves_step_unexecuted_and_regates. |
| REQ-E3.4: E3.4.1, E3.4.2, E3.4.3 | ORCH: expiry_before_and_after_sweep_returns_same_error_and_regates, existing_resolve_endpoint_expiry_semantics; REPO: authorization_validity_is_anchored_to_resolution; API: approval_http_flow_uses_server_operator. |
| REQ-E3.5: E3.5.1, E3.5.2, E3.5.3 | REPO: run_delete_cascades_steps_and_approvals; ACCEPT: step_repository_has_no_single_step_delete; MIGRATION foreign_key_check. |
| REQ-E3.6: E3.6.1, E3.6.2, E3.6.3, E3.6.4 | REPO: authorization_validity_is_anchored_to_resolution, consume_validation_rejects_every_binding; ORCH: authorization_expiry_and_replay_regate; legacy approval suite. |
| REQ-E3.7: E3.7.1, E3.7.2, E3.7.3, E3.7.4, E3.7.5, E3.7.6 | ORCH: protected_executor_not_invoked_until_consumed_and_committed, failed_consume_leaves_step_unexecuted_and_regates, protected_retry_requires_fresh_authorization, timeout_uses_failure_or_transient_retry_and_preserves_consumption, late_success_after_cancellation_is_discarded; REPO: consumed_approval_never_restored. |
| REQ-E3.8: E3.8.1, E3.8.2, E3.8.3, E3.8.4, E3.8.5, E3.8.6, E3.8.7, E3.8.8, E3.8.9, E3.8.10, E3.8.11, E3.8.12 | REPO: expire_and_re_gate_stale_pending_approval_not_reused, expire_and_re_gate_concurrent_calls_produce_exactly_one_pending_approval, expire_and_re_gate_refused_when_run_stopped, expire_and_re_gate_non_unique_integrity_error_propagates_unchanged, expire_and_re_gate_session_rolled_back_after_unique_constraint_violation, expire_and_re_gate_sqlite_busy_retried_bounded, re_gate_real_sqlite_lock_exhaustion_leaves_no_approval, partial_unique_index_excludes_phase1. |
| REQ-E4.1: E4.1.1, E4.1.2, E4.1.3, E4.1.4 | REPO: step_transition_contract; MIGRATION: populated downgrade rejects ninth state; legacy run and approval status CHECK tests. |
| REQ-E4.2: E4.2.1, E4.2.2, E4.2.3, E4.2.4 | REPO: step_transition_contract (8×8 source/target pairs; rejection audit and no mutation), stale_completion_discarded_by_lease_fence; ORCH: missing_reference_fails_pending_step. |
| REQ-E4.3: E4.3.1, E4.3.2, E4.3.3, E4.3.4 | RECOVERY: recovery_interrupts_expired_lease_without_dispatch, valid_lease_survives_repeated_recovery, concurrent_recovery_reconciles_once, startup_recovery_uses_configured_database; live new-process startup without dispatch. |
| REQ-E4.4: E4.4.1, E4.4.2, E4.4.3 | REPO: failure_stop_settlement_and_inflight_completion, pending_downstream_after_success_keeps_run_running, derive_concurrent_calls_and_multi_gate_precedence; ORCH: legacy_zero_step_run_vacuously_completes. |
| REQ-E4.5: E4.5.1, E4.5.2, E4.5.3, E4.5.4 | ORCH: malformed_execution_results_fail_safely, timeout_uses_failure_or_transient_retry_and_preserves_consumption, unsafe_or_indeterminate_not_retried; REPO: failure_stop_settlement_and_inflight_completion. |
| REQ-E4.6: E4.6.1, E4.6.2, E4.6.3, E4.6.4 | REPO: concurrent_dispatch_only_one_succeeds, concurrent_consume_only_one_dispatch_proceeds, cas_uses_begin_immediate; ORCH: concurrent_approved_starts_consume_once; RECOVERY safe restart; live attempt 1→2. |
| REQ-E4.7: E4.7.1, E4.7.2, E4.7.3, E4.7.4, E4.7.5, E4.7.6 | ORCH: retry_budget_and_transient_state, unsafe_or_indeterminate_not_retried, protected_retry_requires_fresh_authorization, failure_stop_suppresses_inflight_retry_and_settles_gates; EXEC: mock_executor_deterministic_output. |
| REQ-E4.8: E4.8.1, E4.8.2, E4.8.3, E4.8.4, E4.8.5 | RECOVERY: recovery_retry_policy_and_attempt_budget, recovery_failure_stop_settles_interrupted_and_gates, recovery_interrupts_expired_lease_without_dispatch. |
| REQ-E4.9: E4.9.1, E4.9.2, E4.9.3 | EXEC: mock_executor_deterministic_output; REPO: consume_and_lease_acquired_in_one_transaction, dispatch_cannot_bypass_snapshot_approval_or_attempt_budget; ORCH: mismatched_approval_regates_without_execution; live malformed fixture refused with INPUT_DIGEST_MISMATCH. |
| REQ-E4.10: E4.10.1, E4.10.2, E4.10.3 | REPO: heartbeat_only_owner_renews_lease, cancelled_run_rejects_heartbeat, find_stale_and_interrupted_steps, released_running_lease_is_recoverable; RECOVERY. |
| REQ-E4.11: E4.11.1, E4.11.2, E4.11.3, E4.11.4 | REPO: stale_completion_discarded_by_lease_fence; ORCH: cancel_inflight_keeps_approval_spent_and_fences_result, late_success_after_cancellation_is_discarded; API: concurrent_execution_requests_preserve_authorization_and_fencing. |
| REQ-E4.12: E4.12.1, E4.12.2, E4.12.3, E4.12.4, E4.12.5, E4.12.6 | REPO: failure_stop_settlement_and_inflight_completion, failure_stop_all_terminal_while_run_waiting_approval_executes_two_step_transition; ORCH: failure_stop_suppresses_inflight_retry_and_settles_gates, failed_workflow_settles_waiting_approval, resolve_cannot_bypass_failure_stop. |
| REQ-E5.1: E5.1.1, E5.1.2, E5.1.3 | EXEC: mock_executor_only_registered_executor, executor_contract_rejects_malformed_results; ORCH: pre_dispatch_validation_fails_without_execution; strict MyPy. |
| REQ-E5.2: E5.2.1, E5.2.2, E5.2.3, E5.2.4 | ACCEPT: env_allowlist_no_parent_inheritance_contract; ORCH: pre_dispatch_validation_fails_without_execution, malformed_execution_results_fail_safely; EXEC: deterministic artifact references and dependency guards. |
| REQ-E5.3: E5.3.1, E5.3.2, E5.3.3 | ORCH: timeout_uses_failure_or_transient_retry_and_preserves_consumption, cancel_inflight_keeps_approval_spent_and_fences_result; EXEC: mock_cooperative_cancellation; RECOVERY startup/stale tests. |
| REQ-E5.4: E5.4.1, E5.4.2, E5.4.3, E5.4.4 | EXEC: mock_executor_only_registered_executor, mock_executor_no_fs_network_subprocess, no_shell_execution_in_executors_package, mock_executor_deterministic_output, registry_rejects_non_mock_registration. |
| REQ-E6.1: E6.1.1, E6.1.2 | tests/unit/test_config.py and tests/unit/test_health.py; actual API/UI bound 127.0.0.1:18866/18865; executor package AST/import guards. |
| REQ-E6.2: E6.2.1, E6.2.2, E6.2.3 | ORCH: pre_dispatch_validation_fails_without_execution; ACCEPT: env_allowlist_no_parent_inheritance_contract; design §12.3–12.4 explicitly disclaims OS isolation. |
| REQ-E6.3: E6.3.1, E6.3.2, E6.3.3 | ACCEPT: env_allowlist_no_parent_inheritance_contract; EXEC: no_shell_execution_in_executors_package; design §12.3; future subprocess criterion contractual/deferred, no subprocess delivered. |
| REQ-E6.4: E6.4.1, E6.4.2 | ACCEPT: no_secret_in_audit_or_logs (DEBUG-level actual gated execution); ORCH: configured_secret_absent_from_execution_audit; API: execution_results_redact_credentials; driver diagnostic suppression fix. |
| REQ-E6.5: E6.5.1, E6.5.2, E6.5.3, E6.5.4, E6.5.5 | REPO: consume_validation_rejects_every_binding, consumed_approval_never_restored, stale_completion_discarded_by_lease_fence; ORCH: failed_consume_leaves_step_unexecuted_and_regates, protected_executor_not_invoked_until_consumed_and_committed. |
| REQ-E6.6: E6.6.1, E6.6.2, E6.6.3 | ORCH: unsafe_or_indeterminate_not_retried; REPO: consumed_approval_never_restored; EXEC: mock_failure_modes, mock_executor_no_fs_network_subprocess; live indeterminate failure attempt 1. |
| REQ-E6.7: E6.7.1, E6.7.2 | ACCEPT: audit_append_only_no_update_delete; inspection of AuditLogRepository overrides and Wave 1 writes; design §12.5: append-only, not signed. |
| REQ-E6.8: E6.8.1, E6.8.2 | EXEC: no_shell_execution_in_executors_package, executors_do_not_import_repositories_services_models; design §12.4 lists prerequisites; no real-agent executor shipped. |
| REQ-E7.1: WHEN/THEN contract (no separately numbered AC) | API: create_start_detail_and_terminal_contract, invalid_template_is_validated_at_run_creation; Phase 1 test_create_run_returns_pending_status. |
| REQ-E7.2: WHEN/THEN contract (no separately numbered AC) | API: create_start_detail_and_terminal_contract, action_bodies_reject_client_authority, concurrent_execution_requests_preserve_authorization_and_fencing. |
| REQ-E7.3: WHEN/THEN contract (no separately numbered AC) | API: cancel_waiting_run_expires_approval, create_start_detail_and_terminal_contract; ORCH: cancel_pending_run_is_idempotent. |
| REQ-E7.4: E7.4.1, E7.4.2, E7.4.3 | API: create_start_detail_and_terminal_contract, step_parent_binding_and_audit_pagination; tests/unit/test_workflows_router.py approval projections; ORCH ordered chain tests. |
| REQ-E7.5: WHEN/THEN contract (no separately numbered AC) | API: step_parent_binding_and_audit_pagination; live audit results (15 events in successful flow) and public identifier visibility. |
| REQ-E7.6: WHEN/THEN contract (no separately numbered AC) | API: approval_http_flow_uses_server_operator, cancel_waiting_run_expires_approval; ORCH: existing_resolve_endpoint_expiry_semantics; live approve/reject/expired 409. |
| REQ-E7.7: E7.7.1, E7.7.1b, E7.7.2, E7.7.3, E7.7.4 | UI: all execution UI AppTests; tests/smoke/test_wave3_pages.py backend failure/empty states; client tests; strict MyPy; live agent API success and negative-status snapshots. |
| REQ-E7.8: WHEN/THEN contract (no separately numbered AC) | API: openapi_execution_contract_matches_export; exporter executed; health route-set guard includes only approved additive paths, merge absent. |
| REQ-E8.1: WHEN/THEN contract (no separately numbered AC) | All preceding rows; 64-pair transition table and parametrized binding/retry/failure tests consolidate multiple specification test names into semantic cases. See naming clarification. |
| REQ-E8.2: WHEN/THEN contract (no separately numbered AC) | Final quality gate table; CI integration selection; isolated migration upgrade/downgrade; actual OpenAPI equality. |
| REQ-E8.3: WHEN/THEN contract (no separately numbered AC) | MIGRATION: migration_008_upgrade_downgrade_and_legacy_roundtrip, migration_008_downgrades_populated_phase2_rows; Alembic command.check and foreign_key_check. |
