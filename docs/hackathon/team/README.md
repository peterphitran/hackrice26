# Hackathon Team Assignments

Each engineer has a separate execution README. Assign names before implementation begins.

| Engineer | Mission | Assignment |
|---|---|---|
| Engineer 1 | Turn code changes into graph-backed impact and workload selections | [Repository Intelligence](engineer-1/README.md) |
| Engineer 2 | Turn evidence into bounded patches, risk scores, and autonomy decisions | [Agents and Decisions](engineer-2/README.md) |
| Engineer 3 | Produce trustworthy baseline/candidate/fix measurements | [Execution and Verification](engineer-3/README.md) |
| Engineer 4 | Provide contracts, persistence, CLI/API wiring, and the final report | [Platform and Product](engineer-4/README.md) |

The [master task board](../TASKS.md) is the source of truth for task definitions and acceptance criteria. Individual READMEs define personal order, owned paths, and handoffs. If the documents disagree, update the master board and affected assignment README in the same pull request.

## Shared Start

All four engineers participate in SH-001 and approve SH-002 before implementation branches diverge.

```text
SH-001  Agree on the fixture, regression signal, threshold, and expected fix
SH-002  Freeze version-one contracts and JSON examples
SH-003  Freeze ports, paths, and environment names
SH-004  Freeze artifact layout and URI rules
```

## Integration Leadership

| Gate | Lead | Required support |
|---|---|---|
| INT-001 Contract compatibility | Engineer 4 | Engineers 1–3 validate their producers/consumers |
| INT-002 Runtime regression detection | Engineer 3 | Engineer 1 connects graph selection; Engineer 4 connects persistence/reporting |
| INT-003 Agent remediation | Engineer 2 | Engineer 3 owns verification; Engineer 4 persists outcomes |
| INT-004 Reproducibility | Engineer 1 | All engineers test from clean environments |
| INT-005 Presentation | Engineer 4 | All engineers rehearse and prepare fallbacks |

## Conflict-Free Ownership

```text
Engineer 1
backend/lou/repository/
backend/lou/analyzers/
backend/tests/unit/repository/
backend/tests/unit/analyzers/

Engineer 2
backend/lou/agents/
backend/lou/scoring/
backend/lou/decision/
backend/lou/policies/
backend/tests/unit/agents/
backend/tests/unit/scoring/
backend/tests/unit/decision/

Engineer 3
backend/lou/sandbox/
backend/lou/execution/
backend/lou/loadtest/
backend/lou/verification/
backend/lou/evidence/
backend/lou/telemetry/
backend/fixtures/
backend/tests/integration/execution/

Engineer 4
backend/contracts/
backend/lou/core/
backend/lou/ingestion/
backend/lou/persistence/
backend/apps/
backend/workers/
backend/tests/integration/persistence/
backend/tests/integration/api/
```

Engineer 4 owns the base Compose file and PostgreSQL service. Engineer 3 owns fixture Dockerfiles and sandbox execution configuration. Coordinate any change that crosses this boundary.

## Working Agreement

- Use task IDs in branch names and pull-request titles, such as `ri-003-repository-graph`.
- Merge shared contracts and serialized examples before concrete adapters.
- Use contract fixtures or fakes when an upstream workstream is incomplete.
- Do not import another workstream's private implementation modules.
- Keep pull requests small enough to integrate throughout the hackathon.
- No stretch task begins before INT-002 passes reliably.
