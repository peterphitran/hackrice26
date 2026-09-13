# Engineer 1 — Repository Intelligence

Assigned to: Steven Dau

## Mission

Transform an immutable base/candidate Git comparison into structured changed symbols, a typed repository graph, an explainable impact set, and deterministic workload selections.

Your output answers:

```text
What changed?
What is structurally connected to it?
Which tests and load scenarios should run?
How complete and trustworthy is that context?
```

The detailed acceptance criteria live in the [master task board](../../TASKS.md).

## Owned Paths

```text
backend/lou/repository/
backend/lou/analyzers/
backend/tests/unit/repository/
backend/tests/unit/analyzers/
```

Do not implement sandbox commands, agent prompts, scoring policy, persistence, or API routes in these modules.

## Assigned Tasks

| Task | Priority | Depends on | Deliverable |
|---|---|---|---|
| RI-001 | P0 | SH-002 | Base/candidate diff parsed into `RepositoryChange` |
| RI-002 | P0 | RI-001 | Changed Python symbols with stable keys and source ranges |
| RI-003 | P0 | SH-002 | Typed NetworkX repository graph and deterministic JSON snapshot |
| RI-004 | P0 | RI-002, RI-003 | Budgeted impact traversal producing `RepositoryContext` |
| RI-005 | P0 | RI-004 | Ordered `WorkloadSelection` results with reasons |
| RI-006 | P0 | SH-002 | One analyzer normalized into `Finding` and `Evidence` |
| RI-007 | P2 | RI-004 | Optional bounded semantic/lexical retrieval |
| INT-004 | P0 lead | INT-003 | Clean-clone and cross-machine reproducibility gate |

## Execution Order

### Phase 1 — Change extraction

- [ ] RI-001: parse added, modified, deleted, and renamed Python files.
- [ ] RI-002: map changed lines to functions, methods, classes, or module scope.
- [ ] Publish representative `RepositoryChange` fixtures for downstream engineers.

### Phase 2 — Graph and impact

- [ ] RI-003: create only the node and edge types required by the demo.
- [ ] Include confidence and unresolved relationships in graph extraction.
- [ ] RI-004: traverse callers, callees, tests, endpoints, data dependencies, and scenarios.
- [ ] Enforce depth and node-count budgets.

### Phase 3 — Selection and findings

- [ ] RI-005: select checkout pytest and k6 workloads from checked-in definitions.
- [ ] Provide a deterministic fallback when graph extraction is incomplete.
- [ ] RI-006: normalize one static analyzer without coupling runtime verification to it.

### Phase 4 — Reproducibility

- [ ] Help wire repository output into INT-002.
- [ ] Lead INT-004 on a second teammate machine or clean environment.
- [ ] Start RI-007 only after the regression-detection gate is stable.

## Inputs You Consume

From Engineer 4:

```text
AnalysisJob
contract version
repository path
base commit SHA
candidate commit SHA
workload registry fixture
```

From Engineer 3:

```text
fixture source layout
test and load-scenario paths
runtime components that should appear in the graph
```

## Outputs You Hand Off

To Engineer 2:

```text
RepositoryChange
RepositoryContext
selection reasons
unresolved relationships
graph completeness/confidence
```

To Engineer 3:

```text
WorkloadSelection
exact checked-in workload identifiers
expected phase and commit identity
```

To Engineer 4:

```text
serializable graph snapshot
changed-symbol summary
impact summary
normalized findings/evidence
```

## Interface Rules

- Paths are repository-relative and use one normalized separator.
- Symbol keys are stable across repeated extraction of the same commit.
- Traversal returns selection reasons, not only node IDs.
- Empty results and incomplete results are different states.
- The graph never invents an edge because an LLM says it probably exists.
- Workload selection returns registry IDs; it never constructs shell commands.

## Personal Definition of Done

- [ ] `checkout()` maps to the expected symbol key.
- [ ] The graph connects checkout to its test, endpoint, data dependency, and load scenario.
- [ ] The correct workload is selected from identical input every time.
- [ ] Broken or unsupported syntax lowers completeness without crashing the run.
- [ ] Unit tests cover empty diffs, renames, duplicate method names, and traversal limits.
- [ ] Downstream engineers can develop using your committed JSON fixtures.
- [ ] INT-004 passes on at least two machines.

## Stretch Work

RI-007 may add lexical or embedding-based retrieval. It must remain optional, local-capable, budgeted, and supplemental to graph-required context.
