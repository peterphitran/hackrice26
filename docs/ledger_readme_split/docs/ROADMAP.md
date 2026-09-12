# Roadmap

## M0 — Benchmark Repository

Build a deliberately broken demo app.

Recommended stack:

```text
FastAPI
PostgreSQL
Redis
```

Potential issues:

```text
N+1 query
slow endpoint
low coverage
bad caching
blocking I/O
memory issue
```

## M1 — Runtime Regression Detection

```text
PR
 ↓
build
 ↓
tests
 ↓
run app
 ↓
k6
 ↓
detect regression
 ↓
GitHub check
```

Goal:

> Detect a real problem ordinary tests miss.

## M2 — AI Remediation Loop

```text
failure
 ↓
diagnosis
 ↓
patch
 ↓
re-run exact workload
 ↓
compare before / after
 ↓
open PR
```

Goal:

> Demonstrate that Ledger can fix and verify a problem.

## M3 — Repository Graph

Add:

```text
Tree-sitter
SCIP
NetworkX
```

Use the graph for:

```text
better context
affected symbols
affected tests
blast radius
```

## M4 — Risk Model

Add:

```text
Debt Risk
Remediation Risk
Confidence
Autonomy Level
```

## M5 — Adaptive Validation

Use repository graph context to determine which tests and load scenarios should run.

## M6 — Runtime Correlation

Add:

```text
OpenTelemetry
runtime-to-code correlation
trace evidence
```

## M7 — Production Loop

Later:

```text
staging
 ↓
load test
 ↓
canary
 ↓
observe
 ↓
continue / rollback
```

Use Argo Rollouts rather than building a custom deployment controller.

## M8 — Learning

Store:

```text
predicted impact
actual impact
decision
agent action
production outcome
```

Then calibrate future predictions.

## M9 — Technical Debt Portfolio

Eventually allow organizations to provide:

```text
engineering time budget
compute budget
maximum remediation risk
```

Ledger chooses the debt items with the highest expected return.

## What Not to Build Yet

Avoid spending MVP time on:

```text
Neo4j
Kubernetes
Temporal
auto-deploy
auto-merge
custom static-analysis engines
custom deployment controllers
multi-language indexing
complex ML models
full enterprise authentication
large dashboard suites
```

## Recommended Build Order

```text
1. Broken demo repository
2. Docker sandbox
3. Build + tests
4. k6 baseline / candidate comparison
5. GitHub webhook + check
6. Semgrep
7. LangGraph remediation loop
8. Patch verification
9. React PR analysis page
10. Repository graph
11. Risk scoring
12. OpenTelemetry
13. Policy engine
14. Production feedback
```
