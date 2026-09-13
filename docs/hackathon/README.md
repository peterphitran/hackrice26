# Lou Hackathon Plan

Lou is a local-first, evidence-driven CI remediation prototype. The hackathon goal is to demonstrate one complete intelligent loop on one repository, language, and runtime regression.

> Lou predicts what a code change affects, selects the relevant validation, detects a regression, proposes a bounded repair, and proves whether the repair worked.

## Execution Documents

- [Team Task Board](TASKS.md) — owners, dependencies, acceptance checks, integration gates, and parallel work plan
- [Engineer Assignments](team/README.md) — four separate execution READMEs with owned paths and handoffs
- [PostgreSQL Schema Plan](DATABASE_SCHEMA.md) — MVP relationships, DDL, indexes, and persistence lifecycle

## Demo Story

The benchmark repository is a deliberately vulnerable or inefficient FastAPI store. A candidate commit introduces an N+1 query in the checkout path while its ordinary unit tests continue to pass.

```text
Known-good commit
      ↓
Candidate commit changes checkout()
      ↓
Diff parser identifies changed symbols
      ↓
Repository graph connects checkout to its endpoint, database, and tests
      ↓
Lou selects the checkout workload
      ↓
Repeated k6 runs show a meaningful p95 or query-count regression
      ↓
Agent receives bounded source, graph, and runtime evidence
      ↓
Agent creates a minimal patch in a temporary worktree
      ↓
Lou repeats the same verification
      ↓
Baseline → candidate → fix report proves or rejects improvement
```

This single scenario demonstrates repository intelligence, graph-guided validation, runtime evidence, agent remediation, differential verification, risk-aware autonomy, and prediction-outcome recording.

## Definition of Done

The hackathon prototype is complete when it can repeatedly:

1. accept a local repository and two commit SHAs;
2. detect the changed Python symbols;
3. select a relevant checked-in test and k6 workload;
4. run baseline and candidate builds under equivalent limits;
5. detect a regression that the unit suite misses;
6. preserve structured evidence for the finding;
7. generate or apply a minimal patch in an isolated worktree;
8. rerun the same verification against the fix;
9. explain the debt risk, remediation risk, confidence, and allowed action; and
10. produce a local report without requiring a hosted service.

GitHub checks, pull requests, and the web UI improve the presentation, but the local loop is the source of truth.

## Scope Boundaries

### Required

```text
Python repository support
Git diff and changed-symbol extraction
small typed repository graph
fixed benchmark repository
pytest
k6
Semgrep or one native static analyzer
baseline / candidate / fix comparison
one configurable LLM adapter
PostgreSQL evidence store
CLI or local API entry point
```

### Optional if time permits

```text
GitHub App and webhook
React results page
LangGraph visualization
OpenTelemetry collector and local trace UI
semantic embeddings
OPA policy evaluation
Redis and Celery
```

### Explicitly deferred

```text
multi-language indexing
complete SCIP integration
Kubernetes
Temporal
Neo4j
gVisor or Firecracker
auto-merge or auto-deploy
online model training
production canaries
enterprise authentication
technical-debt portfolio optimization
```

## Local-First Stack

| Concern | Hackathon choice | Notes |
|---|---|---|
| Language | Python 3.13+ | One supported language keeps graph extraction bounded |
| API | FastAPI | Local REST API and optional webhook receiver |
| CLI | Typer + Rich | Primary reliable demo interface |
| Database | PostgreSQL in Docker Compose | No managed database required |
| Repository structure | Python AST or Tree-sitter | SCIP is deferred unless setup is already working |
| Graph | NetworkX | Build in memory and optionally persist snapshots |
| Static analysis | Semgrep + pytest/coverage | Consume existing tools instead of writing analyzers |
| Execution | Docker | Controlled local fixture repository only |
| Performance | k6 OSS | Run checked-in scripts locally |
| Agent workflow | Plain Python or LangGraph library | Hosted LangSmith services are not required |
| LLM | Provider adapter | Support a capped API key, local model, or recorded mock response |
| Observability | Structured logs first | Local OpenTelemetry is optional |

## Local Architecture

```text
Developer laptop
│
├── lou CLI / FastAPI
│     ├── ingestion
│     ├── repository graph
│     ├── scoring and decisions
│     └── agent orchestration
│
├── PostgreSQL container
│     └── runs, evidence, predictions, decisions, outcomes
│
├── isolated fixture containers
│     ├── baseline
│     ├── candidate
│     └── proposed fix
│
├── pytest / Semgrep / k6
│
└── optional LLM
      ├── local model
      ├── capped hosted API
      └── recorded deterministic response
```

The initial implementation may execute one analysis synchronously. Add Redis and Celery only when the API needs to return immediately while jobs continue in the background.

## Target Local Commands

These commands describe the intended developer experience; they do not imply that the implementation already exists.

```bash
docker compose -f backend/infra/compose.yaml up -d postgres

lou analyze \
  --repo backend/fixtures/broken-store \
  --base good \
  --candidate n-plus-one

lou report --latest
```

The same application service should power both the CLI and future HTTP endpoints.

## Repository Graph Scope

Use only the relationships needed by the demo.

```text
Nodes
FILE
FUNCTION
CLASS
TEST
ENDPOINT
DATABASE_TABLE
LOAD_SCENARIO

Edges
DEFINES
CALLS
IMPORTS
TESTED_BY
SERVES_ENDPOINT
READS_FROM
WRITES_TO
VALIDATED_BY
```

For a changed symbol, graph traversal selects direct callers, callees, tests, endpoints, database dependencies, and load scenarios. Semantic retrieval may add conceptually similar implementations or past fixes, but it must not displace structurally required context.

RI-007 implements semantic retrieval v1 as a bounded local lexical index over immutable Python Git
blobs. SCIP and embedding implementations remain future optional backends behind the same narrow
retrieval boundary; neither is required for the hackathon path.

## Context Bundle

The agent receives a deliberately small bundle:

```text
changed diff
finding and raw evidence summary
changed function
direct callers and callees
relevant interface or repository method
selected tests
selected workload
baseline and candidate measurements
explicit unresolved relationships
```

Record why each item was included. Missing graph relationships lower confidence rather than implying that no dependency exists.

## Transparent Scoring

Hackathon scores are explainable heuristics, not learned financial estimates.

```text
principal =
  0.35 × normalized complexity
+ 0.35 × coverage deficit
+ 0.30 × estimated patch size

interest = principal × (
  0.30 × churn
+ 0.25 × graph centrality
+ 0.25 × observed runtime impact
+ 0.20 × path criticality
)
```

Show the underlying features beside every score. Use debt points rather than dollars or precise engineering hours.

## Risk and Autonomy

The demo supports four actions:

```text
A0  Report only
A1  Recommend a fix
A2  Generate a local patch
A3  Open a pull request through a trusted service
```

Autonomy depends on blast radius, criticality, coverage, reversibility, verification strength, policy, and confidence. The agent never receives GitHub write credentials and cannot auto-merge.

## Prediction and Learning Record

Before execution, store the predicted affected tests, endpoints, services, and expected regression category. After execution, store what actually failed or changed.

```text
Prediction
checkout latency will regress
affected test: test_checkout
affected endpoint: /checkout

Outcome
unit tests passed
/checkout p95 increased 340%
database query count increased per cart item

Evaluation
endpoint prediction correct
runtime category correct
unit-test prediction incorrect or incomplete
```

The hackathon does not train a model. It proves that Lou creates falsifiable prediction-outcome records that can support later calibration.

## Cost Guardrails

- Run PostgreSQL, the benchmark application, k6, and analysis tools locally.
- Do not require managed databases, hosted observability, cloud load testing, or paid CI runners.
- Put LLM access behind one interface with local, hosted, and deterministic mock implementations.
- Set per-run token, retry, time, CPU, and memory budgets.
- Cache repository parsing and baseline results by commit SHA.
- Store large logs and profiles as local artifacts; keep summaries and hashes in PostgreSQL.
- Make GitHub integration optional so loss of network access cannot break the core demo.

## Recommended Build Order

The actionable, owner-assigned version of this sequence is maintained in the [Team Task Board](TASKS.md).

```text
1. Broken FastAPI fixture with known-good and N+1 commits
2. Repeatable pytest and k6 baseline/candidate runner
3. PostgreSQL run, evidence, and verification records
4. Diff parser and changed-symbol extraction
5. Small NetworkX graph and workload selection
6. Structured regression finding and evidence report
7. Agent context bundle and patch generation
8. Fix verification and decision output
9. Local results page
10. Optional GitHub check and remediation PR
```

## Demo Failure Fallbacks

- Keep a recorded LLM patch response if the model endpoint is unavailable.
- Cache baseline measurements and also retain a command to reproduce them live.
- Use deterministic fixture data and fixed container resource limits.
- Keep the CLI demo functional if the frontend or GitHub webhook fails.
- Mark noisy performance results inconclusive instead of claiming improvement.

The scope rule is simple:

> One language, one repository, one regression, one bounded fix, and one undeniable before-and-after result.
