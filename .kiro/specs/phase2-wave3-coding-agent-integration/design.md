# DevFlow AI — Phase 2 Wave 3: Kiro Guided Handoff and Codex CLI Integration Design

**Version:** 1.0.2
**Status:** Frozen — v1.0.2 targeted integration corrections applied
**Scope:** Phase 2 Wave 3 — Kiro Guided Handoff and Codex CLI Integration
**Branch:** `feature/phase2-wave3-coding-agent-integration`
**Base commit:** `ee57dc7` (Wave 2 merged, develop HEAD)
**Last Updated:** 2026-11-23
**Companion:** `requirements.md` (v1.0.2), `tasks.md` (v1.0.2).

---

## 1. Design Principles

Wave 3 extends Wave 1 and Wave 2 additively. All existing contracts are preserved.

---

## Version History

- v1.0.0 — 2026-11-23 — Initial Wave 3 design.
- v1.0.1 — 2026-11-23 — ChatGPT review corrections F01–F10 applied.
- v1.0.2 — 2026-11-23 — Targeted integration corrections: (C1) execution mode enum reconciled `manual|mock|real_when_isolated`; `blocked` removed from all typed contracts and DB constraints. (C2) immutable plan persistence: `baseline_commit` and `deadline_at` added to `execution_plans`; workflow FKs made NOT NULL; crash-safe first transition documented. (C3) durable waiting contract source-verified against actual `StepExecutionResult` (`outcome="indeterminate"`); `WAITING_APPROVAL` confirmed; full durable-wait specification added to §3.2.1. (C4) git metadata path validation subsection §6.3 added with boundary checks, bound reads, no config includes, no alternates, `UNSUPPORTED_REPO_LAYOUT` for submodules/worktrees. (C5) Kiro approval ordering enforcement at `SpecificationApprovalService` §8.5 added. (C6) artifact ownership: `execution_plan_id` FK added to `handoff_artifacts`; content immutability, atomic publication, crash-safe recovery documented. (C7) version bumped to v1.0.2.

---

```
Streamlit UI → APIClient (httpx) → FastAPI routers → Domain services → Repositories → SQLite
                                                   → WorkflowOrchestrator (Wave 1, reused)
                                                   → KiroHandoffExecutor (new, Wave 3)
                                                   → CodexCLIExecutor (new, Wave 3)
                                                   → IsolationGate (new, Wave 3)
                                                   → WorkspaceValidator (new, Wave 3)
                                                   → ReportValidator (new, Wave 3)
                                                   → ExecutionPlanService (new, Wave 3)
                                                   → CodingAgentAuthorizationService (new, Wave 3)
```

Ten rules govern Wave 3:

1. **Phase 0/1/Wave 1/Wave 2 contracts are immutable.** No `CHECK` constraint, enum, service signature, or test is changed.
2. **Real coding-agent execution is blocked throughout Wave 3.** `IsolationGate` is fail-closed; no config flag bypasses it.
3. **All imported report content is untrusted data.** Fields are never instructions. Sanitization is defense-in-depth.
4. **Implementation authorization reuses Wave 1 `dispatch_protected_txn`.** No new approval flow is introduced.
5. **No automatic GitHub operations.** Wave 3 introduces no git write, commit, push, merge, or deploy path.
6. **No automatic cloud fallback.** Execution failures stay failed.
7. **Workspace validation is explicit.** Approved roots are operator-configured.
8. **Mock execution produces clearly labelled synthetic output.** It never claims real implementation.
9. **Offline credential scanning preserved.**
10. **Backward compatibility.** Phase 0/1/Wave 1/Wave 2 audit events work unchanged after migration 010.

---

## 2. Architecture Overview

```
  Developer Machine — 127.0.0.1 only
  ┌────────────────────────────────────────────────────────────────────────────────────────┐
  │  Browser :8501                                      CLI / Postman :8000                │
  │  ┌────────────────────────────────────┐    ┌─────────────────────────────────────────┐ │
  │  │ devflow-ui (Streamlit)              │    │ devflow-api (FastAPI/Uvicorn)            │ │
  │  │  Mode Selection                     │───▶│ Routers: /api/v1/coding-agent/...       │ │
  │  │  Kiro Handoff Screen                │    │ Services:                               │ │
  │  │  Kiro Prompt Export/Retrieve        │    │   ExecutionPlanService (NEW)            │ │
  │  │  Verification Findings              │    │   CodingAgentAuthorizationService (NEW) │ │
  │  │  Spec Approval (Wave 2)             │    │   KiroHandoffExecutor (NEW)             │ │
  │  │  Impl Authorization                 │    │   CodexCLIExecutor (NEW)               │ │
  │  │  Manual/Mock Prompt Export          │    │   IsolationGate (NEW)                  │ │
  │  │  Impl Report Upload                 │    │   WorkspaceValidator (NEW)             │ │
  │  │  Artifact Preview                   │    │   ReportValidator (NEW)                │ │
  │  │  Audit Timeline                     │    │   ApprovalService (Wave 1, reused)     │ │
  │  └────────────────────────────────────┘    │   WorkflowOrchestrator (Wave 1)        │ │
  │  Streamlit uses API layer only;             │   ExecutorRegistry (Wave 1, extended)  │ │
  │  no direct DB access from UI.              └──────────────────┬──────────────────────┘ │
  │                                                      ┌─────────▼──────────┐             │
  │                                                      │ devflow.db (SQLite) │             │
  │                                                      │ [all Wave 1/2 tables]│            │
  │                                                      │ + handoff_records   │             │
  │                                                      │ + handoff_artifacts │             │
  │                                                      │ + handoff_findings  │             │
  │                                                      │ + execution_plans   │             │
  │                                                      │ + execution_attempts│             │
  │                                                      │ + capability_records│             │
  │                                                      └────────────────────┘             │
  │  External: Codex CLI binary (blocked in Wave 3; contract prepared for Wave 6)           │
  └────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. KiroHandoffExecutor

**Location:** `src/devflow_api/executors/kiro_handoff.py`

Implements the Wave 1 `Executor` Protocol.

### 3.1 Handoff State Machine

```
handoff_created
    │  (prompt exported; prompt content stored immutably)
    ▼
awaiting_report  ──────────────────────────────────────► expired
    │  (user submits report; raw content stored)         (TTL exceeded; recovery service)
    ▼
report_submitted
    │  (async validation begins)
    ├─────────────────────────────────────────────────► failed
    ▼  (all validation checks pass)                     (schema/digest/finding error)
report_validated
    │  (optional: findings require correction)
    └─────────────────────────────────────────────────► correction_requested (terminal)
                                                        (loop back to Wave 2 spec revision)

[Cancellable from any non-terminal state → cancelled]

Terminal states: report_validated, failed, expired, cancelled, correction_requested
```

**Key design decision:** `report_validated` is the terminal success state for the handoff. It is NOT renamed to `validated` to avoid confusion with Wave 2 terminology. The design.md §9.2 reconciliation table documents all state name mappings.

### 3.2 Non-Running Human-Wait State

The `awaiting_report` state is a **non-running human-wait state**. When a handoff enters `awaiting_report`:
- No executor coroutine is held in memory.
- No WorkflowStepRun lease is held.
- No background task is active.
- The durable state is recorded only in `handoff_records.status` and `awaiting_since`.

Resumption occurs on the next user action (report submission or cancellation), which is handled as a fresh HTTP request that reads the persisted handoff state.

### 3.2.1 Durable Waiting Contract (Source-Verified)

**Source verification of `StepExecutionResult`:** The actual `StepExecutionResult` schema (from `src/devflow_api/schemas/executor.py`) defines:

```python
class StepExecutionResult(ExecutorModel):
    outcome: Literal["succeeded", "failed", "cancelled", "indeterminate"]
    ...
```

**`outcome="paused"` does NOT exist** in the actual Wave 1 contract. The design proposed this value in v1.0.0–v1.0.1, which was incorrect. The backward-compatible extension for durable human-wait states is:

- `KiroHandoffExecutor.execute()` returns `outcome="indeterminate"` (the existing marker for "outcome not yet determined; not a final failure") when it enters a human-wait state. The `error.code` is set to `WAITING_FOR_HUMAN_INPUT` with `retryable=False` (indeterminate outcomes cannot be retryable per the validator) and `category="internal"`.
- The `WAITING_APPROVAL` step state already exists in Wave 1 (`WorkflowStepRun.status`). The orchestrator maps `indeterminate` from a human-wait executor to `WAITING_APPROVAL` using the `error.code` discriminator.
- No new `StepExecutionResult.outcome` variant is added. No new `WorkflowStepRun` status is added.

**Wait reason discrimination:**
- `error.code = "WAITING_FOR_KIRO_REPORT"` — handoff in `awaiting_report` state.
- `error.code = "WAITING_FOR_IMPL_AUTHORIZATION"` — execution attempt in `awaiting_impl_authorization` state.

These two wait states are always distinguishable by reading `handoff_records.status` and `execution_attempts.status`; no logic can confuse them.

**Ownership:** The wait token is owned by the `handoff_records` row (for Kiro waits) or the `execution_attempts` row (for impl-auth waits). The `WorkflowStepRun` holds only the step-level `WAITING_APPROVAL` state; the sub-state detail is in the domain record.

**Atomic transition into wait state:**
- Entry into `WAITING_APPROVAL` via `request_bound_approval(run_id, step_run_id)` is atomic: transitions step `READY → WAITING_APPROVAL` within a `BEGIN IMMEDIATE` transaction (confirmed from `ApprovalService.request_bound_approval` source: `steps.transition_cas(step_run_id, "WAITING_APPROVAL", from_status="READY")`).

**Lease release:**
- The orchestrator MUST release the step lease before the step enters `WAITING_APPROVAL`. The `KiroHandoffExecutor.execute()` method returns before the user-wait begins; it does not hold an active coroutine.

**Validated report submission:**
- Report submission atomically transitions the attempt from `awaiting_report → report_submitted` using `BEGIN IMMEDIATE` CAS. Duplicate submissions (attempt already in `report_submitted`) are rejected with `HANDOFF_NOT_AWAITING_REPORT`.

**Idempotent resumption:**
- Resumption after restart re-reads attempt status from the database. An already-resumed attempt (status ≠ `awaiting_report` or `awaiting_impl_report`) is not re-dispatched. The recovery service leaves durable wait states untouched.

**Cancellation and expiry:**
- `awaiting_report` → `expired` via TTL check in `CodingAgentRecoveryService` (emits audit event).
- `awaiting_report` → `cancelled` via explicit user action (emits audit event).
- `awaiting_impl_report` → `cancelled` via explicit user action; authorization record reflects cancellation (not consumed).
- Both transitions are CAS on current state within `BEGIN IMMEDIATE` transaction.

**Recovery after restart:**
- Interrupted `awaiting_report` and `awaiting_impl_authorization` attempts are detected on startup by `CodingAgentRecoveryService.recover()`.
- Their status is preserved (not reset to `draft`). No automatic dispatch occurs.
- Only `dispatching` and `mock_execution` states are transitioned to `interrupted` on restart.

**No duplicate approval consumption:**
- CAS on `consumed_at IS NULL` (via Wave 1 `consume_atomic`) prevents double-consumption. A second `dispatch_protected_txn` for the same step returns `ApprovalNotConsumableError`.

**No unintended automatic dispatch:**
- `real_when_isolated` NEVER auto-dispatches even after isolation prerequisites might theoretically become available. The user must explicitly request a new execution attempt.

This is compatible with the Wave 1 engine: the existing `WAITING_APPROVAL` step state supports the KiroHandoffExecutor durable wait pattern without any new step states.

### 3.3 Prompt Template Structure

The Kiro verification prompt is a structured text artifact:

```
=== KIRO VERIFICATION PROMPT ===
Schema Version: 1.0
Handoff ID: {handoff_id}
Issued At: {issued_at}        ← stored at export time; never regenerated on re-read
Specification Request ID: {specification_request_id}
Specification Version ID: {specification_version_id}
Artifact Digest: {artifact_digest}
Selected Tasks: {task_ids_comma_separated}

=== REQUESTED VERIFICATION CRITERIA ===
1. All stated requirements have clear, testable acceptance criteria.
2. Design is consistent with requirements; no undeclared dependencies.
3. Task list covers all requirements; every task maps to at least one requirement.
4. No security vulnerabilities are introduced by the specification.
5. All ADRs have decisions recorded; unresolved decisions are flagged.

=== SPECIFICATION ARTIFACTS ===
--- requirements.md ---
{requirements_content}
--- design.md ---
{design_content}
--- tasks.md ---
{tasks_content}

=== VERIFICATION REPORT FORMAT ===
Submit a JSON report conforming to the KiroVerificationReport schema (see design.md §6).
=================================
```

**Immutability:** The full prompt content is stored in `handoff_artifacts` at export time with `artifact_type = "kiro_prompt"`. Subsequent retrievals return the stored content without regenerating the `issued_at` timestamp.

### 3.4 Digest Binding

`artifact_digest = SHA-256(requirements_text || design_text || tasks_text)` where `||` denotes byte concatenation of the UTF-8 encoded approved artifact texts. This matches the Wave 2 `SpecificationVersionRepository.text_hash()` approach (SHA-256 of content bytes).

### 3.5 Expiry

`KIRO_HANDOFF_EXPIRY_SECONDS` (default 86400) is a new `Settings` field. The `CodingAgentRecoveryService` checks handoffs in `awaiting_report` where `awaiting_since + KIRO_HANDOFF_EXPIRY_SECONDS < now()`.

---

## 4. CodexCLIExecutor

**Location:** `src/devflow_api/executors/codex_cli.py`

Implements the Wave 1 `Executor` Protocol.

### 4.1 Execution Modes and Outcomes

> **Execution mode vs. outcome distinction:** The requested execution mode is `manual | mock | real_when_isolated`. These are the only values accepted in `execution_mode` input fields, database constraints, and API schemas. `real_execution_blocked` is an **outcome state**, not a requested mode. It arises when `real_when_isolated` is requested but isolation prerequisites are unmet at runtime. `blocked` is never a valid requested mode.
>
> **Manual mode:** MUST NOT launch a coding-agent subprocess. No process, shell command, or external binary is invoked at any point.
>
> **Real automated execution:** Unconditionally blocked in Wave 3. All `real_when_isolated` requests result in `real_execution_blocked` outcome.

| Requested Mode | Condition | Outcome State |
|---|---|---|
| `mock` | Always | `mock_execution` → `completed` |
| `real_when_isolated` | Gate not met (Wave 3: always) | `real_execution_blocked` |
| `real_when_isolated` | Gate met (Wave 6 only) | `executing` → `completed`/`failed` |
| `manual` | Always | `manual_handoff` → `awaiting_impl_report` |

`real_execution_blocked` is an **outcome state**, not a requested mode.

### 4.2 CodexExecutionInput Schema

```python
class CodexExecutionInput(ExecutorModel):
    specification_request_id: int
    specification_version_id: int
    artifact_digest: str
    kiro_handoff_id: int               # validated handoff record ID
    task_ids: list[str]                # non-empty; no wildcard "*"
    repo_identity: str
    workspace_identity: str
    execution_mode: Literal["manual", "mock", "real_when_isolated"]   # canonical 3-value enum; "blocked" is an outcome, not a mode
    deadline_at: datetime              # UTC; must be future
    baseline_commit: str | None        # set by WorkspaceValidator
    # canonical_execution_input_hash is computed server-side; never from client
```

### 4.3 Mock Mode

No subprocess, no filesystem access outside approved workspace, no network calls. Produces a deterministic synthetic `CodexImplementationReport` with `execution_mode = "mock"` and `verdict = "PASS_WITH_WARNINGS"`. Every field in the synthetic report is labelled as synthetic. All task results have `status = "completed"` with `notes = "[SYNTHETIC: mock execution]"`. Audit events include `"execution_mode": "mock"` and `"synthetic": true`.

### 4.4 Manual Mode

No subprocess, no filesystem access, no network calls. Generates an immutable implementation prompt, stores it in the execution attempt record, transitions to `awaiting_impl_report`, and waits for the user to import a report. Authorization is consumed at first prompt export via `dispatch_protected_txn`.

### 4.5 Real-Execution-Blocked (Wave 3 outcome for `real_when_isolated`)

`IsolationGate.check()` is called first. If not `prerequisites_met`, outcome state is `real_execution_blocked`. No authorization consumed. No subprocess launched. Error code: `ISOLATION_PREREQUISITES_NOT_MET`.

### 4.6 Abstract Real Execution Contract (Wave 6 reference)

```
Invocation (abstract; flags unverified against installed CLI):
  codex <approval-mode-flag> <cwd-flag> {workspace} [model-flag] {model} {task_prompt}

Note: Exact flag names MUST be verified against the installed CLI version at Wave 6.
The above is an abstract representation only.

Output collection:
  - stdout captured line-by-line; credential patterns redacted before storage
  - exit code 0 = success; non-zero = failure
  - git diff computed after execution for changed_files
  - all output validated against CodexImplementationReport schema before acceptance
```

### 4.7 CodexImplementationReport Schema

```python
class TaskResult(BaseModel):
    task_id: str
    status: Literal["completed", "failed", "skipped", "partial"]
    notes: str | None                  # max 2048 chars; sanitized

class TestSummary(BaseModel):
    total: int
    passed: int
    failed: int
    skipped: int
    coverage_percent: float | None     # must be in [0.0, 100.0]

class CodexImplementationReport(BaseModel):
    schema_version: Literal["1.0"]
    execution_attempt_id: str          # string representation of integer ID
    execution_plan_id: int
    specification_version_id: int
    artifact_digest: str
    authorized_task_ids: list[str]     # from execution plan; must match exactly
    repo_identity: str
    baseline_commit: str
    execution_mode: Literal["mock", "manual", "real"]
    verdict: Literal["PASS", "PASS_WITH_WARNINGS", "FAIL", "INCOMPLETE"]
    task_results: list[TaskResult]     # all authorized_task_ids must be present; no duplicates
    changed_files: list[str]           # max 1000
    test_summary: TestSummary | None
    artifact_refs: list[str]           # max 100
    completed_at: str                  # ISO 8601 UTC
```

---

## 5. IsolationGate

**Location:** `src/devflow_api/services/isolation_gate.py`

### 5.1 Fail-Closed Design

```python
class IsolationGateResult(BaseModel):
    status: Literal["not_configured", "prerequisites_not_met", "prerequisites_met"]
    unmet_prerequisites: list[str]
    checked_at: datetime

class IsolationGate:
    def __init__(self, settings: Settings) -> None:
        self._prerequisites = settings.ISOLATION_PREREQUISITES  # list[str], default []

    async def check(self) -> IsolationGateResult:
        if not self._prerequisites:
            return IsolationGateResult(
                status="not_configured",
                unmet_prerequisites=[],
                checked_at=datetime.now(UTC),
            )
        recognized = {"os_job_objects_enabled", "linux_namespaces_enabled",
                      "seccomp_enabled", "filesystem_allowlist_active", "network_egress_blocked"}
        unmet = []
        for name in self._prerequisites:
            if name not in recognized:
                unmet.append(name)  # unrecognized → not met (fail-closed)
                continue
            if not await self._check_prerequisite(name):
                unmet.append(name)
        if unmet:
            return IsolationGateResult(
                status="prerequisites_not_met", unmet_prerequisites=unmet,
                checked_at=datetime.now(UTC),
            )
        return IsolationGateResult(
            status="prerequisites_met", unmet_prerequisites=[],
            checked_at=datetime.now(UTC),
        )
```

No boolean configuration flag creates a code path returning `prerequisites_met`. In Wave 3, `_check_prerequisite` always returns `False` for all recognized names (Wave 6 provides real implementations).

### 5.2 Gate Check Sequence

```
real_when_isolated request received
        │
        ▼
  IsolationGate.check()
        │
        ├── not_configured / prerequisites_not_met
        │       │
        │       ▼
        │   outcome: real_execution_blocked
        │   error: ISOLATION_PREREQUISITES_NOT_MET
        │   (no authorization consumed, no subprocess, no workspace access)
        │
        └── prerequisites_met (Wave 6 only)
                │
                ▼
          WorkspaceValidator.validate()
                │
                ▼
          CodingAgentAuthorizationService.consume_via_dispatch()
                │
                ▼
          [real execution — Wave 6 only]
```

---

## 6. WorkspaceValidator

**Location:** `src/devflow_api/services/workspace_validator.py`

### 6.1 Corrected Algorithm

The corrected algorithm inspects intermediate path components for symlinks **before** calling `Path.resolve()`. Calling `Path.resolve()` first and then checking `is_symlink()` is incorrect because `resolve()` follows symlinks; `is_symlink()` on the resolved path always returns `False`.

```python
import os
import sys
from pathlib import Path, PurePosixPath

class WorkspaceValidator:
    def __init__(self, settings: Settings) -> None:
        self._approved_roots: list[Path] = []
        for r in settings.APPROVED_WORKSPACE_ROOTS:
            root = Path(r)
            if not root.is_absolute():
                raise ValueError(f"Approved workspace root must be absolute: {r}")
            self._approved_roots.append(root)
        self._follow_symlinks: bool = settings.WORKSPACE_FOLLOW_SYMLINKS
        self._is_windows: bool = sys.platform == "win32"

    def validate(self, raw_path: str) -> Path:
        # 1. Reject UNC and device paths on Windows
        if self._is_windows:
            if raw_path.startswith("\\\\") or raw_path.startswith("//"):
                raise WorkspaceError("INVALID_PATH_FORMAT", "UNC paths not permitted")
            # Normalize Windows backslash to forward slash for further processing
            normalized = raw_path.replace("\\", "/")
        else:
            normalized = raw_path  # do NOT normalize on POSIX
        # 2. Build Path object and check it is absolute
        candidate = Path(normalized)
        if not candidate.is_absolute():
            raise WorkspaceError("RELATIVE_PATH_REJECTED", sanitize(raw_path))
        # 3. Inspect intermediate components for symlinks BEFORE resolve()
        self._check_intermediate_components(candidate)
        # 4. Now safe to resolve (eliminates .., but we've pre-checked symlinks)
        resolved = candidate.resolve()
        # 5. Verify path exists and is a directory
        if not resolved.exists():
            raise WorkspaceError("PATH_DOES_NOT_EXIST", sanitize(raw_path))
        if not resolved.is_dir():
            raise WorkspaceError("PATH_NOT_A_DIRECTORY", sanitize(raw_path))
        # 6. Check containment using is_relative_to(), not string prefix
        for root in self._approved_roots:
            root_resolved = root.resolve()
            if self._is_relative_to(resolved, root_resolved):
                return resolved
        raise WorkspaceError("PATH_NOT_IN_APPROVED_WORKSPACE", sanitize(raw_path))

    def _check_intermediate_components(self, path: Path) -> None:
        """Check each path component for symlinks/junctions before resolve()."""
        parts = path.parts
        accumulated = Path(parts[0])
        for part in parts[1:]:
            accumulated = accumulated / part
            if not accumulated.exists():
                break  # path may not exist yet; resolve() will catch this
            if accumulated.is_symlink():
                if not self._follow_symlinks:
                    raise WorkspaceError("SYMLINKS_DISALLOWED", sanitize(str(path)))
                # When following symlinks, verify the symlink target is within approved roots
                target = accumulated.resolve()
                in_approved = any(
                    self._is_relative_to(target, r.resolve())
                    for r in self._approved_roots
                )
                if not in_approved:
                    raise WorkspaceError("PATH_TRAVERSAL_DETECTED", sanitize(str(path)))

    @staticmethod
    def _is_relative_to(child: Path, parent: Path) -> bool:
        """Safe path containment check using is_relative_to() (Python 3.9+)."""
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False
```

**TOCTOU risk (documented residual):** Even with intermediate component inspection, a symlink could be introduced between the check and use. This is a known, documented residual risk of application-level path checking. Real execution safety requires OS-level isolation (Wave 6). Wave 3 workspace checks are defense-in-depth, not a security guarantee.

**Case sensitivity:** On Windows, path containment comparisons are made case-insensitively by using `.resolve()` (which the OS resolves to canonical case) rather than string prefix matching.

### 6.2 Git Identity Verification (Without Subprocess)

```python
def verify_git_identity(workspace: Path, expected_repo_identity: str) -> str:
    """Returns baseline commit SHA. Raises WorkspaceError on mismatch."""
    git_path = workspace / ".git"
    if not git_path.exists():
        raise WorkspaceError("NOT_A_GIT_REPOSITORY")
    # Handle .git file (worktree or submodule)
    if git_path.is_file():
        content = git_path.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir:"):
            raise WorkspaceError("UNSUPPORTED_GIT_LAYOUT")
        # Worktrees are supported; resolve the gitdir path
        git_dir = Path(content[len("gitdir:"):].strip())
        if not git_dir.is_absolute():
            git_dir = (workspace / git_dir).resolve()
        if not git_dir.is_dir():
            raise WorkspaceError("NOT_A_GIT_REPOSITORY")
    else:
        git_dir = git_path
    # Read remote origin URL from .git/config (file parsing, no subprocess)
    config_path = git_dir / "config"
    if not config_path.exists():
        raise WorkspaceError("GIT_CONFIG_NOT_FOUND")
    remote_url = _parse_git_config_remote_origin(config_path.read_text(encoding="utf-8"))
    if remote_url is None or not _repo_identity_matches(remote_url, expected_repo_identity):
        raise WorkspaceError("REPOSITORY_IDENTITY_MISMATCH")
    # Read HEAD commit without subprocess
    baseline_commit = _read_head_sha(git_dir)
    return baseline_commit
```

### 6.3 Git Metadata Path Validation

All git metadata paths are validated against the approved workspace root before reading. This subsection defines the mandatory validation rules applied by `WorkspaceValidator.verify_git_identity()`.

**Paths subject to validation:** `.git` dir, `.git` indirection files, `gitdir:` targets, `commondir` references, `config`, `HEAD`, ref files (`refs/heads/*`, `refs/tags/*`), `packed-refs`.

**Validation rules (all mandatory):**

1. **Boundary enforcement:** Every resolved git metadata path MUST be inside an approved workspace root (same `is_relative_to()` check as the workspace path itself). Paths outside approved roots raise `WorkspaceError("GIT_METADATA_PATH_OUTSIDE_ROOT")`.

2. **Symlink rejection:** Git metadata paths are subject to the same symlink policy as workspace paths (§6.1 and REQ-W3.5.2). Symbolic links, Windows junctions, and reparse points in git metadata paths are rejected or validated per `WORKSPACE_FOLLOW_SYMLINKS`.

3. **Bounded reads:**
   - `HEAD`, `gitdir`, `commondir` files: max **4 KB**; exceeding this raises `WorkspaceError("GIT_METADATA_TOO_LARGE")`.
   - `config`: max **64 KB**; exceeding this raises `WorkspaceError("GIT_METADATA_TOO_LARGE")`.
   - `packed-refs`: max **1 MB**; exceeding this raises `WorkspaceError("GIT_METADATA_TOO_LARGE")`.

4. **No config includes:** Git `config` files containing `[include]` or `[includeIf]` sections are rejected with `WorkspaceError("GIT_CONFIG_INCLUDES_UNSUPPORTED")`. Config includes are never followed.

5. **No external object alternates:** If `objects/info/alternates` exists, the repository is rejected with `WorkspaceError("GIT_ALTERNATES_UNSUPPORTED")`. External object alternates are never followed.

6. **Standard layout requirement:** `.git` MUST be a directory for standard (non-worktree) repos. `.git` as a file (worktree/submodule gitdir indirection) is supported ONLY if the `gitdir:` target resolves within an approved workspace root; otherwise return `WorkspaceError("UNSUPPORTED_REPO_LAYOUT")`.

7. **Submodule deferral:** Repositories containing a `.gitmodules` file at the root are rejected with `WorkspaceError("UNSUPPORTED_REPO_LAYOUT")`. Submodule workspace validation is deferred to a future wave.

8. **TOCTOU residual limitation:** Validation and read are not atomic at the application level. A file could be replaced between the validation check and the read. This is a known, documented residual risk. Mitigation is defense-in-depth: Wave 6 OS-level sandboxing (operator-controlled workspace roots, sandboxed subprocess boundary) provides real mitigation; Wave 3 path checking is defense-in-depth only.

Supported git layouts: standard `.git` directory, `.git` file for worktrees with target inside approved root. `.git` files with non-`gitdir:` content are rejected. External git objects (alternates) are not followed.

---

## 7. ReportValidator

**Location:** `src/devflow_api/services/report_validator.py`

### 7.1 KiroVerificationReport Validation Pipeline

```
Step  Check                                          Failure code
 1    JSON parse                                     SCHEMA_VALIDATION_ERROR
 2    Pydantic KiroVerificationReport schema          SCHEMA_VALIDATION_ERROR
 3    schema_version == "1.0"                        UNSUPPORTED_SCHEMA_VERSION
 4    handoff_id matches handoff record (int→str)    HANDOFF_ID_MISMATCH
 5    specification_request_id matches               SPEC_REQUEST_MISMATCH
 6    specification_version_id matches               SPEC_VERSION_MISMATCH
 7    artifact_digest matches handoff record         DIGEST_MISMATCH
 8    finding category validation (enum values)      INVALID_FINDING_CATEGORY
 9    finding ID uniqueness (no duplicates)          DUPLICATE_FINDING_IDS
10    reviewed_at is valid ISO 8601 UTC ≤ now()      INVALID_TIMESTAMP
11    report total size ≤ REPORT_MAX_BYTES           REPORT_TOO_LARGE
12    string field sanitization (strip ctrl chars;   (no failure; sanitize in-place)
      cap at REPORT_FIELD_MAX_BYTES)
13    prompt injection pattern detection             (audit-log; no failure on detection alone)
14    Record DevFlow verification result independently (not from report's self-asserted verdict)
```

All 14 steps are deterministic. Steps 1–11 are hard gates (failure stops validation). Steps 12–13 are sanitization and logging (no stop). Step 14 records `devflow_verification_passed = True` only if steps 1–11 all pass.

### 7.2 CodexImplementationReport Validation Pipeline

```
Step  Check                                          Failure code
 1    JSON parse                                     SCHEMA_VALIDATION_ERROR
 2    Pydantic CodexImplementationReport schema      SCHEMA_VALIDATION_ERROR
 3    schema_version == "1.0"                        UNSUPPORTED_SCHEMA_VERSION
 4    execution_attempt_id matches attempt           ATTEMPT_ID_MISMATCH
 5    execution_plan_id matches plan                 PLAN_ID_MISMATCH
 6    specification_version_id matches               SPEC_VERSION_MISMATCH
 7    artifact_digest matches plan                   DIGEST_MISMATCH
 8    authorized_task_ids == plan.task_ids           TASK_ID_SET_MISMATCH
 9    execution_mode matches attempt mode            EXECUTION_MODE_MISMATCH
10    task_results: all authorized tasks present     MISSING_TASK_RESULTS
11    task_results: no duplicate task IDs            DUPLICATE_TASK_RESULTS
12    task_results: no unauthorized task IDs        UNAUTHORIZED_TASK_IN_REPORT
13    contradictory verdict (failed task + PASS)     CONTRADICTORY_VERDICT
14    test_summary consistency                       INCONSISTENT_TEST_COUNTS
15    coverage_percent in [0.0, 100.0]              INVALID_COVERAGE_RANGE
16    report already submitted (replay check)        REPORT_ALREADY_SUBMITTED
17    string field sanitization                      (sanitize in-place)
18    prompt injection detection                     (audit-log)
19    Record DevFlow verification result independently
```

### 7.3 Untrusted Data Handling

Report content is treated as adversarial input:
- No field passed to `eval()`, `exec()`, `subprocess`, `os.system()`, or any equivalent.
- No field passed to an AI provider prompt without hard `=== BEGIN UNTRUSTED CONTENT ===` / `=== END UNTRUSTED CONTENT ===` delimiters.
- JSON parsed with `json.loads()` only (no custom object hooks).
- String fields sanitized: strip `\x00–\x08`, `\x0b–\x0c`, `\x0e–\x1f`; truncate to `REPORT_FIELD_MAX_BYTES`.

### 7.4 ID Reconciliation (Integer DB IDs vs String Report Fields)

Report schemas use `str` for IDs to allow flexibility in report generation tooling. Validation converts the string to `int` for comparison against the integer database ID. A non-numeric string in an integer-expected field returns `SCHEMA_VALIDATION_ERROR`.

---

## 8. Implementation Authorization via Wave 1

Wave 3 reuses the Wave 1 approval and dispatch infrastructure **exactly as it exists in source**.

### 8.1 Actual Wave 1 Interface (from source inspection)

```python
# ApprovalService.request_bound_approval(run_id, step_run_id)
# → reads step.inputs_json and step.input_digest from DB
# → operation = step definition's protected_operation (must be "implementation_authorization")
# → parameters_hash = canonical_hash(step.inputs_json) (computed from persisted inputs)
# → creates ApprovalRequest with operation, parameters_hash, actor_id, expires_at

# ApprovalRepository.consume_atomic(approval_id, operation, parameters_hash,
#                                    workflow_step_run_id, actor_id)
# → CAS: sets consumed_at atomically if status=approved, consumed_at IS NULL,
#         authorization_expires_at > now(), and all binding fields match

# WorkflowStepRunRepository.dispatch_protected_txn(step_id, lease_owner,
#     approval_id, operation, parameters_hash, actor_id, ...)
# → atomically: transitions step READY→RUNNING, then calls consume_atomic
# → raises ApprovalNotConsumableError if consume_atomic returns False
```

### 8.2 Wave 3 Integration Pattern

The Wave 3 implementation authorization gate follows the Wave 1 pattern for a step with `protected_operation = "implementation_authorization"`:

1. `ExecutionPlanService` populates the step's `inputs_json` with the full binding payload (see §8.3) before the step enters `READY` state.
2. The orchestrator calls `WorkflowOrchestrator._advance()`, which finds the step in `READY` state and calls `request_bound_approval(run_id, step_id)`.
3. The user approves via the existing `/api/v1/workflows/{run_id}/approvals/{id}/resolve` endpoint (Wave 1).
4. `ApprovalService._resolve_bound()` transitions step to `READY` and sets `authorization_expires_at`.
5. At dispatch time, `dispatch_protected_txn` atomically transitions step and consumes authorization.

### 8.3 Execution Binding Payload (Step inputs_json)

The Wave 3 implementation step's `inputs_json` contains the full binding payload whose `canonical_hash` becomes `parameters_hash`:

```json
{
  "specification_request_id": <int>,
  "specification_version_id": <int>,
  "artifact_digest": "<sha256>",
  "kiro_handoff_id": <int>,
  "task_ids": ["T1", "T2"],           // sorted
  "repo_identity": "<canonical url>",
  "workspace_identity": "<canonical path>",
  "execution_mode": "mock|manual|real_when_isolated",
  "deadline_at": "<iso8601 utc>",
  "baseline_commit": "<sha>|null"
}
```

This payload is **persisted in `workflow_step_runs.inputs_json`** before authorization is requested. The `canonical_hash` of this JSON is `parameters_hash`. Changing any field requires a new execution plan and step (the orchestrator creates a new run if needed), which produces a different `parameters_hash`, preventing stale-input acceptance.

### 8.5 Kiro Approval Ordering Enforcement at SpecificationApprovalService

For Wave 3 workflow-linked specification versions, `SpecificationApprovalService` MUST enforce the following prerequisite check before creating or resolving any specification approval record:

1. A completed `kiro_handoffs` row (or `handoff_records` row with `status = 'report_validated'`) MUST exist whose `spec_version_digest` matches the current `SpecificationVersion.content_digest`.
2. The handoff's `workflow_run_id` MUST match the current workflow run.

If either check fails, the approval request is rejected with `KIRO_VERIFICATION_REQUIRED`.

This check is enforced in the `_eligible()` method of `SpecificationApprovalService` (the same method that checks review status and digest integrity) for Wave 3 workflow-linked specifications. It is enforced **server-side regardless of UI state**.

**Stale evidence invalidation rule:** When a new specification version row is created (spec revision), the new row is created with `kiro_verification_id = NULL`. A validated Kiro handoff for the prior version is not valid evidence for the new version.

**Legacy Wave 2 compatibility:** The prerequisite check applies only when the specification is associated with a Wave 3 workflow run (`workflow_run_id IS NOT NULL` and the workflow uses `implementation_authorization` as a protected operation). Legacy Wave 2 standalone approvals (not linked to a Wave 3 workflow) are unaffected; their approval path uses the existing `SpecificationApprovalRepository.create()` path without the Kiro prerequisite gate.

**Replacing the global hash uniqueness constraint:** The v1.0.0 design had a `UNIQUE INDEX idx_ep_hash ON execution_plans(canonical_execution_input_hash)`, which would prevent legitimate retries across different workflow runs. This is replaced with:

- A unique constraint scoped to `(workflow_run_id, workflow_step_run_id)`: one active plan per step.
- The `canonical_execution_input_hash` is stored for stale-input detection but is NOT globally unique.
- Duplicate plan creation for the same step and same hash is idempotent (returns existing plan).
- Duplicate plan creation for the same step with a different hash returns `STALE_EXECUTION_INPUT` and requires a new authorization.

---

## 9. Execution Lifecycle State Machines

### 9.1 Handoff State Machine (handoff_records)

```
handoff_created ──(prompt exported)──► awaiting_report ──(TTL)──► expired
                                              │
                                    (user submits report)
                                              │
                                              ▼
                                       report_submitted
                                              │
                              ┌───────────────┼─────────────────┐
                   (validation fails)         │         (validation passes)
                              ▼               ▼                  ▼
                            failed     report_validated    report_validated
                                             │
                               (correction needed)
                                             ▼
                                   correction_requested (terminal)

[Any non-terminal state → cancelled on user request]
```

### 9.2 Execution Attempt State Machine (execution_attempts)

```
plan_prepared
    │  (authorization requested → waiting_approval in WorkflowStepRun)
    ▼
awaiting_impl_authorization
    │  (authorization granted → step READY)
    ▼
authorization_granted
    │  (dispatch begins → dispatch_protected_txn → step RUNNING)
    ▼
dispatching
    │
    ├──(execution_mode=mock)──────────────────► mock_execution ──► completed
    ├──(execution_mode=manual)───────────────► manual_handoff
    │                                              │
    │                                    (authorization consumed;
    │                                     prompt exported)
    │                                              ▼
    │                                      awaiting_impl_report
    │                                              │
    │                                   (user imports report)
    │                                              ▼
    │                                      impl_report_submitted
    │                                              │
    │                                      validating_report
    │                                              │
    │                                   (validation complete)
    │                                        ▼         ▼
    │                                   completed    failed
    │
    └──(execution_mode=real_when_isolated)──► real_execution_blocked
                                              (stays; no auto-retry)

[Any non-terminal state → cancelled; timeout if deadline passed]
[Restart: dispatching/mock_execution → interrupted]
```

### 9.3 State Name Reconciliation

| v1.0.0 name | v1.0.1 name | Reason |
|---|---|---|
| `report_validated` | `report_validated` (unchanged) | Clear; not confused with Wave 2 |
| `validated` (v1.0.0 also used) | `report_validated` only | Eliminate ambiguity |
| `real_execution_blocked` | `real_execution_blocked` (outcome state) | Not a mode; outcome of blocked real request |
| `executing` | `executing` (Wave 6 only; not in Wave 3 DBs) | Deferred |
| `awaiting_impl_report` | `awaiting_impl_report` (new, manual only) | Durable wait for user-submitted report |
| `impl_report_submitted` | `impl_report_submitted` (new) | Report received; validation pending |

### 9.4 Recovery Behavior

On application startup, `CodingAgentRecoveryService.recover()`:
1. Find attempts in `dispatching` or `mock_execution` → transition to `interrupted` (audit event).
2. Find handoffs in `awaiting_report` where TTL exceeded → transition to `expired` (audit event).
3. Leave `awaiting_impl_report`, `awaiting_impl_authorization`, `plan_prepared` unchanged (durable waits).
4. No execution auto-dispatched.

---

## 10. Data Model

### 10.1 New Tables (migration 010)

Migration: `010_add_coding_agent_integration` (down_revision = `009_add_ai_specification_generation`).

#### handoff_records

```sql
CREATE TABLE handoff_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    specification_request_id INTEGER NOT NULL
        REFERENCES specification_requests(id) ON DELETE CASCADE,
    specification_version_id INTEGER NOT NULL
        REFERENCES specification_versions(id),
    artifact_digest TEXT NOT NULL,
    task_ids_json TEXT NOT NULL,
    prompt_text_hash TEXT,
    actor_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'handoff_created'
        CHECK (status IN ('handoff_created','awaiting_report','report_submitted',
                          'report_validated','failed','expired','cancelled',
                          'correction_requested')),
    prompt_exported_at TEXT,
    awaiting_since TEXT,
    report_submitted_at TEXT,
    validated_at TEXT,
    failed_at TEXT,
    expired_at TEXT,
    cancelled_at TEXT,
    validation_error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_hr_spec_version ON handoff_records(specification_version_id);
CREATE INDEX idx_hr_status ON handoff_records(status);
CREATE INDEX idx_hr_awaiting_since ON handoff_records(awaiting_since);
CREATE UNIQUE INDEX uidx_hr_active_per_version ON handoff_records(specification_version_id)
    WHERE status NOT IN ('report_validated','failed','expired','cancelled','correction_requested');
```

#### handoff_artifacts

```sql
-- Owns Kiro prompts and Kiro reports (before execution attempt exists)
CREATE TABLE handoff_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id) ON DELETE CASCADE,
    execution_plan_id INTEGER REFERENCES execution_plans(id),  -- NOT NULL for codex_prompt artifacts; NULL for kiro_prompt/kiro_report
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('kiro_prompt','kiro_report','codex_prompt')),
    content_hash TEXT NOT NULL,          -- SHA-256 of content; required; never overwritten after publication
    content_size_bytes INTEGER NOT NULL,
    storage_key TEXT NOT NULL,          -- DevFlow-generated safe identifier; never client-supplied
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','prompt_publishing','published')),  -- for codex_prompt; kiro artifacts go directly to 'published'
    created_at TEXT NOT NULL
);
CREATE INDEX idx_ha_handoff ON handoff_artifacts(handoff_record_id);
CREATE INDEX idx_ha_plan ON handoff_artifacts(execution_plan_id);
CREATE INDEX idx_ha_hash ON handoff_artifacts(content_hash);
CREATE INDEX idx_ha_type ON handoff_artifacts(artifact_type);
```

> **Artifact ownership rules:**
>
> - Each exported Codex prompt artifact (`artifact_type = 'codex_prompt'`) belongs to exactly one `execution_plans` row via `execution_plan_id FK NOT NULL`. Lookup by `handoff_record_id` alone is prohibited for `codex_prompt` artifacts; lookup MUST include `execution_plan_id`.
> - A `handoff_records` row may be referenced by multiple independently authorized `execution_plans` rows, but each plan has its own `codex_prompt` artifact.
> - `kiro_prompt` and `kiro_report` artifacts belong to the handoff only (`execution_plan_id = NULL`).
>
> **Immutable content:**
> - Prompt content is stored immutably: once `status = 'published'`, the `content` and `content_hash` are never overwritten. Any re-read returns the stored content.
>
> **Atomic publication:**
> - Publication is atomic: a single `BEGIN IMMEDIATE` transaction sets `execution_plans.status = 'prompt_published'` and either inserts the artifact row (first export) or verifies the existing row matches (idempotent re-export).
>
> **Crash-safe recovery:**
> - On restart, any `execution_plans` row with `status = 'prompt_publishing'` (intermediate state from an interrupted publication) is recovered by re-publishing the prompt. This is idempotent because the content hash is deterministic from the immutable execution input fields.

#### handoff_findings

```sql
CREATE TABLE handoff_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('blocker','major','minor','informational')),
    is_non_overridable INTEGER NOT NULL DEFAULT 0 CHECK (is_non_overridable IN (0,1)),
    affected_artifact TEXT NOT NULL,
    description TEXT NOT NULL,          -- sanitized
    suggested_fix TEXT,                 -- sanitized
    override_justification TEXT,        -- min 20 chars when set
    acknowledged_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (handoff_record_id, finding_id)   -- unique finding_id within a handoff
);
CREATE INDEX idx_hf_handoff ON handoff_findings(handoff_record_id);
CREATE INDEX idx_hf_category ON handoff_findings(category);
```

#### execution_plans

```sql
CREATE TABLE execution_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_record_id INTEGER NOT NULL REFERENCES handoff_records(id),
    workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),          -- NOT NULL: standalone plans not supported in Wave 3
    workflow_step_run_id INTEGER NOT NULL REFERENCES workflow_step_runs(id), -- NOT NULL: every plan must belong to a step
    specification_version_id INTEGER NOT NULL REFERENCES specification_versions(id),
    artifact_digest TEXT NOT NULL,
    task_ids_json TEXT NOT NULL,
    repo_identity TEXT NOT NULL,
    workspace_identity TEXT NOT NULL,
    baseline_commit   TEXT NOT NULL,   -- git commit SHA of the repo baseline; persisted before auth request
    deadline_at       TEXT,            -- optional execution deadline (UTC ISO 8601, may be NULL)
    execution_mode TEXT NOT NULL
        CHECK (execution_mode IN ('manual','mock','real_when_isolated')),  -- requested mode only; 'blocked' is an outcome state
    canonical_execution_input_hash TEXT NOT NULL,  -- not globally unique; scoped by step
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','authorized','consumed','cancelled','stale')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workflow_run_id, workflow_step_run_id)  -- one active plan per step
);
CREATE INDEX idx_ep_handoff ON execution_plans(handoff_record_id);
CREATE INDEX idx_ep_hash ON execution_plans(canonical_execution_input_hash);
```

> **Immutable plan persistence requirement:** The complete immutable execution plan — including `baseline_commit`, `deadline_at`, and `canonical_execution_input_hash` — MUST be persisted in a single atomic transaction before `implementation_authorization` is requested. Authorization input reconstruction from mutable client data is prohibited.
>
> **Mandatory workflow ownership:** Every authorized execution plan MUST be linked to a workflow run and step (`workflow_run_id NOT NULL`, `workflow_step_run_id NOT NULL`). Standalone (non-workflow) execution plans are not supported in Wave 3.
>
> **Replacement plans:** After any input field changes (task IDs, artifact digest, baseline commit, deadline, execution mode), a new `execution_plans` row MUST be created and fresh authorization requested. The old plan's authorization is not transferable.
>
> **Crash-safe first transition:** The first manual prompt export, authorization consumption, durable prompt publication, and execution-attempt state transition MUST be crash-safe: a single `BEGIN IMMEDIATE` transaction spans the plan status update and attempt creation.
>
> **Authorization consumption guard:** Authorization MUST NOT be consumed for `real_when_isolated` mode when isolation prerequisites are unmet at execution time. The `IsolationGate` check runs before `dispatch_protected_txn`.

#### execution_attempts

```sql
CREATE TABLE execution_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_plan_id INTEGER NOT NULL REFERENCES execution_plans(id),
    execution_mode TEXT NOT NULL
        CHECK (execution_mode IN ('mock','manual','real')),
    status TEXT NOT NULL DEFAULT 'plan_prepared'
        CHECK (status IN ('plan_prepared','awaiting_impl_authorization',
                          'authorization_granted','dispatching','mock_execution',
                          'manual_handoff','real_execution_blocked','executing',
                          'awaiting_impl_report','impl_report_submitted',
                          'validating_report','completed','failed',
                          'cancelled','timeout','interrupted')),
    canonical_execution_input_hash TEXT NOT NULL,
    baseline_commit TEXT,
    deadline_at TEXT,
    issued_prompt_hash TEXT,            -- hash of the immutable prompt issued
    manual_handoff_created_at TEXT,
    report_submitted_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    timed_out_at TEXT,
    cancelled_at TEXT,
    interrupted_at TEXT,
    self_reported_verdict TEXT,
    devflow_verification_passed INTEGER CHECK (devflow_verification_passed IN (0,1)),
    error_code TEXT,
    error_message TEXT,                 -- sanitized
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_ea_plan ON execution_attempts(execution_plan_id);
CREATE INDEX idx_ea_status ON execution_attempts(status);
```

#### execution_artifacts

```sql
-- Owns implementation artifacts produced during execution (reports, diffs, logs)
CREATE TABLE execution_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_attempt_id INTEGER NOT NULL
        REFERENCES execution_attempts(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('codex_report','diff','patch','test_report','log','other')),
    file_name TEXT NOT NULL,            -- sanitized; DevFlow-generated or sanitized client name
    content_hash TEXT NOT NULL,
    content_size_bytes INTEGER NOT NULL,
    storage_key TEXT NOT NULL,          -- DevFlow-generated safe storage identifier
    created_at TEXT NOT NULL
);
CREATE INDEX idx_exart_attempt ON execution_artifacts(execution_attempt_id);
CREATE INDEX idx_exart_hash ON execution_artifacts(content_hash);
```

#### capability_records

```sql
CREATE TABLE capability_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    codex_cli_installed INTEGER NOT NULL CHECK (codex_cli_installed IN (0,1)),
    codex_cli_version TEXT,
    codex_cli_capability_status TEXT NOT NULL
        CHECK (codex_cli_capability_status IN ('unavailable','unverified','verified')),
    kiro_integration_type TEXT NOT NULL DEFAULT 'guided_handoff',
    isolation_status TEXT NOT NULL
        CHECK (isolation_status IN ('not_configured','prerequisites_not_met','prerequisites_met')),
    unmet_prerequisites_json TEXT,
    created_at TEXT NOT NULL
);
```

**Table count:** 7 new tables (handoff_records, handoff_artifacts, handoff_findings, execution_plans, execution_attempts, execution_artifacts, capability_records). Migration 010 is fully reversible; downgrade drops all 7 tables in reverse dependency order.

---

## 11. API Route Table

All under `/api/v1/coding-agent/`. All require `actor_id = Settings.LOCAL_OPERATOR_ID`.

| # | Method | Path | Sync/Async | Success | Key Error Codes |
|---|---|---|---|---|---|
| 1 | GET | `/capabilities` | sync | 200 | — |
| 2 | GET | `/isolation-status` | sync | 200 | — |
| 3 | POST | `/handoffs` | async | 202 | 400, 409 |
| 4 | GET | `/handoffs/{id}` | sync | 200 | 404 |
| 5 | POST | `/handoffs/{id}/export-prompt` | async | 202 | 404, 409 |
| 6 | GET | `/handoffs/{id}/prompt` | sync | 200 | 404, 409 |
| 7 | POST | `/handoffs/{id}/submit-report` | async | 202 | 404, 409 |
| 8 | GET | `/handoffs/{id}/validation-status` | sync | 200 | 404 |
| 9 | POST | `/handoffs/{id}/cancel` | sync | 200 | 404, 409 |
| 10 | POST | `/execution-plans` | sync | 201 | 400, 409 |
| 11 | POST | `/execution-plans/{id}/request-authorization` | async | 202 | 404, 403, 409 |
| 12 | GET | `/execution-plans/{id}/authorization-status` | sync | 200 | 404 |
| 13 | POST | `/executions` | async | 202 | 400, 403, 409 |
| 14 | GET | `/executions/{id}` | sync | 200 | 404 |
| 15 | GET | `/executions/{id}/prompt` | sync | 200 | 404, 409 |
| 16 | POST | `/executions/{id}/submit-impl-report` | async | 202 | 404, 409 |
| 17 | GET | `/executions/{id}/report-validation-status` | sync | 200 | 404 |
| 18 | POST | `/executions/{id}/cancel` | sync | 200 | 404, 409 |
| 19 | GET | `/artifacts/{id}` | sync | 200 file | 404, 403, 409 |
| 20 | GET | `/executions/{id}/audit` | sync | 200 | 404 |

**Total: 20 endpoints.**

Authorization request (endpoint 11) delegates to Wave 1 `request_bound_approval`; it does NOT create a new approval record directly. The Wave 1 approval resolve endpoint (`/api/v1/workflows/{run_id}/approvals/{id}/resolve`) is reused for granting authorization.

---

## 12. Streamlit Page Design

Streamlit accesses data exclusively via the API layer (`APIClient` httpx-based). No direct database or service layer access.

### 12.1 Component Hierarchy

```
CodingAgentPage
  ├── IsolationStatusBanner
  ├── ModeSelectionPanel (manual / mock / real_execution_blocked[disabled])
  ├── KiroHandoffPanel
  │     ├── PromptPreviewBlock (st.code; full text)
  │     ├── ExportPromptButton (download .txt)
  │     ├── HandoffStateIndicator
  │     └── ReportUploadWidget (file upload → submit-report)
  ├── FindingsPanel
  │     ├── FindingSummary (non-overridable blockers / advisory majors / minor counts)
  │     ├── NonOverridableFindingList (no override UI)
  │     └── AdvisoryFindingList (acknowledgment field with justification)
  ├── SpecApprovalPanel (reuses Wave 2 component; only rendered after findings resolved)
  ├── ExecutionPlanPanel
  ├── ImplAuthorizationPanel
  │     ├── AuthorizationSummary (task IDs, expiry)
  │     ├── ExpiryIndicator
  │     └── AuthorizeButton (delegates to Wave 1 approval resolve)
  ├── PromptExportPanel (manual/mock)
  ├── ImplReportUploadPanel (manual mode only)
  ├── ValidationResultsPanel
  │     ├── DevFlowVerificationResult (independent check)
  │     └── SelfReportedVerdict (clearly labelled as reported by user/agent)
  └── AuditTimelinePanel
```

### 12.2 Non-Overridable Findings Display

Non-overridable findings are displayed with:
- Red border and "BLOCKED — Cannot be overridden" label.
- No override button, no justification field.
- Clear message: "Specification must be revised before proceeding."

---

## 13. Architecture Decision Records

### ADR-W3-01: Manual as First-Class Execution Mode

**Decision:** `manual`, `mock`, and `real_when_isolated` are the three requested execution modes. `real_execution_blocked` is an outcome state, not a mode. Manual mode supports a complete lifecycle with durable wait states for user-imported reports.

**Rationale (F01):** Manual mode must be a first-class mode because it represents the primary production workflow in Wave 3. Defining `blocked` as a mode would require clients to request a "blocked" execution, which is semantically incorrect.

**Status:** Decided.

### ADR-W3-02: Kiro Guided Handoff as Selected Scope

**Decision:** `KiroHandoffExecutor` implements guided handoff. Wave 3 does not implement programmatic Kiro API access.

**Rationale (F09):** Guided handoff is the selected implementation scope. No programmatic Kiro API has been confirmed available. Claiming "no API exists" may be incorrect given limited investigation scope, so this ADR documents the selection as a scope decision, not a capability claim.

**Status:** Decided.

### ADR-W3-03: Codex CLI Abstract Contract (Flags Unverified)

**Decision:** The abstract invocation contract is documented without claiming specific flag names are verified. Wave 3 does not invoke the CLI. Wave 6 verifies and locks exact flags.

**Rationale (F09):** CLI not installed; exact flags cannot be verified. Documenting an unverified flag string as the invocation contract would create a defect in Wave 6 implementation. The abstract contract preserves typing without asserting unverified syntax.

**Status:** Decided.

### ADR-W3-04: Authorization Reuses Wave 1 dispatch_protected_txn

**Decision:** The Wave 3 implementation authorization gate uses the Wave 1 `WorkflowStepRunRepository.dispatch_protected_txn()` and `ApprovalService.request_bound_approval()` interfaces exactly as they exist in source. No new approval flow is introduced.

**Rationale (F02):** The actual Wave 1 source implements `request_bound_approval(run_id, step_run_id)` — it derives `parameters_hash` from `canonical_hash(step.inputs_json)`, not from a caller-supplied hash. The Wave 3 binding payload is embedded in `step.inputs_json`, which is the correct mechanism.

**Status:** Decided.

### ADR-W3-05: Corrected WorkspaceValidator Algorithm

**Decision:** Intermediate symlink check precedes `Path.resolve()`. `is_relative_to()` replaces string prefix matching. UNC/device paths rejected on Windows. TOCTOU risk documented as residual.

**Rationale (F04):** The v1.0.0 algorithm was incorrect: calling `is_symlink()` after `resolve()` always returns `False` because `resolve()` follows symlinks. The corrected algorithm checks components before resolve.

**Status:** Decided.

### ADR-W3-06: Report Trust Model

**Decision:** All imported report content is untrusted data. Validation is structural and integrity-based, not a guarantee of semantic correctness. Self-reported verdict is recorded separately from DevFlow's independent check.

**Rationale (F03):** A self-reported PASS proves only that the submitter claims PASS, not that work was done. DevFlow AI's verification covers schema, identity, digest, task coverage, and execution mode — not the quality of implementation.

**Status:** Decided.

### ADR-W3-07: IsolationGate Fail-Closed

**Decision:** Empty prerequisites list → `not_configured` → real execution blocked. Config flags alone do not satisfy the gate. Wave 3: all `real_when_isolated` → `real_execution_blocked`.

**Rationale (F01, F09):** Any path that enables real subprocess execution without verified prerequisites would constitute an isolation bypass. The `not_configured` status explicitly blocks rather than permitting execution by default.

**Status:** Decided.

### ADR-W3-08: Non-Running Human-Wait State

**Decision:** `awaiting_report` and `awaiting_impl_report` are non-running durable wait states. The Wave 1 `WAITING_APPROVAL` step state is reused as the step-level representation. No new step state is introduced.

**Rationale (F06):** Holding an executor coroutine for hours/days is not viable. The Wave 1 `WAITING_APPROVAL` step state already represents a durable human-wait state; KiroHandoffExecutor returns a result that maps to this state. Handoff sub-states are tracked in `handoff_records`.

**Status:** Decided.

### ADR-W3-09: Artifact Ownership Split (handoff_artifacts vs execution_artifacts)

**Decision:** Kiro prompts, Kiro reports, and manual Codex prompts are owned by `handoff_artifacts` (FK to `handoff_records`). Implementation artifacts (Codex reports, diffs, test reports) are owned by `execution_artifacts` (FK to `execution_attempts`).

**Rationale (F07):** Kiro verification occurs before execution attempts exist. The v1.0.0 design required `execution_attempt_id` in `report_artifacts` for Kiro reports, which was incorrect because execution attempts don't exist at that stage.

**Status:** Decided.

### ADR-W3-10: GitHub Automation Deferred to Wave 4

**Decision:** Wave 3 introduces no GitHub write operations.

**Rationale:** Wave 4 approval gates (pr_creation_approval, merge_approval) have not been implemented. Introducing git writes in Wave 3 would bypass those gates.

**Status:** Decided.

---

## 14. Security Architecture

### 14.1 Trust Boundaries

```
 TRUSTED                          │                     UNTRUSTED
─────────────────────────────────│─────────────────────────────────────
 DevFlow AI process               │   User-imported Kiro reports
 SQLite database                  │   User-imported Codex reports
 Operator configuration           │   User-uploaded manual impl reports
 Wave 1/2 approval records        │   Workspace files (future real exec)
 Audit log                        │   Codex CLI stdout/stderr (future)
```

Every crossing from UNTRUSTED to TRUSTED requires:
1. Schema validation (Pydantic model).
2. String field sanitization (control chars, length cap).
3. Explicit field-by-field transfer (no `**untrusted_dict`).
4. Digest/identity verification.

### 14.2 Sanitization Limits

Sanitization reduces risk. It does not eliminate prompt injection risk. Injected content that passes sanitization may still be misleading or harmful. The final defense is human review of all AI-assisted outputs and DevFlow AI's independent structural verification checks.

### 14.3 Non-Overridable Finding Categories

The following are **non-overridable** (workflow cannot advance regardless of user justification):
- `security` blockers
- `integrity_failure` blockers
- `digest_mismatch` blockers
- `invalid_authorization` blockers
- `isolation_failure` blockers
- `invalid_report_schema` blockers
- `missing_mandatory_evidence` blockers

Advisory acknowledgment is permitted only for:
- `major` findings without a security/integrity sub-category.
- `minor` and `informational` findings.
