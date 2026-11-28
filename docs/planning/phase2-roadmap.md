# DevFlow AI — Phase 2 Implementation Roadmap

**Version:** 2.2.0
**Status:** Updated — Wave 2 approved decisions recorded
**Branch:** `feature/phase2-wave2-ai-spec-generation`
**Base commit:** `b3b37ff` (Wave 1 implementation merged, develop HEAD)
**Last Updated:** 2026-11-18
**Supersedes:** None (Phase 2 roadmap). Extends `docs/planning/phase1-roadmap.md` (v1.5.9); does not modify it.

**Version history:**
- v2.0.0 — 2026-11-17 — Initial six-wave Phase 2 roadmap. Defines Waves 1–6: Workflow Execution Engine, AI Specification Generation and Review, Kiro Guided Handoff and Codex CLI, Approval-Gated GitHub Automation, Streamlit Workflow Experience, Security Hardening and End-to-End Validation. Wave 1 specifications are authored in this branch (`feature/phase2-wave1-workflow-execution-engine`); Waves 2–6 are outlined only and are not specified in detail in Wave 1 (per scope).
- v2.1.0 — 2026-11-17 — Wave 1 spec reconciliation pass (see `docs/reports/phase2-wave1-spec-resolution.md`): resolved defaults applied (pending TTL 3600s, approved authorization validity 3600s via separate column, concurrency 1, lease 60s, heartbeat 15s, recovery auto-reconcile + explicit start, mandatory executor guards, immutable full template snapshot per run); Wave 1 gate updated to reflect validity-bounded + fenced approvals, transient retries, and immutable template snapshot. Eight step states and six Phase 1 run states preserved.
- v2.2.0 — 2026-11-18 — Wave 2 approved decisions recorded (branch `feature/phase2-wave2-ai-spec-generation`): Ollama confirmed as default provider; OpenAI confirmed as optional and never a silent fallback; `MCPClientAdapter` isolation design adopted; migration 009 assigned to Wave 2; approved decisions 1–10 from original user request applied; Wave 2 spec files authored in `.kiro/specs/phase2-wave2-ai-specification-generation/`; MCP SDK compatibility verdict UNKNOWN pending PR #20 evaluation; Wave 1 approval framework reused for spec approval gate.

---

## Phase 2 Goal

Deliver the end-to-end DevFlow AI workflow with full auditability and no automatic PR merging:

1. A user describes a feature.
2. DevFlow AI creates a workflow run.
3. Ollama (default) or optional OpenAI API generates specifications.
4. Kiro verifies specifications through guided handoff.
5. The AI reviews the Kiro report.
6. The user approves specifications.
7. Codex CLI implements approved tasks.
8. The AI reviews implementation results.
9. The user approves PR creation.
10. The GitHub adapter creates the PR and monitors CI.
11. The user separately approves merging after CI passes.

Every operation is auditable. PR merging is never automatic. The application remains local-first, single-user, loopback-only, and free-core; Ollama is the default AI provider and OpenAI is optional. No paid service is mandatory.

**Phase 1 → Phase 2 boundary:** Phase 1 delivered persistent project management, workflow state tracking with human approval gates, three live adapters (GitHub, Ollama, Figma), MCP client infrastructure, and GitHub workflow orchestration — but explicitly deferred autonomous execution of `definition_json` to Phase 2. Phase 2 Wave 1 implements that deferred execution engine.

---

## Scope Boundaries

### In scope (Phase 2)

| Wave | Capability | Key deliverables |
|---|---|---|
| Wave 1 | Workflow Execution Engine | Step-level eight-state lifecycle, `WorkflowOrchestrator`, executor contract, mock/no-op executor, durable recovery, approval checkpoints, minimal API/UI |
| Wave 2 | AI Specification Generation and Review | Ollama (default) / OpenAI (optional) as executors generating specs; AI review of Kiro report; spec-approval gate |
| Wave 3 | Kiro Guided Handoff and Codex CLI | Kiro verification handoff executor; Codex CLI implementation executor; implementation-authorization gate |
| Wave 4 | Approval-Gated GitHub Automation | PR creation executor; CI monitoring; PR-creation approval gate; merge-approval gate (no auto-merge) |
| Wave 5 | Streamlit Workflow Experience | Full workflow dashboard, run/step visualization, approval UX, audit timeline |
| Wave 6 | Security Hardening and End-to-End Validation | OS-level isolation prerequisites for real coding-agent execution; end-to-end workflow validation; hardening |

### Out of scope (Phase 2)

| Capability | Reason |
|---|---|
| Multi-user access control | Local single-user tool; explicit trust boundary |
| Automatic PR merge | Permanent exclusion; merge always requires separate explicit approval |
| OS-level sandboxing in Wave 1 | Documented as a prerequisite (Wave 6 / later); Wave 1 is application-level only |
| Android, React, TypeScript, Vite | Global constraint; not in scope for any phase |

---

## Alembic Migration Chain (Phase 2)

Phase 1 head (verified): `007_add_github_confirmations`. Phase 2 extends the chain:

```
007_add_github_confirmations  (Phase 1 head — verified)
  └── 008_add_workflow_execution_engine   ← Wave 1 (planned; confirmed as actual head at Wave 2 spec authoring)
        └── 009_add_ai_specification_generation  ← Wave 2 (assigned)
              └── 010..  (Waves 3–6 as needed; not numbered in this roadmap)
```

**Wave 1 migration:** `008_add_workflow_execution_engine` (chains from `007`; confirmed in codebase at Wave 2 spec authoring). Creates `workflow_step_runs`; additively extends `approval_requests` (no CHECK-constraint change). Reversible. See `.kiro/specs/phase2-wave1-workflow-execution-engine/design.md` §3.

**Wave 2 migration:** `009_add_ai_specification_generation` (chains from `008`; assigned in Wave 2 spec). Creates `specification_requests`, `specification_versions`, `specification_artifacts`, `generation_attempts`, `review_attempts`, `review_findings` and `specification_approval_requests`, with the indexes specified in the frozen design. Does NOT alter any Phase 0/1/Wave 1 table. Reversible. See `.kiro/specs/phase2-wave2-ai-specification-generation/design.md` §9.

**Later waves** add migrations only as required (e.g., Kiro/Codex artifact tables, PR/CI tracking). Migration revision numbers are not invented ahead of time; each wave confirms the then-current head.

---

## Wave Summary

### Wave 1 — Workflow Execution Engine
**PR target:** `develop`
**Complexity:** High
**Gate:** Eight-state step lifecycle enforced (eight step states, six Phase 1 run states preserved); orchestrator creates/runs/stops/cancels runs with immutable template snapshot per run; mock executor validates the engine; recovery auto-reconciles stale steps (no auto-dispatch); approval checkpoints are digest-bound, single-use, validity-bounded, and fenced against stale completions; retries are transient (`RUNNING→READY`, never via `FAILED`); all REQ-E8.1 named tests pass; migration 008 round-trips (incl. `workflow_runs` snapshot + `approval_requests` validity columns); Phase 1 tests unchanged (legacy `NULL`-template runs preserved).

| Task | Description | Size |
|---|---|---|
| T1 | Workflow template schema + validation | M |
| T2 | `WorkflowStepRun` model + migration 008 (incl. run snapshot + approval validity columns) | M |
| T3 | Repositories (step-run CAS/lease/fence + approval validity/consume) | M |
| T4 | Executor contract (schemas/executor.py) + MockExecutor + registry | M |
| T5 | WorkflowOrchestrator (create/start/dispatch/retry/cancel; Phase 1 service reuse) | L |
| T6 | Approval checkpoints (binding, validity, re-gating, fenced consumption) | M |
| T7 | Recovery service + leases (auto-reconcile, no auto-dispatch) | M |
| T8 | Routers + OpenAPI | M |
| T9 | Streamlit minimal UI | M |
| T10 | Test suite (Wave 1) | L |
| T11 | Wave 1 readiness report + final verification | S |

**Spec:** `.kiro/specs/phase2-wave1-workflow-execution-engine/{requirements,design,tasks}.md`.

### Wave 2 — AI Specification Generation and Review
**PR target:** `develop`
**Complexity:** High
**Branch:** `feature/phase2-wave2-ai-spec-generation`
**Spec:** `.kiro/specs/phase2-wave2-ai-specification-generation/{requirements,design,tasks}.md` (frozen v1.6.1)
**Readiness report:** `docs/reports/phase2-wave2-spec-readiness.md`
**MCP assessment:** `docs/reports/phase2-wave2-mcp-compatibility-assessment.md`

**Gate:** Ollama (default) generates structured specification artifacts (`requirements.md`, `design.md`, `tasks.md`) from a user-authored feature description; OpenAI is optional (config-gated, NEVER a silent fallback); `SpecificationReviewService` evaluates artifacts and classifies findings; a specification-approval checkpoint (reusing Wave 1 `ApprovalService`) gates progression; all within the Wave 1 execution engine; no external write without approval.

**Approved decisions (recorded 2026-11-18):**
1. Wave 1 (`b3b37ff`) is the accepted baseline for Wave 2 planning.
2. Ollama is the default AI provider; OpenAI is optional and requires explicit configuration (`AI_PROVIDER="openai"` + non-empty `OPENAI_API_KEY`).
3. OpenAI MUST NEVER be invoked as a silent fallback when Ollama fails.
4. `MCPClientAdapter` wraps existing `MCPClient`; all `mcp.*` imports confined to `MCPClient`; services use only `MCPClientAdapter`.
5. MCP declares `mcp>=2.3.0,<3.0` and locks 2.3.0. The transport validates SDK union messages through Pydantic.
6. Dependabot PR #20 must be evaluated separately before merging; it is NOT part of this Wave 2 spec branch.
7. Human approval is required. Workflow-bound requests reuse Wave 1 `ApprovalService` and protected dispatch. Standalone requests use a separate single-use approval record.
8. Wave 2 ships no real coding-agent execution (Codex CLI, Kiro automation, autonomous GitHub writes).
9. Alembic migration 009 assigned to Wave 2 (`009_add_ai_specification_generation`, chains from `008`).
10. `SpecificationApprovalService` verifies the current version and artifact digest. TTL, actor binding and single-use gates apply to both approval paths.

| Task | Description | Size |
|---|---|---|
| W2.T1 | `AIProvider` interface + `OllamaProvider` | M |
| W2.T2 | `OpenAIProvider` (optional, config-gated) | M |
| W2.T3 | `MCPClientAdapter` with allowlist policy + credential isolation | M |
| W2.T4 | `SpecificationGenerationService` + prompt engineering | L |
| W2.T5 | `SpecificationReviewService` + finding classification | M |
| W2.T6 | `SpecificationVersionRepository` + content hashing | M |
| W2.T7 | `SpecificationApprovalService` integrating Wave 1 approval framework | M |
| W2.T8 | Alembic migration `009_add_ai_specification_generation` | M |
| W2.T9 | FastAPI routes + OpenAPI schemas | M |
| W2.T10 | Streamlit pages (spec request, generation status, review findings, approval) | M |
| W2.T11 | Test suite (offline/mocked providers; CI offline) | L |
| W2.T12 | Wave 2 readiness report + final verification | S |
| W2.T13 | Specification startup recovery | S |
| W2.T14 | Wave 2 executor registration | M |

**Implementation evidence:** [Group D report](../reports/phase2-wave2-group-d-implementation-report.md) and [Wave 2 final report](../reports/phase2-wave2-final-implementation-report.md). Dependabot PR #20 is declaration-only and becomes redundant after the tested Wave 2 upgrade lands.

### Wave 3 — Kiro Guided Handoff and Codex CLI
**PR target:** `develop`
**Complexity:** High
**Spec:** `.kiro/specs/phase2-wave3-coding-agent-integration/{requirements,design,tasks}.md` (frozen v1.0.4)
**Readiness report:** `docs/reports/phase2-wave3-final-spec-readiness.md`
**Gate:** Kiro verifies specifications through guided handoff; Codex CLI implements approved tasks; implementation-authorization checkpoint gates Codex; real coding-agent execution is gated behind the isolation prerequisites from Wave 1 §12.4 (do not enable until OS-level isolation exists).

| Task (indicative) | Description | Size |
|---|---|---|
| K1 | Kiro handoff executor (verification) | M |
| K2 | Codex CLI executor (implementation) | L |
| K3 | Implementation-authorization checkpoint | M |
| K4 | Isolation prerequisite gate (refuse execution until Wave 6 hardening) | M |
| K5 | Tests: Wave 3 | L |

**Open decisions for Wave 3:** Kiro programmatic interface; Codex CLI invocation contract; working-directory and env allowlists; artifact (diff/patch) handling.

### Wave 4 — Approval-Gated GitHub Automation
**PR target:** `develop`
**Complexity:** High
**Gate:** PR creation executor (reusing Phase 1 `GitHubConfirmation` consume-on-use); CI monitoring; PR-creation approval gate; merge-approval gate; **no auto-merge endpoint ever**.

| Task (indicative) | Description | Size |
|---|---|---|
| G1 | PR creation executor (workflow-bound) | L |
| G2 | CI status monitoring executor | M |
| G3 | PR-creation approval checkpoint | M |
| G4 | Merge-approval checkpoint (UI affordance only; no merge endpoint) | M |
| G5 | Tests: Wave 4 | L |

**Open decisions for Wave 4:** workflow-bound vs. standalone GitHub operations (Phase 1 was standalone `workflow_run_id=None`); canonical parameter binding for workflow-bound operations; CI provider abstraction.

### Wave 5 — Streamlit Workflow Experience
**PR target:** `develop`
**Complexity:** Medium
**Gate:** Full workflow dashboard; run/step visualization with live status; approval UX; audit timeline; filtering; the minimal Wave 1 UI is replaced by the full experience.

| Task (indicative) | Description | Size |
|---|---|---|
| U1 | Workflow dashboard (run/step visualization) | M |
| U2 | Approval UX (per-checkpoint) | M |
| U3 | Audit timeline | M |
| U4 | APIClient extensions | S |
| U5 | Tests: Wave 5 | M |

### Wave 6 — Security Hardening and End-to-End Validation
**PR target:** `develop`
**Complexity:** High
**Gate:** OS-level isolation prerequisites met before real coding-agent execution; end-to-end workflow (steps 1–11) validated; security review pass; all negative security tests green.

| Task (indicative) | Description | Size |
|---|---|---|
| S1 | OS-level process isolation (Windows Job Objects / Linux namespaces+seccomp) | L |
| S2 | OS-enforced filesystem allowlist | L |
| S3 | Network egress control | M |
| S4 | Resource limits + credential vault | M |
| S5 | End-to-end workflow validation (mock + real providers) | L |
| S6 | Security review + hardening tests | L |

**Open decisions for Wave 6:** OS-specific isolation strategy; whether real coding-agent execution is enabled behind a feature flag; audit integrity (chained hashes).

---

## Task Dependency Graph (Phase 2, high level)

```
Phase 1 baseline (2d077ec)
    │
    └── Wave 1 (T1..T11) ─── Wave 1 PR → develop
            │
            ├── Wave 2 (A1..A5) ─── Wave 2 PR → develop
            │       │
            │       └── Wave 3 (K1..K5) ─── Wave 3 PR → develop
            │               │
            │               └── Wave 4 (G1..G5) ─── Wave 4 PR → develop
            │                       │
            │                       └── Wave 5 (U1..U5) ─── Wave 5 PR → develop
            │                               │
            │                               └── Wave 6 (S1..S6) ─── Wave 6 PR → develop
            │
            └── (Wave 5 may start in parallel with Waves 3–4 once Wave 1 is merged; UI mocks the later waves)
```

Waves are sequenced by dependency; Wave 5 (UI) can overlap later waves because the Wave 1 engine provides deterministic state to visualize and the UI can mock not-yet-built executors.

---

## Security Summary (Phase 2)

Every consequential/external action requires explicit, digest-bound, single-use approval. No action modifies external state silently.

| Action | Authorization mechanism | Wave |
|---|---|---|
| Generate specification (Ollama/OpenAI) | specification_approval checkpoint | 2 |
| Implement via Codex CLI | implementation_authorization checkpoint | 3 |
| Create PR | pr_creation_approval checkpoint + `GitHubConfirmation` (reuse Phase 1) | 4 |
| Merge PR | merge_approval checkpoint; **no merge endpoint** | 4 (UI affordance only) |
| Workflow step with side-effect | `ApprovalRequest` gate (Phase 1) / Phase 2 checkpoint | 1–4 |
| Real coding-agent execution | Gated behind Wave 6 OS-level isolation | 3 (disabled) / 6 (enabled) |

Credential presence is authentication, not authorization (Phase 1 invariant preserved). Auto PR merge is permanently excluded.

---

## Complexity Estimates

| Wave | Tasks (rough) | Story points | Calendar estimate |
|---|---|---|---|
| Wave 1 | 11 | 30–40 | 4–6 days |
| Wave 2 | 5 | 18–24 | 3–4 days |
| Wave 3 | 5 | 22–30 | 4–6 days |
| Wave 4 | 5 | 20–26 | 3–5 days |
| Wave 5 | 5 | 14–18 | 2–3 days |
| Wave 6 | 6 | 28–40 | 5–8 days |
| **Total** | **37** | **132–178** | **21–32 days** |

Estimates assume a single developer working one task at a time per wave with same-day independent review per wave PR. Waves 2–6 estimates are indicative; detailed task breakdowns are authored per wave.

---

## References

- Wave 1 requirements: `.kiro/specs/phase2-wave1-workflow-execution-engine/requirements.md`
- Wave 1 design: `.kiro/specs/phase2-wave1-workflow-execution-engine/design.md`
- Wave 1 tasks: `.kiro/specs/phase2-wave1-workflow-execution-engine/tasks.md`
- Wave 2 requirements: `.kiro/specs/phase2-wave2-ai-specification-generation/requirements.md`
- Wave 2 design: `.kiro/specs/phase2-wave2-ai-specification-generation/design.md`
- Wave 2 tasks: `.kiro/specs/phase2-wave2-ai-specification-generation/tasks.md`
- Wave 2 readiness report: `docs/reports/phase2-wave2-spec-readiness.md`
- Wave 2 MCP compatibility: `docs/reports/phase2-wave2-mcp-compatibility-assessment.md`
- Phase 1 roadmap: `docs/planning/phase1-roadmap.md`
- Phase 1 requirements/design/tasks: `.kiro/specs/phase1-functional-orchestration/`
- Phase 1 spec readiness report: `docs/reports/phase1-wave5-spec-readiness.md`
- Wave 1 spec readiness report: `docs/reports/phase2-wave1-spec-readiness.md`
