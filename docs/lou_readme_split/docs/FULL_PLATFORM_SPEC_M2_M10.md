# Lou Full Platform Specification: M2–M10

Status: proposed  
Scope: the complete platform described by the Lou architecture and roadmap  
Baseline: M0 benchmark fixture, M1 local regression detection, deterministic verification,
PostgreSQL evidence persistence, CLI, FastAPI, graph traversal, and reporting are implemented.

This document turns the long-term roadmap into independently deliverable milestones. Each
milestone has a narrow contract, measurable acceptance criteria, and an explicit integration gate.
No milestone may silently expand the authority of an agent or bypass independent verification.

## Target operating loop

```text
repository / PR
→ intake and index
→ predict impact
→ run selected validation
→ create evidence and decision
→ diagnose and propose patch
→ verify patch independently
→ policy check
→ optional PR / deployment
→ observe outcome
→ calibrate predictions and portfolio priorities
```

## Global requirements

1. Every cross-layer payload is a versioned Pydantic contract with fixture, schema, and round-trip tests.
2. Every untrusted operation runs in a disposable, resource-limited workspace. Agents cannot publish,
   change policy, access credentials, or choose unrestricted commands.
3. Every decision records inputs, policy revision, confidence, uncertainty, and evidence references.
4. `inconclusive` is a first-class result. Missing context, tool failure, or excessive variance must
   never be represented as a regression or a successful fix.
5. PostgreSQL remains the system of record; artifacts are content-addressed and repository-relative.
6. Local mode works without paid services. Hosted providers, GitHub, Redis, and deployment systems
   are optional adapters until their milestone is accepted.
7. All expensive or long-running work has a timeout, retry limit, cancellation path, and bounded output.

## M2 — AI remediation loop

Detailed implementation spec: [M2 AI Remediation Loop](specs/M2_AI_REMEDIATION_LOOP.md).

### Objective

Turn a persisted finding into a bounded diagnosis, patch proposal, independently verified result, and
optional pull request.

### Deliverables

- `DiagnosisInput`, `AgentRun`, `PatchArtifact`, and `RemediationResult` contracts.
- Provider-neutral diagnosis and patch ports with deterministic recorded/mock and optional live adapters.
- LangGraph state machine for gather context → diagnose → plan → patch → verify → risk check → publish.
- Context builder that includes diff, finding, evidence, selected graph context, tests, and measurements
  under file, byte, and token budgets.
- Patch validator that enforces expected base SHA, repository boundaries, file/line budgets, and secret/
  workflow/binary-file rejection.
- User-facing `lou remediate --run <id>` and API composition using the existing `FixVerifier`.
- Trusted PR publisher in dry-run mode first, then GitHub App mode.

### Acceptance

- A recorded mock completes the full loop without network access in under 120 seconds on the fixture.
- A live provider response is normalized to the same contract as the mock, or returns typed `abandoned`.
- At most three patch attempts occur; repeated or unchanged patches stop the run.
- A patch is applied only in a fresh worktree and the verifier runs the exact selected workloads again.
- Failed or inconclusive verification cannot produce an `open_pr` decision.
- Dry-run publishing produces the branch name, diff hash, report, and intended PR body without credentials.
- Unit tests cover provider failure, malformed patch, budget exhaustion, rejected patch, retry, and approval.

### Dependencies and gate

Depends on M1, existing AD/EV contracts, and EV-007. Gate: good and deliberately bad recorded patches
must produce the expected improvement and failure classifications.

## M3 — Repository graph and semantic index

### Objective

Build a versioned, incremental code graph that links definitions, references, tests, APIs, schemas, data
stores, commits, ownership, and runtime workload entry points.

### Deliverables

- Tree-sitter language adapters with a documented support matrix and parser version.
- SCIP import adapter when available, with native AST/lexical fallback when it is not.
- Stable `GraphNode`, `GraphEdge`, `IndexSnapshot`, and unresolved-relationship contracts.
- Incremental indexing keyed by commit SHA and toolchain revision; unchanged files are reused.
- NetworkX query API for callers/callees, tests, endpoints, data dependencies, and bounded traversal.
- Deterministic JSON snapshot and optional PostgreSQL persistence for index metadata and artifact hashes.

### Acceptance

- The same repository commit produces byte-identical graph snapshots on two clean environments.
- A changed symbol returns direct callers, callees, tests, endpoints, and data dependencies with reasons.
- Every traversal enforces depth and node budgets and reports truncation explicitly.
- At least Python and one additional language have parser contract tests; unsupported languages return a
  typed unsupported result.
- Incremental indexing changes only affected files and records index duration and parser revisions.
- Fixtures cover definitions, aliases, decorators, dynamic/unresolved relationships, and renames.

### Dependencies and gate

Builds on RI-001–RI-006. Gate: graph-required workloads remain selected even when semantic indexing is
disabled or incomplete.

## M4 — Impact prediction

### Objective

Predict the blast radius and required validation before executing a candidate change, then measure
prediction quality against observed evidence.

### Deliverables

- `ImpactPrediction` contract containing predicted nodes, relationships, workloads, risk signals,
  confidence, omitted context, and model revision.
- Deterministic heuristic predictor using graph reachability, centrality, changed-file type, history,
  coverage, and ownership.
- Evaluator that compares predicted versus observed symbols, tests, services, and runtime paths.
- Precision, recall, false-negative, and calibration reports by repository and analyzer revision.
- CLI/API output that clearly separates prediction from observed verification.

### Acceptance

- Predictions are recorded before candidate execution and cannot be edited afterward.
- Evaluation labels every predicted and observed item as true positive, false positive, or false negative.
- CI fails if fixture precision or recall falls below thresholds defined in a checked-in policy file.
- Low-confidence and incomplete graphs increase required validation; they never create false certainty.
- A baseline evaluator and a simple changed-file baseline are included for comparison.

### Dependencies and gate

Depends on M3 and M1. Gate: two benchmark repositories show a documented improvement over changed-file-only
selection without dropping required workloads.

## M5 — Risk model and policy engine

### Objective

Make debt value, remediation danger, confidence, and permitted autonomy transparent and policy-bounded.

### Deliverables

- Versioned `DebtAssessment`, `RemediationRisk`, and `AutonomyDecision` contracts.
- Principal/interest scoring from churn, complexity, centrality, incidents, performance, ownership,
  criticality, coverage, reversibility, and verification strength.
- OPA policy adapter with a local deterministic policy implementation and policy revision recording.
- Explainable feature contributions, missing-input reasons, confidence intervals, and expected-value output.
- A0–A5 autonomy policy with the initial product ceiling at A3 (open PR; no auto-merge/deploy).

### Acceptance

- Scores stay in documented ranges and are deterministic for identical inputs.
- Missing context lowers confidence or autonomy and is visible in the report.
- Schema/data migrations, security-sensitive files, and low-coverage changes cannot be marked trivially safe.
- Failed or inconclusive verification cannot select an autonomy level that publishes a PR.
- Policy tests cover every autonomy ceiling, override, deny rule, and revision mismatch.

### Dependencies and gate

Can proceed alongside M4, but consumes M2 verification results. Gate: a policy test suite proves no agent
or caller can bypass the organizational ceiling.

## M6 — Adaptive validation

### Objective

Select the smallest validation plan that provides sufficient confidence for each predicted impact and risk.

### Deliverables

- `ValidationPlan` contract with required tests, workloads, static analyzers, resource budgets, and reasons.
- Planner combining graph impact, prediction confidence, changed files, historical failures, coverage,
  criticality, and policy.
- Registry adapters for pytest, k6, Semgrep/native analyzers, and future repository-specific commands.
- Budget optimizer that reports what was omitted and why.
- Evaluation comparing adaptive selection with full-suite and changed-file baselines.

### Acceptance

- Every selected command comes from a trusted registry; no model-generated shell command is executed.
- The plan is deterministic for identical repository/index/policy inputs.
- Required graph paths are never omitted by a budget optimization.
- Planner timeout and budget exhaustion return a usable bounded plan or `inconclusive`.
- Benchmark tests measure runtime saved and missed-impact rate against the full-suite baseline.

### Dependencies and gate

Depends on M3–M5. Gate: adaptive validation reduces work on unchanged paths while preserving all known
fixture regressions.

## M7 — Runtime correlation and observability

### Objective

Connect runtime behavior to repository symbols, commits, workloads, and evidence without exposing secrets.

### Deliverables

- OpenTelemetry instrumentation for request, database-query, workload, sandbox, and verification spans.
- Correlation IDs linking trace, analysis run, commit, workload, and artifact.
- Symbol mapping from runtime spans to graph nodes with explicit unresolved mappings.
- Redaction, sampling, retention, and local collector configuration.
- Report sections showing runtime evidence and correlation confidence.

### Acceptance

- A fixture request produces trace IDs and query spans that link to the expected symbol and workload.
- Trace export failure never blocks deterministic verification; it records missing observability.
- Secrets, tokens, request bodies, and sensitive headers are absent from exported evidence.
- Correlation tests cover renamed symbols, missing symbols, sampling, and collector outage.
- Trace payload and retention limits are enforced in code and tested.

### Dependencies and gate

Depends on M1 and M3. Gate: a local collector rehearsal links one measured regression to code and commit
without requiring a hosted observability service.

## M8 — Staging and production loop

### Objective

Use verified changes in staging/canary environments with explicit promotion and rollback decisions.

### Deliverables

- Deployment adapter and contracts for release, environment, canary, observation window, promotion,
  rollback, and deployment evidence.
- Argo Rollouts adapter; no custom deployment controller.
- Staging verification gate using the M6 validation plan and M7 telemetry.
- SLO/error-budget policy checks and bounded observation windows.
- Rollback command and immutable record of who/what/policy initiated it.

### Acceptance

- A canary can be promoted, paused, or rolled back using a deterministic policy fixture.
- SLO breach and missing telemetry produce pause/rollback, never silent promotion.
- Deployment retries are idempotent by release ID and commit SHA.
- Production credentials are accessible only to the trusted deployment adapter.
- A complete deployment report links commit, analysis, verification, traces, policy, and outcome.

### Dependencies and gate

Depends on M5–M7. Gate: staging-only rehearsal proves promote and rollback paths; production access is not
required for acceptance.

## M9 — Outcome learning and calibration

### Objective

Learn from observed outcomes while preserving transparent baselines and preventing unvalidated model drift.

### Deliverables

- `OutcomeRecord` linking prediction, decision, agent action, verification, deployment, and production result.
- Offline evaluation jobs for calibration, precision/recall, expected-value error, and drift.
- Time-windowed datasets with schema and feature revisions.
- Dashboards/reports for false positives, false negatives, regressions after fixes, rollback causes, and
  confidence calibration.
- Model promotion policy requiring comparison with transparent heuristic baselines.

### Acceptance

- Every production-capable decision has a linked outcome or an explicit pending/unknown state.
- Metrics are reproducible from versioned snapshots and do not mutate historical records.
- A new predictor cannot be promoted unless it beats the baseline on a checked-in evaluation set.
- Drift and insufficient sample size lower confidence and trigger review rather than automatic policy changes.
- Privacy and retention tests verify deletion/redaction rules without breaking aggregate metrics.

### Dependencies and gate

Depends on M4, M5, M7, and M8. Gate: an offline fixture dataset demonstrates calibration reporting and a
deliberately worse predictor is rejected.

## M10 — Technical-debt portfolio

### Objective

Prioritize remediation across repositories using engineering capacity, compute budget, risk limits, and
expected return.

### Deliverables

- Multi-repository identities, teams, ownership, criticality, and access boundaries.
- `DebtItem`, `PortfolioConstraint`, `RemediationPlan`, and `PortfolioDecision` contracts.
- Prioritizer using debt avoided, remediation effort, verification cost, regression risk, and urgency.
- Budget-aware scheduler with fairness, dependencies, and pause/resume state.
- Portfolio report with ranked items, rationale, confidence, budget consumption, and realized outcomes.

### Acceptance

- The same portfolio inputs and policy produce the same ranked plan.
- The scheduler never exceeds engineering, compute, risk, or repository-access budgets.
- Cross-repository data is isolated by tenant and authorization boundary.
- A portfolio recommendation links each item to evidence and an explainable expected-value calculation.
- Simulated outcomes update future ranking only through versioned, reviewable learning inputs.

### Dependencies and gate

Depends on M5, M8, and M9. Gate: a multi-repository simulation demonstrates budget compliance, deterministic
ranking, and outcome-linked reprioritization.

## Cross-cutting implementation sequence

1. Freeze contracts and migrations before each milestone; add fixtures before adapters.
2. Finish M2 with the existing local fixture, then generalize repository intake and graph indexing in M3.
3. Add workers/Redis only when M2 or M8 execution exceeds API request limits; do not introduce them earlier.
4. Keep PostgreSQL as the system of record, but add tables only when a milestone has a durable contract.
5. Run each gate in a clean clone and retain command, commit, dependency, resource, and artifact hashes.
6. Keep GitHub, hosted AI, OTel collectors, OPA, Argo, and React behind optional adapters until their
   milestone acceptance tests pass locally.

## Definition of done for every milestone

- A versioned spec and contract fixtures are committed.
- Unit tests cover valid, invalid, boundary, timeout, retry, and `inconclusive` behavior.
- An integration rehearsal exercises the real composition with disposable infrastructure where required.
- Security boundaries and credential handling are tested.
- The README documents local setup, cost, limits, and a reproducible verification command.
- Evidence and artifacts are persisted with hashes and can be read after process restart.
- The task board records the milestone status, known gaps, and the next integration gate.
