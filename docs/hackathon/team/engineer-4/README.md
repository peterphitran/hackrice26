# Engineer 4 — Platform and Product

Assigned to: ____________________

## Mission

Provide the stable contracts and local platform that connect every workstream, persist the evidence lifecycle, expose the CLI/API, and present the final before-and-after story.

Your output answers:

```text
How does a run start and retain identity?
Where are results and artifacts recorded?
How do modules integrate without tight coupling?
How does a judge understand the result?
```

The detailed acceptance criteria live in the [master task board](../../TASKS.md), and table design lives in the [database schema plan](../../DATABASE_SCHEMA.md).

## Owned Paths

```text
backend/contracts/
backend/lou/core/
backend/lou/ingestion/
backend/lou/persistence/
backend/apps/api/
backend/apps/cli/
backend/workers/
backend/tests/integration/persistence/
backend/tests/integration/api/
```

You own the base Compose file and PostgreSQL service under `backend/infra/`. Engineer 3 owns fixture Dockerfiles and sandbox execution configuration.

## Assigned Tasks

| Task | Priority | Depends on | Deliverable |
|---|---|---|---|
| SH-001 | P0 recorder | none | Agreed fixture/regression/demo contract |
| SH-002 | P0 lead | SH-001 | Versioned Pydantic contracts and JSON fixtures |
| SH-003 | P0 | SH-001 | Approved ports, paths, environment names, and defaults |
| SH-004 | P0 co-owner | SH-002 | Artifact convention aligned with persistence |
| PF-001 | P0 | SH-003 | Reproducible Python project tooling |
| PF-002 | P0 | SH-003 | Local PostgreSQL, SQLAlchemy, Alembic, and first migration |
| PF-003 | P0 | PF-002, SH-002, SH-004 | Run/evidence persistence repositories |
| PF-004 | P0 | PF-001, SH-002 | `lou analyze` and `lou report` CLI |
| PF-005 | P0 | All P0 result contracts | Terminal and saved evidence report |
| PF-006 | P1 | PF-003, PF-004 | FastAPI analysis endpoints |
| PF-007 | P2 | PF-006 | Optional single results page |
| PF-008 | P2 | PF-006, AD-006, EV-007 | Optional trusted GitHub integration |
| INT-001 | P0 lead | Initial producer/consumer tasks | Contract compatibility gate |
| INT-005 | P1 lead | INT-004 | Presentation and fallback gate |

## Execution Order

### Phase 1 — Shared foundation

- [ ] Record SH-001 decisions in the hackathon README or fixture specification.
- [ ] Lead SH-002 and obtain sign-off from all producers and consumers.
- [ ] SH-003: freeze environment variable names, ports, and configurable paths.
- [ ] Co-author SH-004 with Engineer 3.
- [ ] PF-001: make setup, lint, type-check, and test commands reproducible.

### Phase 2 — Persistence

- [ ] PF-002: start PostgreSQL locally and create the first vertical-slice migration.
- [ ] Implement `repositories`, `analysis_runs`, `workloads`, `verification_runs`, `findings`, and `evidence` first.
- [ ] PF-003: expose repository interfaces independent of SQLAlchemy sessions.
- [ ] Validate idempotency and lifecycle transitions.

### Phase 3 — CLI and report

- [ ] PF-004: build the CLI against recorded contract fixtures first.
- [ ] PF-005: show changed symbols, selection reasons, findings, evidence, measurements, risks, and decisions.
- [ ] Lead INT-001 and repair contract mismatches before integrating implementations.
- [ ] Support Engineer 3 during INT-002 persistence/report wiring.

### Phase 4 — Presentation

- [ ] Add prediction, agent, patch, decision, and outcome persistence as those stages become real.
- [ ] Lead INT-005 and maintain both live and recorded report paths.
- [ ] Start PF-006 only after the CLI end-to-end path works.
- [ ] Start PF-007 or PF-008 only after INT-004 passes.

## Inputs You Consume

From Engineer 1:

```text
RepositoryChange
RepositoryContext
WorkloadSelection
graph snapshot and completeness
normalized static findings
```

From Engineer 2:

```text
AgentResult
PatchArtifact metadata
debt and remediation-risk features
LouDecision and rationale
```

From Engineer 3:

```text
VerificationResult by phase
Finding and Evidence
artifact URIs and hashes
execution status and measurements
```

## Outputs You Hand Off

To all engineers:

```text
versioned Pydantic contracts
serialized JSON examples
application configuration
run IDs and lifecycle rules
persistence interfaces/fakes
local setup and test commands
```

To judges/users:

```text
one-command entry point
stage progress
saved evidence report
clear verified/failed/inconclusive result
optional API/UI/GitHub presentation
```

## Interface Rules

- CLI and API call the same application service.
- Pydantic API contracts and SQLAlchemy persistence models remain separate.
- Domain services receive repository interfaces, not database sessions.
- Every run is keyed by repository and immutable commit SHAs.
- Duplicate requests return or resume the existing run.
- Large output stays in hashed artifacts rather than JSONB.
- The trusted publisher is the only component allowed to use GitHub write credentials.

## Personal Definition of Done

- [ ] Clean-clone setup works with documented commands.
- [ ] PostgreSQL starts locally with no managed account.
- [ ] Alembic upgrades an empty database and downgrades cleanly.
- [ ] Contract fixtures pass every producer/consumer test.
- [ ] Run state and evidence survive process restart.
- [ ] CLI can analyze and report without FastAPI, GitHub, or a frontend.
- [ ] Report presents baseline → candidate → fix with evidence and confidence.
- [ ] Optional service failure cannot break the core local demo.

## Stretch Work

Implement PF-006, PF-007, and PF-008 in that order. A working API is more valuable than a disconnected UI, and GitHub integration is valuable only after trusted local publication rules are proven.
