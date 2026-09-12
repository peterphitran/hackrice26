# Engineer 3 — Execution and Verification

Assigned to: ____________________

## Mission

Build the deterministic benchmark and produce trustworthy, repeatable evidence for baseline, candidate, and fix phases. You own the verification verdict independently of the agent.

Your output answers:

```text
Did the code build and pass its tests?
Did runtime behavior regress?
Did the proposed fix materially improve it?
Was the experiment reliable or inconclusive?
```

The detailed acceptance criteria live in the [master task board](../../TASKS.md).

## Owned Paths

```text
backend/lou/sandbox/
backend/lou/execution/
backend/lou/loadtest/
backend/lou/verification/
backend/lou/evidence/
backend/lou/telemetry/
backend/fixtures/
backend/tests/integration/execution/
```

Engineer 4 owns the base Compose file and PostgreSQL container. You own fixture Dockerfiles, sandbox images, runtime limits, and execution behavior.

## Assigned Tasks

| Task | Priority | Depends on | Deliverable |
|---|---|---|---|
| SH-004 | P0 co-owner | SH-002 | Artifact directories, URIs, hashes, and retention rules |
| EV-001 | P0 | SH-001 | Seedable broken-store fixture with good/N+1 states |
| EV-002 | P0 | SH-002, SH-004 | Safe structured command execution primitive |
| EV-003 | P0 | EV-001, EV-002 | Controlled local sandbox lifecycle |
| EV-004 | P0 | EV-002, EV-003 | Phase-aware tests and static checks |
| EV-005 | P0 | EV-001, EV-003 | Repeated k6 checkout experiment |
| EV-006 | P0 | EV-004, EV-005 | Baseline/candidate differential result |
| EV-007 | P0 | AD-005, EV-006 | Independent proposed-fix verification |
| EV-008 | P1 | EV-002 | Correlated local structured logs |
| INT-002 | P0 lead | Repository, execution, and platform P0 tasks | Runtime regression detection gate |

## Execution Order

### Phase 1 — Deterministic fixture

- [ ] Co-author SH-004 with Engineer 4.
- [ ] EV-001: build the FastAPI store, fixed dataset, tests, checkout workload, and N+1 patch.
- [ ] Seed a temporary Git repository with stable logical `good` and `n-plus-one` refs.
- [ ] Prove unit tests pass in both states while the runtime signal regresses.

### Phase 2 — Execution foundation

- [ ] EV-002: execute argument arrays with timeout, cancellation, bounded output, and typed failures.
- [ ] EV-003: build create → execute → collect → destroy lifecycle.
- [ ] Verify cleanup after success, failure, timeout, and cancellation.

### Phase 3 — Differential verification

- [ ] EV-004: run identical selected checks for each commit phase.
- [ ] EV-005: warm up and repeat the checkout experiment under equivalent limits.
- [ ] EV-006: calculate absolute values, deltas, thresholds, repetitions, and variance.
- [ ] Classify candidate-only versus shared/pre-existing failures.

### Phase 4 — Fix verification

- [ ] Lead INT-002 with graph selection from Engineer 1 and persistence/reporting from Engineer 4.
- [ ] EV-007: apply Engineer 2's patch to a fresh worktree and repeat exact verification.
- [ ] Test both the expected repair and a deliberately ineffective patch.
- [ ] Add EV-008 only after the primary evidence is stable.

## Inputs You Consume

From Engineer 1:

```text
WorkloadSelection
workload registry ID
selection reason
affected endpoint/data path
```

From Engineer 2:

```text
PatchArtifact
expected base SHA
required verification plan
```

From Engineer 4:

```text
AnalysisJob
versioned execution/evidence contracts
artifact URI rules
persistence interface
```

## Outputs You Hand Off

To Engineer 1:

```text
fixture code relationships
test and workload registry metadata
actual affected runtime path
```

To Engineer 2:

```text
Finding
Evidence summaries
baseline/candidate VerificationResult
fix VerificationResult
```

To Engineer 4:

```text
phase and commit identity
structured command/test results
aggregate measurements
artifact URIs and hashes
verification verdict
```

## Interface Rules

- The same workload definition and meaningful resource limits apply to every phase.
- Tool failure, test failure, detected regression, and noisy/inconclusive result are separate states.
- Raw output is an artifact; PostgreSQL receives summaries and hashes.
- Verification must not trust the agent's claimed result.
- A patch may not disable tests, lower thresholds, or change the workload to pass.
- Cleanup must not depend on a successful command exit.

## Personal Definition of Done

- [ ] Fixture setup is deterministic and requires no external service.
- [ ] Baseline and candidate unit tests both pass.
- [ ] Candidate regression exceeds the agreed threshold reliably.
- [ ] Raw and aggregate results retain phase and commit identity.
- [ ] High variance becomes `inconclusive` instead of a forced verdict.
- [ ] Expected fix returns near baseline without weakening verification.
- [ ] Bad fix fails independent verification.
- [ ] INT-002 works from one local command.

## Stretch Work

EV-008 adds structured logs or optional OpenTelemetry. Observability must remain local and cannot become a prerequisite for collecting verification evidence.
