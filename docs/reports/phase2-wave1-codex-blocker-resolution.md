# Phase 2 Wave 1 — Codex Blocker Resolution Report

**Report date:** 2026-11-18
**Spec version:** v2.1.2 (supersedes v2.1.1)
**Branch:** `feature/phase2-wave1-workflow-engine-implementation`
**Base:** `de209b695c41824835dcd504b61bc326c01bf97d` (PR #14 merged; Phase 2 Wave 1 spec v2.1.1)
**Task type:** Specification correction only — no production code, migrations, Codex, push, PR, or merge.

Codex reported implementation blocked on two architectural ambiguities before task T1. This report documents each blocker's root cause, the final architectural decision, the updated specification artefacts, and remaining risks.

---

## Blocker 1 — Approval expiry semantics (F16)

### Root cause

The v2.1.1 specification had a conflict between three requirements:

- **REQ-E2.5**: returns `APPROVAL_ALREADY_RESOLVED` for any approval whose status is no longer `pending`, then `APPROVAL_EXPIRED` for a pending approval past TTL.
- **REQ-E3.4.1**: expects `APPROVAL_EXPIRED` after the TTL sweep has *already* changed status to `expired`.
- Ambiguity: when the sweep has run first, `status == 'expired'`; `APPROVAL_ALREADY_RESOLVED` would fire before the expiry check, giving a misleading response code.

Additionally, the spec said a `WAITING_APPROVAL` step could re-gate through `READY` after expiry. But a step in `WAITING_APPROVAL` is not `READY` — there is no transition path to `READY` except `WAITING_APPROVAL → READY` (approval approved). Going through `READY` to re-gate would require creating an approved approval (which hasn't happened) or skipping a valid state, breaking the transition map.

### Final architectural decisions

**F16a — Expiry response code takes precedence for Phase 2 expired approvals.** The resolve path checks expiry before `APPROVAL_ALREADY_RESOLVED`:
1. If Phase 2 approval (`operation IS NOT NULL`) and `status == 'pending'` and `now >= expires_at`: **`APPROVAL_EXPIRED`** (pre-sweep).
2. If Phase 2 approval and `status == 'expired'`: **`APPROVAL_EXPIRED`** (post-sweep).
3. If `status != 'pending'` for any other reason: `APPROVAL_ALREADY_RESOLVED`.
4. Phase 1 approvals (`expires_at IS NULL`): `APPROVAL_ALREADY_RESOLVED` for all non-pending, unchanged.

Both pre-sweep and post-sweep return the same code — callers get a consistent, actionable response regardless of sweep timing.

**F16b — Re-gating from `WAITING_APPROVAL` goes through `expire_and_re_gate`, not through `READY`.** A new atomic operation `expire_and_re_gate(step_run_id, new_approval_data, run_id, session)` replaces the old approval and creates a new one in one `BEGIN IMMEDIATE` transaction. The step status stays `WAITING_APPROVAL` throughout. The run status is unchanged. This is represented as a `WAITING_APPROVAL → WAITING_APPROVAL` self-transition in the step state machine (not a new state). Guards prevent this operation when the run is cancelled or failure-stop is engaged (the failure-stop settle path will handle the step instead).

### Updated files and sections

| File | Section | Change |
|---|---|---|
| `requirements.md` | REQ-E2.4 | Added E2.4.6: re-gate from WAITING_APPROVAL via `expire_and_re_gate`, no intermediate READY |
| `requirements.md` | REQ-E2.5 | Rewritten: expiry check precedes already-resolved; pre-sweep and post-sweep both return APPROVAL_EXPIRED |
| `requirements.md` | **REQ-E3.8** (new) | `expire_and_re_gate` specification: pre/post-sweep, idempotent, guards, concurrent risk |
| `requirements.md` | REQ-E4.2 transition map | Added `WAITING_APPROVAL → WAITING_APPROVAL` row |
| `requirements.md` | REQ-E8.1 | Added 10 F16 named tests |
| `design.md` | §7.3 | Added expiry response precision (F16); notes pre-sweep and post-sweep behaviour |
| `design.md` | §7.4 | Added paragraph: re-gate from WAITING_APPROVAL goes through §7.5, not READY |
| `design.md` | **§7.5** (new) | `expire_and_re_gate` algorithm and SQLite pattern |
| `design.md` | §7.6, §7.7, §7.8 | Renumbered from §7.5–7.7 |
| `design.md` | §7.7 (resolve) | Rewrote with F16 check order |
| `design.md` | §8.2 state map | Added `WAITING_APPROVAL → WAITING_APPROVAL` row |
| `tasks.md` | T3 | Added `expire_and_re_gate` to ApprovalRequestRepository |
| `tasks.md` | T5 | Added expiry re-gate from WAITING_APPROVAL path |
| `tasks.md` | T6 | Updated resolve order; wired `expire_and_re_gate` into reconciliation sweep |
| `tasks.md` | T10 | Added F16 named tests |

### Compatibility implications

- Phase 1 `ApprovalService.resolve` behavior unchanged: Phase 1 approvals (`expires_at IS NULL`) still return `APPROVAL_ALREADY_RESOLVED` for all non-pending states.
- No new step states introduced; `WAITING_APPROVAL → WAITING_APPROVAL` is a self-transition modelled as an atomic record swap, not an ORM-level status change.
- Phase 1 `WorkflowRunRepository.VALID_FROM` is not touched by `expire_and_re_gate`.

---

## Blocker 2 — Concurrent approval gates and persisted run status (F17)

### Root cause

The Phase 1 `WorkflowRunRepository.VALID_FROM` map contains:
```python
"waiting_approval": ("running",)   # only "running" is a valid from-state
```

The v2.1.1 spec instructed `request_bound_approval` to call `run_repo.transition_status(run_id, "waiting_approval", ["running"])`. This works for the **first gate** (run is `running`). For a **second independent gate** (run is already `waiting_approval`), `from_state = ["running"]` does not match and the transition would raise `InvalidStatusTransitionError`.

Separately, the spec stated that `ApprovalService.resolve` with decision `approved` causes `waiting_approval → running`. But if two gates exist and only one is approved, the run should stay `waiting_approval`. The `VALID_FROM` path works for single-gate runs but is incorrect for multi-gate runs.

There was no specification of which run status wins when multiple step states coexist (e.g., one step `RUNNING`, another `WAITING_APPROVAL`, a third `FAILED`).

### Final architectural decisions

**F17a — `derive_and_persist_run_status` replaces ad-hoc `transition_status` calls for run status.** A new helper, implemented on the repository layer but called by the orchestrator and `ApprovalService.resolve`, reads all step statuses in one `BEGIN IMMEDIATE` transaction and applies a deterministic precedence table to compute the target run status. It then calls `transition_status` only if a valid transition exists. This is the single authority for run status after any step state change (except the initial `pending → running` which remains a direct `WorkflowService.transition_run` call).

**F17b — Multi-gate handling.** The first gate uses `request_bound_approval` → `transition_status("waiting_approval", ["running"])` as before. Subsequent gates (run already `waiting_approval`) skip the run transition: they transition only the step (`READY → WAITING_APPROVAL`) and call `derive_and_persist_run_status` to confirm idempotent `waiting_approval`. The `VALID_FROM` constraint is never violated because `waiting_approval → waiting_approval` is never issued as a `transition_status` call.

**F17c — Status-precedence table.** Priority order (highest wins):

| Priority | Condition | Run status |
|---|---|---|
| 1 | failure-stop AND all steps terminal | `failed` |
| 2 | (cancel path; handled externally) | `cancelled` |
| 3 | Any step `WAITING_APPROVAL` | `waiting_approval` |
| 4 | Any step `READY`/`RUNNING`/`INTERRUPTED` | `running` |
| 5 | All steps `SUCCEEDED` | `completed` |
| 6 | All steps `PENDING` | `pending` |

**F17d — Approval resolve triggers `derive_and_persist_run_status`.** When `resolve` approves a Phase 2 gate, it transitions the step `WAITING_APPROVAL → READY` and then calls `derive_and_persist_run_status`. If other `WAITING_APPROVAL` steps remain, the derivation stays `waiting_approval` (priority 3 wins). If all gates are cleared, the derivation moves to `running` (priority 4). This replaces the previous unconditional `waiting_approval → running` after every approval.

**F17e — Concurrent `derive_and_persist_run_status` calls.** Both calls operate inside `BEGIN IMMEDIATE`. If two concurrent calls target the same state, the loser's `transition_status` CAS (`WHERE status = current`) gets `rowcount == 0` (the winner already updated it) — a silent no-op. The final run status is correct. No explicit retry is needed.

### Updated files and sections

| File | Section | Change |
|---|---|---|
| `requirements.md` | REQ-E2.4 | Updated: derive_and_persist_run_status replaces approval-service transition call for run status |
| `requirements.md` | REQ-E2.5 | Added E2.5.1 clarification: approved may leave run waiting_approval; added E2.5.5 |
| `requirements.md` | REQ-E2.9 | Rewritten: VALID_FROM map documented; multi-gate handling (second gate skips run transition); derive_and_persist_run_status as the authority |
| `requirements.md` | **REQ-E2.10** (new) | Full specification: derive_and_persist_run_status algorithm, precedence table, acceptance criteria |
| `requirements.md` | REQ-E8.1 | Added 12 F17 named tests |
| `requirements.md` | Traceability matrix | Added REQ-E2.10 → §5.9 |
| `design.md` | §5.8 | Updated: table shows `derive_and_persist_run_status` for completed/failed/running; multi-gate note with VALID_FROM explanation |
| `design.md` | **§5.9** (new) | `derive_and_persist_run_status` algorithm, precedence table, concurrent safety, failure-stop flag |
| `design.md` | §7.7 resolve | Updated: calls `derive_and_persist_run_status`; run stays waiting_approval if other gates remain |
| `tasks.md` | T3 | Added `derive_and_persist_run_status` to repository layer |
| `tasks.md` | T5 | Updated: multi-gate skip; derive called for all run status changes; initial start still direct |
| `tasks.md` | T6 | Updated: resolve triggers derive; first gate vs subsequent gate distinction |
| `tasks.md` | T10 | Added F17 named tests |
| `tasks.md` | Completion gate | Added F17 invariant checks |

### Compatibility implications

- `WorkflowRunRepository.VALID_FROM` is **not changed**. `derive_and_persist_run_status` only issues calls that are within `VALID_FROM`; it never issues `waiting_approval → waiting_approval`.
- Phase 1 `ApprovalService.resolve` for single-gate runs produces the same observable result: the run returns to `running` after the one gate is approved (derivation rule 4 fires because no `WAITING_APPROVAL` step remains).
- Phase 1 `WorkflowService.transition_run` is still used for `pending → running` (initial start) and may be used by `derive_and_persist_run_status` for `running → completed`/`failed`.
- `request_bound_approval` signature is unchanged; the multi-gate guard is an internal implementation detail.

---

## 3. No ninth step status introduced

Both blockers are resolved without adding a new step status. `WAITING_APPROVAL → WAITING_APPROVAL` is a data-level swap (expire + create new approval, step status field unchanged). `derive_and_persist_run_status` operates on existing run statuses only.

---

## 4. Test requirements traceability

| New named test | REQ | Blocker |
|---|---|---|
| `test_approval_expired_pre_sweep_returns_expired_code` | E2.5.3, E3.8.1 | F16 |
| `test_approval_expired_post_sweep_returns_expired_code` | E2.5.3, E3.8.2 | F16 |
| `test_phase1_approval_already_resolved_still_returned_for_expired` | E2.5.2 | F16 |
| `test_expire_and_re_gate_pre_sweep_waiting_approval_step` | E3.8.1, E2.4.6 | F16 |
| `test_expire_and_re_gate_post_sweep_waiting_approval_step` | E3.8.2 | F16 |
| `test_expire_and_re_gate_idempotent` | E3.8.3 | F16 |
| `test_expire_and_re_gate_refused_when_run_cancelled` | E3.8.5 | F16 |
| `test_expire_and_re_gate_refused_when_failure_stop_engaged` | E3.8.6 | F16 |
| `test_concurrent_expire_and_re_gate_produces_valid_state` | E3.8.7 | F16 |
| `test_old_approval_id_returns_expired_after_re_gate` | E3.8.8 | F16 |
| `test_two_independent_gates_run_stays_waiting_approval` | E2.10.1, E2.9.3 | F17 |
| `test_approve_one_of_two_gates_run_stays_waiting_approval` | E2.10.1, E2.5.5 | F17 |
| `test_approve_both_gates_run_becomes_running` | E2.10.2, E2.5.5 | F17 |
| `test_reject_one_gate_cancels_run_regardless_of_other_gate` | E2.5.1 | F17 |
| `test_executor_fails_while_gate_pending_run_stays_running_until_settled` | E2.10.4, E4.4.2 | F17 |
| `test_derive_run_status_all_succeeded_returns_completed` | E2.10.3 | F17 |
| `test_derive_run_status_any_waiting_returns_waiting_approval` | E2.10.1 | F17 |
| `test_derive_run_status_failure_stop_all_terminal_returns_failed` | E2.10.4 | F17 |
| `test_derive_run_status_failure_stop_inflight_step_stays_running` | E2.10.4 | F17 |
| `test_derive_run_status_idempotent_on_terminal_run` | E2.10.3 | F17 |
| `test_derive_run_status_concurrent_calls_produce_consistent_state` | E2.10.5 | F17 |
| `test_run_status_precedence_failed_beats_waiting_approval` | E2.10 table, priority 1 vs 3 | F17 |

All F14/F15 named tests (v2.1.1) are preserved unchanged.

---

## 5. Remaining risks

1. **Concurrent `expire_and_re_gate` double-approval window (REQ-E3.8.7):** two concurrent reconciliation sweeps could each create a `pending` approval for the same step, resulting in two valid pending approvals. Wave 1 (concurrency=1) makes this unreachable; a future `UNIQUE(workflow_step_run_id) WHERE status='pending'` partial-unique index (SQLite supports partial indexes via CHECK) can close it. Documented as a known risk.

2. **`derive_and_persist_run_status` and `WorkflowService.transition_run` overlap:** `derive_and_persist_run_status` calls `run_repo.transition_status` for completed/failed; `WorkflowService.transition_run` also calls `run_repo.transition_status` for the same transitions. If both are called in a race, one will get `rowcount == 0` (the from-state CAS fails). This is safe (the winner set the correct status) but the caller must handle the `InvalidStatusTransitionError` from `transition_run` if status has already advanced. Implementer must add a try/except for this case or switch all terminal transitions to `derive_and_persist_run_status`.

3. **Migration 008 head-confirmation (pre-existing):** implementer must re-confirm Alembic head `007` before authoring migration 008.

4. **Settle target (`FAILED` vs `CANCELLED`) choice (pre-existing from F15):** blocked steps settle to `FAILED(skipped)`. This remains correct and unchanged.

---

## 6. Final specification readiness verdict

All specification documents updated to v2.1.2. The two Codex implementation blockers (F16 approval expiry semantics, F17 concurrent approval gates) are resolved with fully specified behavior, compatibility with Phase 1 services preserved, and new named tests covering both pre-sweep and post-sweep expiry, concurrent gates, run status precedence, derive-idempotency, and concurrent derive safety.

**PHASE 2 WAVE 1 SPEC v2.1.4 — AWAITING CHATGPT REVIEW**
