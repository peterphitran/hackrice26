# Lou Hackathon Task Board

This is the execution board for the local-first Lou prototype. It converts the product plan into work that four engineers can complete in parallel while integrating through stable contracts.

The target demo is one Python repository, one known runtime regression, one graph-selected workload, one bounded agent patch, and one repeatable baseline → candidate → fix result.

## Status Reconciliation (after PR #15)

The checkboxes below reflect the implementation currently merged into `main`. The live CLI
vertical slice is complete for the checked-in `broken-store` fixture, but graph-driven impact
traversal, independent fix verification in the main flow, saved reports, and API endpoints remain
follow-up work.

## How to Use This Board

Checkboxes represent repository status:

```text
[ ] Not started
[~] In progress
[x] Complete and merged
[!] Blocked; add the blocking task ID and reason
[-] Removed from hackathon scope
```

Priorities:

```text
P0  Required for the end-to-end demo
P1  Improves evidence or presentation after the demo works
P2  Stretch work only
```

Effort is relative:

```text
S  One focused implementation session
M  Several connected changes
L  Split into smaller tasks before implementation
```

Every task is complete only when its acceptance checks pass. If a task changes a shared contract, update the contract fixture and notify every downstream owner before merging.

## Team Workstreams

Individual execution views:

- [Engineer 1 — Repository Intelligence](team/engineer-1/README.md)
- [Engineer 2 — Agents and Decisions](team/engineer-2/README.md)
- [Engineer 3 — Execution and Verification](team/engineer-3/README.md)
- [Engineer 4 — Platform and Product](team/engineer-4/README.md)
- [Team assignment index](team/README.md)

| Workstream | Suggested owner | Owns | Must not own |
|---|---|---|---|
| Repository Intelligence | Engineer 1 | Diff parsing, symbols, graph, impact, workload selection | Sandbox execution or agent policy |
| Agents & Decisions | Engineer 2 | Context bundles, diagnosis, patches, scoring, autonomy | Verification verdicts or GitHub credentials |
| Execution & Verification | Engineer 3 | Fixture, sandbox, pytest, k6, artifacts, comparisons | Agent conclusions or API persistence |
| Platform & Product | Engineer 4 | PostgreSQL, CLI/API, report, integration wiring | Analyzer logic or sandbox internals |

One person should act as integration owner for each gate. This role rotates and does not create a fifth workstream.

## Critical Path

```text
Shared contracts + deterministic fixture
                  ↓
     ┌────────────┼────────────┐
     ▼            ▼            ▼
Diff + graph   Verification   Persistence
     └────────────┼────────────┘
                  ▼
        Regression detection gate
                  ↓
       Context + agent + patch
                  ↓
          Fix verification gate
                  ↓
       Decision + report + demo
```

The team should protect this path. Semantic embeddings, GitHub integration, a frontend, and hosted services cannot block it.

## Shared Contract Freeze

Complete this section together before the four workstreams separate.

### [x] SH-001 — Lock the demo contract

- Priority: P0
- Owner: All; Engineer 4 records the decision
- Effort: S
- Depends on: none
- Output: a committed fixture specification describing the known-good state, N+1 candidate state, expected unit-test result, expected performance signal, and expected fix

Acceptance:

- [ ] The baseline and candidate behavior is written as a testable specification.
- [ ] The expected unit-test behavior and regression signal are agreed.
- [ ] The minimum meaningful regression threshold is explicit.
- [ ] The expected repair is small enough for one reviewable patch.
- [ ] The demo can run without GitHub or an external webhook.

### [x] SH-002 — Freeze version-one domain contracts

- Priority: P0
- Owner: Engineer 4 with sign-off from Engineers 1–3
- Effort: M
- Depends on: SH-001
- Output: Pydantic models under `backend/contracts/` plus serialized JSON fixtures

Required contracts:

| Contract | Producer | Consumer |
|---|---|---|
| `AnalysisJob` | CLI/API | Repository and execution services |
| `RepositoryChange` | Diff parser | Graph builder and report |
| `RepositoryContext` | Repository intelligence | Agent and decision services |
| `WorkloadSelection` | Workload selector | Verification service |
| `Finding` | Analyzers/comparator | Persistence, agent, report |
| `Evidence` | Execution/analyzers | Persistence and decisions |
| `VerificationResult` | Verification service | Agent, decision, report |
| `AgentResult` | Agent workflow | Patch verifier and report |
| `PatchArtifact` | Patch service | Verification and trusted publisher |
| `LouDecision` | Decision engine | CLI/API/report/GitHub adapter |

Acceptance:

- [ ] Every contract has a schema version.
- [ ] IDs, commit phase, status values, and confidence scales are unambiguous.
- [ ] JSON fixtures deserialize in contract tests.
- [ ] Unknown tool-specific fields live under metadata rather than changing the common schema.
- [ ] No contract contains credentials or raw unrestricted prompts.

### [x] SH-003 — Define ports, paths, and local configuration

- Priority: P0
- Owner: Engineer 4
- Effort: S
- Depends on: SH-001
- Output: one approved configuration table defining environment names, defaults, ports, and paths

Acceptance:

- [ ] PostgreSQL, fixture application, Lou API, and artifact-root ports/paths do not conflict.
- [ ] Defaults work locally without paid services.
- [ ] Secrets are optional for the recorded/mock agent path and ignored by Git.
- [ ] All paths are configurable and no code assumes a developer-specific absolute path.

### [x] SH-004 — Define artifact layout

- Priority: P0
- Owner: Engineers 3 and 4
- Effort: S
- Depends on: SH-002
- Output: artifact URI convention and directory layout

Target layout:

```text
.lou/artifacts/{analysis_run_id}/
├── baseline/
├── candidate/
├── fix/
├── patches/
└── report/
```

Acceptance:

- [ ] Every artifact has a phase, source, content hash, and relative URI.
- [ ] Large logs remain outside PostgreSQL.
- [ ] Re-running the same job does not overwrite a completed attempt.
- [ ] Artifact cleanup behavior is documented.

## Workstream 1 — Repository Intelligence

Engineer 1 can implement against contract fixtures before execution or persistence is ready.

### [x] RI-001 — Parse repository changes

- Priority: P0
- Owner: Engineer 1
- Effort: M
- Depends on: SH-002
- Output: `RepositoryChange` from repository path, base SHA, and candidate SHA

Acceptance:

- [ ] Detects added, modified, deleted, and renamed Python files.
- [ ] Rejects missing or invalid commit SHAs with a typed error.
- [ ] Produces stable, repository-relative paths.
- [ ] Unit tests cover empty diffs and renamed files.

### [x] RI-002 — Extract changed Python symbols

- Priority: P0
- Owner: Engineer 1
- Effort: M
- Depends on: RI-001
- Output: changed functions, methods, and classes with stable symbol keys and source ranges

Acceptance:

- [ ] Maps changed lines to their containing symbols.
- [ ] Handles module-level changes explicitly.
- [ ] Symbol keys distinguish methods with the same name in different classes.
- [ ] Parsing failures lower completeness and do not crash the whole analysis.

### [x] RI-003 — Build the typed repository graph

- Priority: P0
- Owner: Engineer 1
- Effort: M
- Depends on: SH-002
- Output: NetworkX graph containing the node and edge types defined in the hackathon README

Acceptance:

- [x] Builds `FILE`, `FUNCTION`, `CLASS`, `TEST`, `ENDPOINT`, `DATABASE_TABLE`, and `LOAD_SCENARIO` nodes where applicable.
- [x] Adds only evidence-backed typed edges.
- [x] Records extractor confidence and unresolved relationships.
- [x] Serializes deterministically to a JSON artifact.

### [x] RI-004 — Traverse impact from changed symbols

- Priority: P0
- Owner: Engineer 1
- Effort: M
- Depends on: RI-002, RI-003
- Output: `RepositoryContext`

Acceptance:

- [x] Returns direct callers, callees, tests, endpoints, data dependencies, and scenarios.
- [x] Uses explicit depth and node-count budgets.
- [x] Records why every returned node was selected.
- [x] Distinguishes no relationship found from incomplete extraction.
- [x] Fixture test confirms `checkout()` reaches the checkout test and workload.

### [x] RI-005 — Select verification workloads

- Priority: P0
- Owner: Engineer 1
- Effort: S
- Depends on: RI-004, SH-002
- Output: ordered `WorkloadSelection` records with reasons and confidence

Acceptance:

- [x] The N+1 candidate selects the checkout pytest and k6 scenarios.
- [x] Selection is deterministic for identical graph input.
- [x] A configured fallback scenario is returned when the graph is incomplete.
- [x] Workload commands come from the checked-in registry, never model-generated shell text.

### [x] RI-006 — Normalize one static analyzer

- Priority: P0
- Owner: Engineer 1
- Effort: S
- Depends on: SH-002
- Output: Semgrep or native analyzer results converted to `Finding` and `Evidence`

Acceptance:

- [x] Tool exit statuses are distinguished from detected findings.
- [x] Findings have stable fingerprints.
- [x] Raw output is stored as an artifact.
- [x] The adapter can be disabled without breaking runtime verification.

### [ ] RI-007 — Add bounded semantic retrieval

- Priority: P2
- Owner: Engineer 1
- Effort: M
- Depends on: RI-004
- Output: ranked supplemental context results

Acceptance:

- [ ] Works locally using lexical retrieval or optional embeddings.
- [ ] Never removes graph-required context.
- [ ] Records score and selection reason.
- [ ] Enforces a strict item/token budget.

## Workstream 2 — Agents and Decisions

Engineer 2 should use recorded contract fixtures until real findings and verification results are available.

### [x] AD-001 — Implement transparent debt scoring

- Priority: P0
- Owner: Engineer 2
- Effort: M
- Depends on: SH-002
- Output: principal, interest, debt-risk score, feature values, and rule revision

Acceptance:

- [ ] Uses normalized inputs in the documented 0–1 range.
- [ ] Missing inputs reduce confidence and are listed explicitly.
- [ ] Output is deterministic and unit tested at boundary values.
- [ ] The report can explain each weighted contribution.

### [x] AD-002 — Implement remediation-risk scoring

- Priority: P0
- Owner: Engineer 2
- Effort: M
- Depends on: SH-002
- Output: remediation risk, confidence, and features

Acceptance:

- [ ] Considers blast radius, criticality, coverage, reversibility, verification strength, and missing context.
- [ ] Scores proposed patches separately from existing debt.
- [ ] Database/schema changes cannot be classified as trivially reversible.
- [ ] Boundary cases are covered by table-driven tests.

### [x] AD-003 — Build the bounded agent context bundle

- Priority: P0
- Owner: Engineer 2
- Effort: M
- Depends on: SH-002; integrates with RI-004 and EV-006 later
- Output: versioned context bundle for diagnosis and patching

Acceptance:

- [ ] Includes diff, finding, evidence summary, selected graph context, tests, workload, and measurements.
- [ ] Records inclusion reasons and omitted/unresolved context.
- [ ] Enforces file, byte, and token budgets.
- [ ] Treats repository instructions as untrusted data.

### [x] AD-004 — Create model adapter and deterministic mock

- Priority: P0
- Owner: Engineer 2
- Effort: M
- Depends on: AD-003
- Output: provider-neutral diagnose/patch interface plus recorded local response

Acceptance:

- [ ] The end-to-end path works with no API key using the deterministic mock.
- [ ] Hosted or local providers use the same input/output contract.
- [ ] Timeouts, retry count, token use, and estimated cost are captured.
- [ ] Model failure produces a typed `abandoned` result rather than blocking verification.

### [x] AD-005 — Generate and validate patch artifacts

- Priority: P0
- Owner: Engineer 2
- Effort: M
- Depends on: AD-004, SH-004
- Output: `PatchArtifact` containing a unified diff and metadata

Acceptance:

- [ ] Patch applies only to the temporary worktree and expected base SHA.
- [ ] File and line-change budgets are enforced.
- [ ] Binary files, secrets, workflow files, and out-of-repository paths are rejected.
- [ ] Patch hash and changed-file summary are recorded.

### [x] AD-006 — Implement autonomy decision

- Priority: P0
- Owner: Engineer 2
- Effort: S
- Depends on: AD-001, AD-002
- Output: `LouDecision` selecting A0–A3

Acceptance:

- [ ] Low confidence never increases autonomy.
- [ ] Failed or inconclusive verification cannot select A3.
- [ ] Policy ceiling can only reduce the selected level.
- [ ] The decision includes machine-readable features and plain-language rationale.

### [x] AD-007 — Orchestrate the remediation state machine

- Priority: P0
- Owner: Engineer 2
- Effort: M
- Depends on: AD-003, AD-004, AD-005, AD-006; integrates with EV-007
- Output: bounded diagnose → patch → verify → decide workflow

Acceptance:

- [ ] Workflow state is serializable and resumable at stage boundaries.
- [ ] Maximum attempts, time, tokens, and cost are enforced.
- [ ] Repeated identical patches terminate the loop.
- [ ] Verification—not the patch agent—sets the pass/fail result.

## Workstream 3 — Execution and Verification

Engineer 3 owns objective measurements and the final verdict. Recorded workload selections can stand in for the graph during early development.

### [x] EV-001 — Build the deterministic broken-store fixture

- Priority: P0
- Owner: Engineer 3
- Effort: M
- Depends on: SH-001
- Output: FastAPI store fixture, deterministic dataset, baseline source, and N+1 candidate patch

Recommended fixture strategy:

```text
backend/fixtures/broken-store/
├── template/
├── variants/n_plus_one.patch
├── tests/
├── loadtests/checkout.js
└── scripts/seed_fixture_repo.py
```

The seed script creates a temporary Git repository with stable `good` and `n-plus-one` refs; do not commit a nested `.git` directory.

Acceptance:

- [ ] Seed command creates the same logical repository on every machine.
- [ ] Unit tests pass at both refs.
- [ ] Candidate produces the agreed deterministic regression signal.
- [ ] Fixture contains no external network or paid dependency.

### [x] EV-002 — Implement command execution primitives

- Priority: P0
- Owner: Engineer 3
- Effort: M
- Depends on: SH-002, SH-004
- Output: structured command result with exit code, duration, stdout/stderr artifact references, timeout, and resource metadata

Acceptance:

- [ ] Argument arrays are used instead of shell interpolation.
- [ ] Timeouts terminate child processes.
- [ ] Output size is bounded and complete output is stored as an artifact.
- [ ] Cancellation and tool-not-found errors are distinguishable.

### [x] EV-003 — Create controlled local sandbox lifecycle

- Priority: P0
- Owner: Engineer 3
- Effort: M
- Depends on: EV-001, EV-002
- Output: create → execute → collect → destroy lifecycle for fixture containers/worktrees

Acceptance:

- [ ] Runs as non-root where supported.
- [ ] Applies CPU, memory, PID, disk, and time limits.
- [ ] Does not mount the Docker socket or credentials.
- [ ] Network behavior is explicit and fixture-local.
- [ ] Cleanup runs after success, failure, timeout, or cancellation.

### [x] EV-004 — Run tests and static checks by commit phase

- Priority: P0
- Owner: Engineer 3
- Effort: S
- Depends on: EV-002, EV-003
- Output: baseline/candidate/fix test results using shared contracts

Acceptance:

- [x] Records phase and exact commit SHA.
- [x] Separates command failure from test failure.
- [x] Preserves coverage summary when enabled.
- [x] Same selected commands run for candidate and fix.

### [x] EV-005 — Build repeated k6 checkout experiment

- Priority: P0
- Owner: Engineer 3
- Effort: M
- Depends on: EV-001, EV-003
- Output: warm-up plus repeated p50/p95/p99, throughput, error-rate, and query-count measurements

Acceptance:

- [x] Uses fixed fixture data and equivalent container limits.
- [x] Stores raw k6 output and aggregate metrics.
- [x] Executes the `WorkloadSelection` contract rather than hardcoded agent commands.
- [x] Candidate regression is reproduced reliably enough for the demo.
- [x] Excessive variance yields `inconclusive`.

### [x] EV-006 — Compare baseline and candidate

- Priority: P0
- Owner: Engineer 3
- Effort: M
- Depends on: EV-004, EV-005
- Output: differential `VerificationResult`, findings, and evidence

Acceptance:

- [x] Reports absolute values, deltas, thresholds, repetitions, and variance.
- [x] Classifies baseline-only, candidate-only, and shared failures.
- [x] Does not attribute pre-existing failures to the candidate.
- [x] Produces the expected N+1 regression finding without LLM interpretation.

### [x] EV-007 — Verify proposed fixes independently

- Priority: P0
- Owner: Engineer 3
- Effort: M
- Depends on: AD-005, EV-006
- Output: fix-phase `VerificationResult`

Acceptance:

- [x] Applies the patch to a fresh worktree at the expected base.
- [x] Reruns the same unit and load workloads.
- [x] Rejects patches that disable or weaken verification.
- [x] Marks improvement, regression, no material change, or inconclusive.

### [ ] EV-008 — Add local structured observability

- Priority: P1
- Owner: Engineer 3
- Effort: S
- Depends on: EV-002
- Output: correlated structured logs for each analysis stage

Acceptance:

- [ ] Every log includes analysis run ID, phase, stage, and attempt.
- [ ] No credentials or raw environment dumps are logged.
- [ ] A failed demo can be diagnosed from local artifacts.

## Workstream 4 — Platform and Product

Engineer 4 can use shared JSON fixtures to develop persistence, CLI, API, and reporting before the other services are implemented.

### [x] PF-001 — Establish Python project tooling

- Priority: P0
- Owner: Engineer 4
- Effort: S
- Depends on: SH-003
- Output: package metadata, locked dependencies, lint/type/test commands, and `.env.example`

Acceptance:

- [ ] One documented setup command works from a clean clone.
- [ ] CLI and tests import `lou` without custom developer paths.
- [ ] Dependency versions are reproducible.
- [ ] Lint, formatting check, type check, and unit tests have stable commands.

### [x] PF-002 — Add local PostgreSQL and migration tooling

- Priority: P0
- Owner: Engineer 4
- Effort: M
- Depends on: SH-003
- Output: Compose PostgreSQL service, SQLAlchemy setup, Alembic configuration, and first migration

First vertical-slice tables:

```text
repositories
analysis_runs
workloads
verification_runs
findings
evidence
```

Acceptance:

- [ ] Database starts locally with no managed account.
- [ ] Migration upgrades an empty database and downgrades cleanly.
- [ ] Application performs a health check with a non-superuser role.
- [ ] Integration tests use an isolated test database.

### [x] PF-003 — Implement run and evidence repositories

- Priority: P0
- Owner: Engineer 4
- Effort: M
- Depends on: PF-002, SH-002, SH-004
- Output: persistence interfaces and SQLAlchemy implementations

Acceptance:

- [ ] Creates an idempotent analysis run by deduplication key.
- [ ] Enforces valid lifecycle transitions.
- [ ] Persists verification, finding, and evidence summaries transactionally.
- [ ] Domain services do not depend directly on SQLAlchemy sessions.

### [x] PF-004 — Implement the local CLI vertical slice

- Priority: P0
- Owner: Engineer 4
- Effort: M
- Depends on: PF-001, SH-002; integrates with all P0 service contracts
- Output: `lou analyze` and `lou report`

Acceptance:

- [ ] Accepts repository, base SHA, candidate SHA, and optional configuration.
- [ ] Prints stage progress and the final run ID.
- [ ] Operational failures always return nonzero; an explicit `--ci` mode also returns nonzero for blocked or inconclusive decisions.
- [ ] `lou report` works from stored data without rerunning analysis.

### [ ] PF-005 — Build the local evidence report

- Priority: P0
- Owner: Engineer 4
- Effort: M
- Depends on: SH-002; integrates with RI-004, EV-006, EV-007, AD-006
- Output: terminal and saved Markdown or JSON report

Acceptance:

- [ ] Shows commits, changed symbols, selected workloads, and selection reasons.
- [ ] Shows baseline/candidate/fix values and deltas.
- [ ] Shows findings, evidence references, risk features, confidence, and autonomy decision.
- [ ] Distinguishes verified, failed, and inconclusive outcomes.
- [ ] Links to local artifacts using repository-relative paths.

### [ ] PF-006 — Add FastAPI endpoints

- Priority: P1
- Owner: Engineer 4
- Effort: M
- Depends on: PF-003, PF-004
- Output: health, create-analysis, analysis-status, and analysis-report endpoints

Acceptance:

- [ ] API calls the same application service as the CLI.
- [ ] Request and response bodies use versioned contracts.
- [ ] Duplicate requests return the existing run.
- [ ] Long execution does not block once a worker is introduced.

### [ ] PF-007 — Add results page

- Priority: P2
- Owner: Engineer 4
- Effort: M
- Depends on: PF-006
- Output: one PR-analysis/results page

Acceptance:

- [ ] Renders saved fixture data when the backend is unavailable.
- [ ] Displays evidence and deltas rather than only a score.
- [ ] Handles failed and inconclusive states.
- [ ] Does not become required for the CLI demo.

### [ ] PF-008 — Add trusted GitHub integration

- Priority: P2
- Owner: Engineer 4
- Effort: M
- Depends on: PF-006, AD-006, EV-007
- Output: verified webhook, GitHub check, and optional remediation PR publisher

Acceptance:

- [ ] Verifies webhook signatures and deduplicates delivery IDs.
- [ ] Uses least-privilege, short-lived installation credentials.
- [ ] Agent and sandbox never receive credentials.
- [ ] Only an A3 decision with passed verification can publish a PR.
- [ ] Local CLI remains fully functional without GitHub.

## Integration Gates

### [x] INT-001 — Contract compatibility gate

- Priority: P0
- Integration owner: Engineer 4
- Depends on: SH-002, RI-001, AD-001, EV-002, PF-003

Acceptance:

- [ ] Every workstream can consume the shared JSON fixtures.
- [ ] Producers serialize data that downstream contract tests accept.
- [ ] No circular imports exist between workstreams.
- [ ] Contract version mismatch fails with a clear error.

### [~] INT-002 — Runtime regression detection gate

- Priority: P0
- Integration owner: Engineer 3
- Depends on: RI-001–RI-006, EV-001–EV-006, PF-002–PF-005

Acceptance:

- [ ] One local command creates an analysis run.
- [ ] Graph traversal selects the checkout workload.
- [ ] Unit tests pass while k6 or query evidence identifies the N+1 regression.
- [ ] Finding and evidence survive process restart in PostgreSQL/artifacts.
- [ ] Report clearly explains why the candidate is worse than baseline.

This is the first complete demo. Do not begin stretch work until it passes reliably.

### [ ] INT-003 — Agent remediation gate

- Priority: P0
- Integration owner: Engineer 2
- Depends on: INT-002, AD-003–AD-007, EV-007

Acceptance:

- [ ] Recorded/mock agent path works without network access.
- [ ] Patch is applied only in a fresh temporary worktree.
- [ ] Exact verification is rerun against the fix.
- [ ] Failed verification cannot be presented as success.
- [ ] Decision output reflects verification and remediation risk.

### [ ] INT-004 — Reproducibility gate

- Priority: P0
- Integration owner: Engineer 1
- Depends on: INT-003

Acceptance:

- [ ] A teammate can start from a clean clone and follow the README successfully.
- [ ] Demo works on at least two team machines.
- [ ] Commit SHAs, dependency lock, fixture data, workload hash, and resource limits appear in evidence.
- [ ] Repeated runs produce the same verdict or explicitly report excessive noise.

### [ ] INT-005 — Presentation gate

- Priority: P1
- Integration owner: Engineer 4
- Depends on: INT-004

Acceptance:

- [ ] Five-minute demo script has been rehearsed.
- [ ] Live path and recorded fallback use the same saved evidence format.
- [ ] Report tells one story: prediction → regression → patch → verified result → decision.
- [ ] A failed optional UI or GitHub integration cannot derail the core demo.

## Parallel Execution Plan

### Wave 0 — Align and freeze

All engineers complete SH-001 and SH-002. Engineers 3 and 4 complete SH-003 and SH-004. Avoid writing implementations against informal dictionaries before these merge.

### Wave 1 — Build with fixtures

Work proceeds simultaneously:

```text
Engineer 1  RI-001 through RI-006
Engineer 2  AD-001 through AD-004 using recorded contracts
Engineer 3  EV-001 through EV-006 using recorded selections
Engineer 4  PF-001 through PF-005 using recorded results
```

Merge small contract-compatible changes continuously. Do not wait for a complete workstream branch.

### Wave 2 — Integrate detection

Complete INT-001 and INT-002. Freeze stretch tasks until the regression-detection path is reproducible.

### Wave 3 — Close remediation loop

Engineer 2 completes patch/orchestration work while Engineer 3 integrates independent fix verification. Engineers 1 and 4 improve context explanations and reporting. Complete INT-003 and INT-004.

### Wave 4 — Present and stretch

Complete INT-005, then select P1/P2 work based on remaining time. GitHub and frontend work never replace demo rehearsal or reproducibility.

## Merge Order

Use this order to reduce conflicts:

1. Project tooling and versioned contracts
2. Fixture and local infrastructure
3. Independent service modules with unit tests
4. Persistence adapters
5. CLI application service
6. Detection integration
7. Agent and fix verification integration
8. Report polish
9. Optional API, UI, and GitHub integration

Keep orchestration thin. Repository intelligence, execution, scoring, and persistence must remain directly testable without running LangGraph or FastAPI.

## Pull Request Checklist

Every implementation PR should answer:

- [ ] Which task ID does this close?
- [ ] Which contract does it produce or consume?
- [ ] What command verifies it locally?
- [ ] What fixture or failure case proves it works?
- [ ] Does it introduce a network, credential, cost, or platform dependency?
- [ ] Does it change the five-minute demo path?
- [ ] Are logs, generated artifacts, caches, secrets, and nested Git data excluded?

## Parking Lot

The detailed stretch tasks are RI-007 for semantic retrieval, PF-007 for the results page, and PF-008 for GitHub integration. Record additional post-hackathon ideas here instead of expanding P0 scope. Do not start parking-lot work before INT-004 passes.

- [ ] PK-001 — SCIP semantic index integration
- [ ] PK-002 — OPA service integration
- [ ] PK-003 — Local OpenTelemetry collector and trace UI
- [ ] PK-004 — Redis/Celery worker deployment
- [ ] PK-005 — Multi-language repository graph
- [ ] PK-006 — Production canary and outcome collection
- [ ] PK-007 — Learned debt-interest calibration

## Final Demo Checklist

- [ ] Clean-clone setup instructions work.
- [ ] Local PostgreSQL starts successfully.
- [ ] Fixture repository seeds deterministically.
- [ ] Baseline and candidate unit tests pass.
- [ ] Candidate regression is measurable.
- [ ] Graph selects the correct workload.
- [ ] Evidence is persisted with immutable identity.
- [ ] Agent path works live or through the deterministic mock.
- [ ] Patch is bounded and independently verified.
- [ ] Report shows baseline → candidate → fix.
- [ ] Risk and autonomy decision is explained.
- [ ] Demo works without paid or hosted infrastructure.
- [ ] Recorded fallback artifacts are available.
