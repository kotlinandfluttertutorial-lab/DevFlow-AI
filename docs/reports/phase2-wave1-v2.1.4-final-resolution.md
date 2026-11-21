# Phase 2 Wave 1 — v2.1.4 Final Edge-Case Corrections

**Report date:** 2026-11-18
**Spec version:** v2.1.4 (supersedes v2.1.3)
**Branch:** `feature/phase2-wave1-workflow-engine-implementation`
**Task type:** Specification correction only — no production code, migrations, Codex, push, PR, or merge.

---

## Correction A — Expired pending approval during re-gating

### Root cause

The v2.1.3 `expire_and_re_gate` pre-check (step 1) queried for `status = 'pending' AND operation IS NOT NULL` but did not check `expires_at > now`. A pending approval that was itself already past its TTL — created by a prior re-gate call but not yet swept by the TTL sweep — would be returned as a valid reusable replacement. The step would proceed with an already-expired approval, allowing a user to resolve it only to get `APPROVAL_EXPIRED` immediately.

Additionally, the v2.1.3 algorithm caught `IntegrityError` generically. This would swallow `ForeignKeyViolation` (e.g. the `workflow_step_run_id` FK referencing a deleted step), `CheckConstraintViolation` (e.g. the `status` CHECK), and other integrity errors that indicate real data problems — not concurrency conflicts. These should propagate unchanged.

The algorithm also did not define what happens to the SQLAlchemy session after catching an `IntegrityError`. After an unhandled integrity error, an async SQLAlchemy session is in an invalid state; any further DB work without an explicit rollback and session reset will fail or produce incorrect results.

### Final architectural decisions

**CA.1 — Pre-check validates `expires_at > now`.** Step (1) of `expire_and_re_gate` is updated: `SELECT ... WHERE status = 'pending' AND operation IS NOT NULL AND expires_at > :now`. A pending approval with `expires_at <= now` is not returned as a reusable replacement — it falls through to step (2) where it is expired and a new one is created.

**CA.2 — `IntegrityError` catch narrowed to `UniqueConstraintViolation`.** Only the specific SQLAlchemy/sqlite3 `UniqueConstraintViolation` class (or equivalent) is caught as a concurrency conflict. All other `IntegrityError` subclasses re-raise unchanged, propagating to the caller.

**CA.3 — Session rollback required after `UniqueConstraintViolation`.** The algorithm now explicitly calls `await session.rollback()` before any further database work, then opens a fresh `session.begin()` context for the re-read. This ensures the SQLAlchemy session is in a clean state.

### Affected files and sections

| File | Section | Change |
|---|---|---|
| `requirements.md` | REQ-E3.8 | Pre-check includes `expires_at > now`; E3.8.10–E3.8.12 added |
| `requirements.md` | REQ-E8.1 | Added 3 CA named tests |
| `design.md` | §7.5 | Algorithm updated: pre-check `AND expires_at > :now`; `IntegrityError` → `UniqueConstraintViolation`; session rollback semantics documented |
| `tasks.md` | T3 | Done-when updated for CA |
| `tasks.md` | T10 | 3 CA tests added |

### New named tests (CA)

| Test | REQ |
|---|---|
| `test_expire_and_re_gate_stale_pending_approval_not_reused` | E3.8.10 |
| `test_expire_and_re_gate_non_unique_integrity_error_propagates_unchanged` | E3.8.11 |
| `test_expire_and_re_gate_session_rolled_back_after_unique_constraint_violation` | E3.8.12 |

### Transition legality

CA does not change any step or run state transitions. The step stays `WAITING_APPROVAL` throughout; no run status change is issued.

---

## Correction B — Failure-stop normalization

### Root cause

The v2.1.3 `derive_and_persist_run_status` algorithm required the caller to supply an `is_failure_stop` flag. `ApprovalService.resolve` (and potentially recovery) could call `derive` without knowledge of failure-stop, supplying `is_failure_stop=False`. Under that flag, with a `WAITING_APPROVAL` step and no active non-terminal steps, rule 4 would fire and the run would stay `waiting_approval`. If another caller then settled the last step, rule 2 would need to fire — but the run is `waiting_approval`, which is not in `VALID_FROM["failed"]`. The algorithm raised `InvalidStatusTransitionError`.

There was also the edge case where the last `WAITING_APPROVAL` step was settled *and* all other steps became terminal in the same call: rule 2 fires immediately, `current_run_status` is still `waiting_approval`, and no prior call had performed the intermediate `waiting_approval → running` transition. The two-step logic in v2.1.3 was described but the algorithm did not automatically perform it — it raised `InvalidStatusTransitionError` because `waiting_approval NOT in VALID_FROM["failed"]`.

### Final architectural decisions

**CB.1 — `is_failure_stop` derived from persisted step state.** The `derive_and_persist_run_status(run_id, session)` signature no longer accepts an `is_failure_stop` parameter. Instead, the function computes it from the step rows read inside the transaction: any step with `status == 'FAILED'` and `result_status` indicating a real failure (not a skipped/downstream-settled step) sets `is_failure_stop = True`. This prevents any caller from bypassing failure-stop by supplying an incorrect flag.

**CB.2 — Atomic two-step `waiting_approval → running → failed`.** When rule 2 fires (`is_failure_stop AND all steps terminal`) and `current_run_status == 'waiting_approval'`, the algorithm executes two `transition_status` calls with `commit=False` before the single final commit:
1. `transition_status(run_id, 'running', ['waiting_approval'], commit=False)` — legal: `VALID_FROM["running"] = ("pending", "waiting_approval")`.
2. `transition_status(run_id, 'failed', ['running'], commit=False)` — legal: `VALID_FROM["failed"] = ("running",)`.

Both execute in the same `BEGIN IMMEDIATE` transaction; no intermediate `running` state is ever committed as a visible snapshot. The `waiting_approval → failed` path that is not in `VALID_FROM` is thus never attempted as a single step.

**CB.3 — Concurrent approval resolution cannot bypass failure-stop.** Because `is_failure_stop` is derived from the step rows read under `BEGIN IMMEDIATE`, a concurrent `ApprovalService.resolve` call that has just approved a gate cannot race with failure-stop. If failure-stop is engaged (a real `FAILED` step exists in the DB), any concurrent `derive` call will read that step and compute `is_failure_stop = True`, preventing `waiting_approval` from being derived for a run already in failure-stop.

**CB.4 — Concurrent `derive` with two-step transition.** Two concurrent callers both reach rule 2 with `current == 'waiting_approval'`. The loser's first CAS (`waiting_approval → running`) gets `rowcount == 0` (the winner already moved the run). The loser catches `InvalidStatusTransitionError`, re-reads the run, finds it `failed` (the winner completed both steps), and returns.

### Transition legality table

| Transition | Context | VALID_FROM legal? |
|---|---|---|
| `waiting_approval → running` | Failure-stop, non-terminal steps remain (rule 3) | ✓ `VALID_FROM["running"] = ("pending","waiting_approval")` |
| `waiting_approval → running` | First step of two-step (CB rule 2, all terminal) | ✓ same |
| `running → failed` | Second step of two-step (CB rule 2, all terminal) | ✓ `VALID_FROM["failed"] = ("running",)` |
| `waiting_approval → failed` | Direct — **never issued** | ✗ Not in `VALID_FROM` |

### Affected files and sections

| File | Section | Change |
|---|---|---|
| `requirements.md` | REQ-E2.10 | Rewrote: signature `(run_id, session)`; `is_failure_stop` derived inside; two-step rule (CB) documented; E2.10.7 updated; E2.10.10 added |
| `requirements.md` | REQ-E8.1 | Added 4 CB named tests |
| `design.md` | §5.9 | Algorithm rewritten: `is_failure_stop` derived from rows; two-step special case; signature `(run_id, session)` |
| `tasks.md` | T3, T5 | Signature change; `is_failure_stop` from persisted state; CB tests |
| `tasks.md` | T10 | 4 CB tests added |

### New named tests (CB)

| Test | REQ |
|---|---|
| `test_failure_stop_all_terminal_while_run_waiting_approval_executes_two_step_transition` | E2.10.7 |
| `test_failure_stop_two_step_transition_atomic_no_intermediate_running_visible` | E2.10.7 |
| `test_concurrent_approval_resolve_cannot_bypass_failure_stop` | E2.10.10 |
| `test_derive_uses_persisted_step_state_for_failure_stop_flag` | E2.10.10 |

---

## Consistency verification

| Check | Result |
|---|---|
| `waiting_approval → failed` never issued directly | ✓ Two-step guaranteed by CB.2 |
| `is_failure_stop` from persisted state only | ✓ Caller flag removed (CB.1) |
| Stale pending approval not reused | ✓ `expires_at > now` in pre-check (CA.1) |
| `UniqueConstraintViolation` narrowed | ✓ Other `IntegrityError` subclasses propagate (CA.2) |
| Session rollback before re-read | ✓ Explicit `session.rollback()` (CA.3) |
| Phase 1 `VALID_FROM` unchanged | ✓ |
| No ninth step status | ✓ |
| `approval_requests.status` CHECK unchanged | ✓ |
| `workflow_runs.status` CHECK unchanged | ✓ |

---

## Residual risks

1. **`is_failure_stop` computation cost.** Each `derive` call now reads all step rows and scans for real `FAILED` steps. For runs with many steps this adds a small query cost. At Wave 1 (concurrency=1, mock executor only) this is negligible; a future optimization could cache the flag in `workflow_runs.failure_stop_at` column.

2. **`UniqueConstraintViolation` class name.** The exact SQLAlchemy exception class depends on the backend adapter and version. The implementer must verify the correct import (`sqlalchemy.exc.IntegrityError` with a `pgcode` check, or `sqlite3.IntegrityError` with a message check for SQLite). Recommend a utility function `is_unique_violation(exc)` to encapsulate the check.

3. **Two-step timing window.** Between the two `commit=False` calls inside `BEGIN IMMEDIATE`, the write lock is held for two sequential SQL statements. This is safe for SQLite (single-writer) but adds ~1 ms to lock hold time. No functional risk.

4. **Re-read after `UniqueConstraintViolation` with expired winner.** The edge case where the winner's newly-created approval has also expired by the time the loser re-reads is documented (raises `DatabaseBusyError`). In practice this requires the TTL to be very short (< seconds). No Wave 1 risk (TTL = 3600s).

5. **Migration head re-confirmation (pre-existing).** Implementer must re-confirm Alembic head `007` before authoring migration 008.

---

## Final verdict

**PHASE 2 WAVE 1 SPEC v2.1.4 — AWAITING CHATGPT REVIEW**

No production code was written. No migrations were executed. No Codex was invoked. No push, PR, or merge was performed.
