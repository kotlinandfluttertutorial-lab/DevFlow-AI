# Phase 2 Wave 1 — Final Specification Corrections Report (v2.1.2 → v2.1.3)

**Report date:** 2026-11-18
**Spec version:** v2.1.3 (supersedes v2.1.2)
**Branch:** `feature/phase2-wave1-workflow-engine-implementation`
**Commit:** `3d7d161` (v2.1.2 Codex blocker resolution; this correction built on top)
**Task type:** Specification correction only — no production code, migrations, Codex, push, PR, or merge.

---

## F18 — Correct failure-stop status precedence

### Root cause

The v2.1.2 `derive_and_persist_run_status` precedence table (design §5.9, REQ-E2.10) placed `WAITING_APPROVAL` at priority 2 above failure-stop's `running` at priority 3. This produced a wrong result: when failure-stop was engaged and a `WAITING_APPROVAL` step had not yet been settled, the derivation would compute `waiting_approval` instead of `running`. The run would stall in `waiting_approval` even though the approval gate was about to be invalidated by the failure-stop policy.

Additionally, the `else: lost concurrent race` comment in the algorithm silently dropped any illegal transition (`current_run_status NOT in VALID_FROM[target]`), hiding programming errors. The spec gave no guidance on what to do when `waiting_approval → failed` was required (which is not in `VALID_FROM["failed"] = ("running",)`).

### Final architectural decisions

**F18a — Failure-stop precedes `WAITING_APPROVAL` in the precedence table.** The corrected table (§5.9, REQ-E2.10) is:

| Priority | Condition | Run status |
|---|---|---|
| 1 | `cancelled` (terminal; no-op) | `cancelled` |
| 2 | `is_failure_stop` AND all steps terminal | `failed` |
| **3** | **`is_failure_stop` AND any step non-terminal** | **`running`** |
| **4** | **(NOT `is_failure_stop`) AND any step `WAITING_APPROVAL`** | **`waiting_approval`** |
| 5 | Any step `READY`/`RUNNING`/`INTERRUPTED` | `running` |
| 6 | All steps `SUCCEEDED` | `completed` |
| 7 | All steps `PENDING` | `pending` |

Under failure-stop, rule 3 fires before rule 4. A `WAITING_APPROVAL` step during failure-stop is about to be settled; the run stays `running` until all steps are terminal.

**F18b — `waiting_approval → running` is an explicit legal transition.** `VALID_FROM["running"] = ("pending", "waiting_approval")`. When failure-stop settles a `WAITING_APPROVAL` step (→ `FAILED(skipped)`) and the run is `waiting_approval`, rule 3 fires, deriving `running`. The transition `waiting_approval → running` is legal per `VALID_FROM` and must be issued explicitly by `derive_and_persist_run_status`.

**F18c — `waiting_approval → failed` is NOT a direct transition.** `VALID_FROM["failed"] = ("running",)`. To reach `failed`, the run must first be `running`. The sequence during failure-stop settlement is: (a) `WAITING_APPROVAL` step settled → `FAILED(skipped)`; (b) derive fires: `is_failure_stop + non-terminal exist` → `running` (rule 3; `waiting_approval → running`); (c) last in-flight step terminates → all terminal; (d) derive fires again: `is_failure_stop + all terminal` → `failed` (rule 2; `running → failed`). Both transitions are legal.

**F18d — Illegal transitions raise, not no-op.** If `current_run_status NOT in VALID_FROM[target]`, `InvalidStatusTransitionError` is raised. Callers catch it, re-read the run status, verify it already equals target (concurrent winner already set it), and return. This is a one-time re-read, not a retry loop.

### Affected files and sections

| File | Section | Change |
|---|---|---|
| `requirements.md` | REQ-E2.10 | Rewrote precedence table: failure-stop rule split into "all terminal → failed" (rule 2) and "non-terminal → running" (rule 3); `WAITING_APPROVAL` rule moves to rule 4 (non-failure-stop only); illegal transition raises error; E2.10.4–E2.10.9 rewritten/added |
| `requirements.md` | REQ-E8.1 | Added 5 F18 named tests |
| `design.md` | §5.9 | Rewrote precedence table and algorithm: `else: lost concurrent race` → `raise InvalidStatusTransitionError`; added clarifications on `waiting_approval → failed` two-step sequence; `waiting_approval → running` explicitly named as legal |
| `tasks.md` | T3, T5, T10 | Updated derive-related done-when criteria; F18 tests added to T10 |

### Transition legality summary

| Transition | VALID_FROM | Context | Legal? |
|---|---|---|---|
| `running → waiting_approval` | `VALID_FROM["waiting_approval"] = ("running",)` | First gate | ✓ |
| `waiting_approval → running` | `VALID_FROM["running"] = ("pending","waiting_approval")` | Failure-stop rule 3 OR last gate approved | ✓ |
| `running → failed` | `VALID_FROM["failed"] = ("running",)` | Failure-stop rule 2 (all terminal) | ✓ |
| `waiting_approval → failed` | Not in VALID_FROM | Direct — **never issued** | ✗ (two-step via `running`) |
| `failed → running` | Not in VALID_FROM | — | ✗ (raises) |

### New named tests (F18)

| Test | REQ |
|---|---|
| `test_failure_stop_with_waiting_approval_derives_running_not_waiting` | E2.10.4 |
| `test_failure_stop_settles_waiting_approval_step_transitions_run_waiting_to_running` | E2.10.7 |
| `test_failure_stop_all_steps_terminal_after_gate_settled_derives_failed` | E2.10.6 |
| `test_derive_illegal_transition_raises_not_silent_noop` | E2.10.8 |
| `test_derive_concurrent_race_caller_retries_once_and_succeeds` | E2.10.9 |

---

## F19 — Guarantee unique pending approval during re-gating

### Root cause

The v2.1.2 `expire_and_re_gate` specification (design §7.5, REQ-E3.8) acknowledged a double-approval window in E3.8.3 and E3.8.7: if two callers ran concurrently and both found no existing `pending` approval, both would create one. The spec said this was "acceptable for Wave 1 (concurrency=1)" and deferred a fix to "a future index." F19 requires this to be fixed now.

The original algorithm was: expire existing + unconditional INSERT. Under concurrent calls this produced two valid `pending` approvals for the same step, potentially allowing two different actors to approve the same gate.

### Final architectural decisions

**F19a — Partial unique index `uidx_approval_step_pending`.** A SQLite partial unique index is added to migration 008:

```sql
CREATE UNIQUE INDEX uidx_approval_step_pending
    ON approval_requests(workflow_step_run_id)
    WHERE status = 'pending' AND operation IS NOT NULL;
```

- `operation IS NOT NULL` excludes Phase 1 rows; legacy behavior is unaffected.
- `downgrade()` drops this index.
- SQLite supports partial unique indexes via `WHERE` clauses (since SQLite 3.8.9; the repo uses SQLite 3.49.1).

**F19b — Guarded atomic insert with idempotency check.** The `expire_and_re_gate` algorithm is rewritten with three levels of protection:

1. **Pre-check (idempotency guard):** inside `BEGIN IMMEDIATE`, check for an existing `pending` Phase 2 approval for this step. If one exists, return it immediately (the prior call already created it). This handles the common case — repeated reconciliation passes — without touching the database.

2. **Expire + insert:** expire the old `pending` approval (if any), then insert the new one. The partial unique index enforces uniqueness at the database level.

3. **`IntegrityError` catch:** if the insert fails due to a uniqueness violation (concurrent call won the race between the pre-check and the insert), catch the error, commit/release the lock, re-read the existing `pending` approval, and return it. This handles the narrow race window.

**F19c — `SQLITE_BUSY` retry policy.** `BEGIN IMMEDIATE` may return `SQLITE_BUSY` if the write lock is held. The spec requires up to 3 retry attempts with exponential backoff (10 ms, 20 ms, 40 ms). On exhaustion, `DatabaseBusyError` is raised and propagates to the caller.

**F19d — Audit trail preserved.** Expired approvals retain their history (append-only audit log). The idempotency path (`approval_re_gate_noop`) writes an informational audit entry without modifying any existing record.

### Migration 008 DDL addition

```sql
-- Added to the approval_requests ALTER section of migration 008:
CREATE UNIQUE INDEX uidx_approval_step_pending
    ON approval_requests(workflow_step_run_id)
    WHERE status = 'pending' AND operation IS NOT NULL;

-- downgrade() must drop it:
DROP INDEX IF EXISTS uidx_approval_step_pending;
```

### Affected files and sections

| File | Section | Change |
|---|---|---|
| `requirements.md` | REQ-E3.8 | Rewrote to specify guarded-insert with index; E3.8.3 rewrites (idempotent returns existing, no duplicate); E3.8.7 rewrites (index enforces uniqueness); E3.8.9 added (SQLITE_BUSY retry) |
| `requirements.md` | REQ-E3.1.1 | (unchanged; migration-added columns note covers the new index via E3.8.7) |
| `requirements.md` | REQ-E8.1 migration tests | Added `test_migration_008_creates_partial_unique_index` |
| `requirements.md` | REQ-E8.1 | Added 6 F19 named tests |
| `design.md` | §3.3 | Added `CREATE UNIQUE INDEX uidx_approval_step_pending` to `approval_requests` DDL and `downgrade()` note |
| `design.md` | §7.5 | Rewrote `expire_and_re_gate` with guarded-insert algorithm, `IntegrityError` handling, `SQLITE_BUSY` retry, and design notes |
| `tasks.md` | T2 | Migration 008 must include `CREATE UNIQUE INDEX uidx_approval_step_pending` and its `downgrade()` drop |
| `tasks.md` | T3 | `expire_and_re_gate` updated with `IntegrityError` catch, retry policy |
| `tasks.md` | T10 | 6 F19 tests added |

### New named tests (F19)

| Test | REQ |
|---|---|
| `test_expire_and_re_gate_concurrent_calls_produce_exactly_one_pending_approval` | E3.8.3 |
| `test_expire_and_re_gate_second_call_returns_existing_pending_no_duplicate` | E3.8.3 |
| `test_expire_and_re_gate_sqlite_busy_retried_bounded` | E3.8.9 |
| `test_expire_and_re_gate_partial_unique_index_enforces_uniqueness` | E3.8.7 |
| `test_phase1_approval_unaffected_by_partial_unique_index` | E3.8.7 |
| `test_migration_008_creates_partial_unique_index` | E3.8.7 |

---

## Consistency verification

| Check | Result |
|---|---|
| `WAITING_APPROVAL → running` appears in VALID_FROM | ✓ `VALID_FROM["running"] = ("pending","waiting_approval")` — legal |
| `waiting_approval → failed` never issued directly | ✓ Two-step path documented in §5.9 |
| Partial unique index excludes Phase 1 rows | ✓ `WHERE operation IS NOT NULL` |
| `downgrade()` drops the index | ✓ Specified in §3.3 DDL notes and T2 |
| No ninth step status introduced | ✓ |
| Phase 1 `VALID_FROM` map unchanged | ✓ |
| `approval_requests.status` CHECK unchanged | ✓ |
| `workflow_runs.status` CHECK unchanged | ✓ |

---

## Remaining risks

1. **SQLite partial unique index syntax:** `CREATE UNIQUE INDEX ... WHERE ...` requires SQLite ≥ 3.8.9. The repo uses SQLite 3.49.1 (verified in Phase 1 CI). No risk.

2. **`IntegrityError` catch scope:** the catch must be specific to `sqlite3.IntegrityError` (or SQLAlchemy's `IntegrityError`) and not silently swallow other integrity errors (e.g., FK violations). The implementer must scope the catch correctly.

3. **`derive_and_persist_run_status` and terminal run re-entry:** a caller that passes `is_failure_stop=False` for a run that already has a real `FAILED` step (e.g., during recovery after restart) will compute the wrong target. Callers must always pass `is_failure_stop` correctly by checking whether any real (non-skipped) `FAILED` step exists for the run before calling.

4. **`waiting_approval → failed` two-step timing:** if the failure-stop settle and the final step completion race, the intermediate `running` state may be visible briefly in the API. This is correct behavior (the run is `running` between the two derive calls); no inconsistency results.

5. **Migration head re-confirmation:** implementer must re-confirm Alembic head `007` before authoring migration 008.

---

## Final verdict

**PHASE 2 WAVE 1 SPEC v2.1.4 — AWAITING CHATGPT REVIEW**

No production code was written. No migrations were executed. No Codex was invoked. No push, PR, or merge was performed.
