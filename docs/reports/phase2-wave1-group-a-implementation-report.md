# Phase 2 Wave 1 Group A implementation report

**2026-11-19**

**PHASE 2 WAVE 1 GROUP A COMPLETE — AWAITING REVIEW**

## Preflight

- Repository `J:\Android\GenAI\DevFlow-AI`.
- Branch `feature/phase2-wave1-workflow-engine-implementation`.
- Starting commit `2386a784d39c1bac3526923342985d70d4ce9a36`, containing the v2.1.4 final edge-case corrections.
- Starting tracked and untracked working-tree status was clean. The earlier local implementation-blocker report was present.
- The requirements, design, tasks, and `phase2-wave1-v2.1.4-final-resolution.md` were available locally.
- The project `.venv` uses pyenv-win Python 3.12.10.
- Alembic returned `007_add_github_confirmations` before migration authoring. The resulting sole head is `008_add_workflow_execution_engine`.

## Completed work

### T1

Added Pydantic workflow-template, step, tool-selection, retry-policy, default, and approval-operation types. Validation covers nonempty templates, unique keys, dependency existence, cycles, duplicate dependencies, approval metadata, executor-selection consistency, positive versions and execution limits, JSON-compatible inputs, and finite numeric values.

The parsing helper raises `InvalidWorkflowTemplateError` carrying `INVALID_WORKFLOW_TEMPLATE`. Canonical serialization and SHA-256 helpers support snapshot and approval digests. Output-reference resolution belongs to the later orchestration task.

### T2

Added `WorkflowStepRun` with the eight-state CHECK, run/key uniqueness, ordered identifiers, execution results, input digests, attempt/idempotency fields, UTC timestamps, and leases. Added nullable run snapshot/version/hash columns and approval binding, consumption, and expiry columns.

Migration 008 adds the required indexes, including `uidx_approval_step_pending` with the Phase 2 pending-approval predicate. Its approval-table rebuild explicitly carries the Phase 1 status CHECK and AUTOINCREMENT high-water mark.

### T3

Implemented snapshot-plus-step creation, guarded snapshot updates, step CAS transitions, dispatch, protected dispatch with atomic approval consumption, fenced result persistence, lease renewal/release, stale and interrupted queries, failure-stop settlement, and transactional run-state derivation.

Approval helpers cover validated bindings, single-use consumption, pending expiry, resolution-anchored authorization validity, and concurrency-safe expiry re-gating. Bound issuance checks the configured local operator identity. Re-gating checks persisted run and step state, and dispatch checks the snapshot approval policy and attempt budget.

The repository uses the existing approval-repository alias and Phase 1 run transition method. The existing approval service and router continue to use their current interfaces.

## Validation evidence

All commands used the project `.venv\Scripts\python.exe`.

| Check | Final result |
|---|---|
| `ruff check .` | Passed |
| `ruff format --check .` | Passed, 159 files |
| `mypy --strict src/ tests/` | Passed, 149 source files |
| Focused Group A schema, repository, and migration tests | 139 passed |
| Full suite with branch-inclusive coverage and 80% threshold | 452 passed, 93.98% coverage |
| `alembic heads` | Sole head `008_add_workflow_execution_engine` |
| `git diff --check` | Passed |

Focused verification used the following test files with `--no-cov`.

- `tests/unit/test_workflow_template.py`
- `tests/unit/test_workflow_execution_repositories.py`
- `tests/integration/test_workflow_execution_migration.py`

The final full-suite invocation was

```powershell
.\.venv\Scripts\python.exe -m pytest -v --cov=src --cov-branch --cov-fail-under=80 --cov-report=term-missing --basetemp=J:/Android/GenAI/DevFlow-AI/.pytest-group-a-full-2 -o cache_dir=J:/Android/GenAI/DevFlow-AI/.pytest-cache-group-a
```

Pytest's default temporary directory was inaccessible in this execution environment. Workspace-local temporary and cache directories allowed execution with the existing test assertions. An earlier full run encountered a three-second Streamlit AppTest timeout. That test passed in an isolated rerun and in the final full suite.

Two existing migration tests now assert head 008. The settings example includes the two new approval-lifetime fields required by the existing settings-template parity test.

## Migration and compatibility evidence

Isolated migration tests exercise empty-database upgrade through 008, full downgrade to base and upgrade, repeated 007 → 008 → 007 cycles with legacy records, and populated Phase 2 downgrade followed by upgrade. Alembic `command.check` verifies ORM/schema agreement.

Assertions cover legacy run and approval records, nullable legacy snapshot/binding values, both Phase 1 status CHECK definitions, foreign-key integrity, partial-index creation/removal, eight-state enforcement, populated approval-table copying, and preservation of a deleted approval's AUTOINCREMENT high-water mark. Existing MCP and GitHub migration/deletion tests pass in the full suite.

Migration execution targeted test databases. Applying 008 to an operational database remains a separate operator action.

## Security and concurrency evidence

- Independent file-backed SQLite sessions prove one dispatch winner and one protected-dispatch winner.
- The seven consumption-binding rejection cases verify rollback of status, attempt, owner, expiry, and dispatch audit, followed by a rejection audit carrying the mismatch category.
- The atomic consumption test observes `READY` and attempt zero from a second connection while the lease/consume transaction is in progress. Successful return exposes committed lease and consumption state.
- Replay and terminal/retry-transition tests retain the spent consumption marker. Repository updates reject direct `consumed_at` mutation.
- Lease tests cover owner and attempt mismatches, cancelled-run renewal, valid-lease interruption rejection, stale completions, and released-lease recovery.
- Concurrent re-gating produces exactly one valid pending approval. Tests cover pre/post-sweep expiry, stale-pending replacement, the partial index, unrelated integrity failures, and rollback before winner re-read.
- Both injected busy errors and an actual held SQLite write lock exercise bounded retries.
- Failure-stop tests cover each settlement source state, in-flight completion, pending-approval expiry, persisted failure precedence, and concurrent approval resolution.
- An independent reader observes the original waiting state during both normalization statements. An injected second-statement failure rolls back the first statement and its audit.
- Transition tests cover all 64 source/target combinations. The execution repository exposes individual-step reads and lifecycle helpers. Audit writes use the existing append-only repository.

## Implementation clarifications and deviations

1. SQLite conflict identification uses `sqlalchemy.exc.IntegrityError.orig`, the supported `sqlite_errorcode`, and the exact indexed-column diagnostic for `approval_requests.workflow_step_run_id`. Other unique, CHECK, and foreign-key errors propagate through the rollback boundary.
2. Failure-stop classification parses the structured JSON `category`. A diagnostic message containing the word `skipped` still counts as a real failure. Missing or malformed failure metadata is classified conservatively. Indeterminate terminal failures engage failure-stop in accordance with REQ-E2.6 and design §5.5.
3. Three total busy attempts use delays of 10 ms and 20 ms before the second and third attempts. Exhaustion raises `DatabaseBusyError`. This follows the explicit maximum-three-attempt bound. A fourth attempt after a 40 ms delay would exceed that bound.
4. The explicit `immediate_transaction` context owns a new transaction or composes helpers through `commit=False`. It rejects unrelated active transactions without committing or discarding their work. A caught nested failure marks the outer unit of work for rollback. Run derivation can join this context so future approval-service composition can remain atomic.
5. SQLAlchemy schema comparison exposed incomplete reflection of an inline SQLite ALTER-added reference. The migration therefore uses an Alembic batch rebuild for the approval FK, carries the legacy CHECK explicitly, and restores the sequence high-water mark.
6. Canonicalization ignores outer JSON formatting through parsing and preserves string contents, including whitespace inside JSON string values. Numeric types and Unicode content contribute to the digest.
7. A succeeded predecessor with a still-pending successor derives `running`, following REQ-E4.4's nonterminal-run rule. This fills the mixed-state case omitted from the abbreviated precedence table.
8. The persisted column is `inputs_json`, following the approved database design.

These clarifications are recorded for review in this report. The specification files retain v2.1.4.

## Changed files

Created files are

- `alembic/versions/008_add_workflow_execution_engine.py`
- `src/devflow_api/models/workflow_step_run.py`
- `src/devflow_api/repositories/_workflow_state.py`
- `src/devflow_api/repositories/_workflow_transaction.py`
- `src/devflow_api/repositories/workflow_status.py`
- `src/devflow_api/repositories/workflow_step_run.py`
- `src/devflow_api/schemas/workflow_template.py`
- `tests/integration/test_workflow_execution_migration.py`
- `tests/unit/test_workflow_execution_repositories.py`
- `tests/unit/test_workflow_template.py`
- `docs/reports/phase2-wave1-group-a-implementation-report.md`

Modified files are

- `.env.example`
- `src/devflow_api/models/__init__.py`
- `src/devflow_api/models/approval.py`
- `src/devflow_api/models/workflow.py`
- `src/devflow_api/repositories/approval.py`
- `src/devflow_api/repositories/workflow.py`
- `src/devflow_api/schemas/approval.py`
- `src/devflow_core/config.py`
- `src/devflow_core/exceptions.py`
- `tests/integration/test_database.py`
- `tests/unit/test_github_confirmation_repository.py`

## Review boundary and remaining work

The remaining task groups cover executor contracts, orchestration, approval-service integration, recovery startup, API/UI wiring, and full Wave 1 acceptance. Group A supplies their persistence foundation. Approval expiry HTTP responses and automatic replacement after a failed protected dispatch require that later service integration.

Snapshot immutability is enforced through the execution repository's update contract and independence from the live workflow definition. Direct database administration remains within the local operator trust boundary.

The preserved blocker report has SHA-256 `EBABB2573E3B27F658ECCEB605CA0322A0B260119DC8177BAF6B486B14115724`. The existing `/docs/` ignore rule applies to this new local report.

Group A is ready for review.
