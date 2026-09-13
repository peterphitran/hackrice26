# Architecture

## Architectural Layers

Lou is organized into six logical layers:

```text
1. Integration
   GitHub App / webhooks / CLI

2. Repository Intelligence
   parsing / semantic indexing / graph / history

3. Analysis & Execution
   static analysis / build / tests / runtime / load testing

4. Decision Engine
   debt / risk / prioritization / autonomy

5. Agent Layer
   diagnosis / planning / patching / review

6. Feedback & Learning
   verification / deployment / outcomes / calibration
```

## Main Flow

```text
GitHub PR
   ↓
FastAPI
   ↓
LangGraph
   ↓
Repository context
   ↓
Analysis + sandbox execution
   ↓
Evidence
   ↓
Decision engine
   ↓
Agent remediation
   ↓
Independent verification
   ↓
GitHub result
```

## LangGraph Responsibility

LangGraph should coordinate:

- state
- branching
- retries
- remediation loops
- human interrupts
- checkpointing

It should not implement:

- static analysis
- graph analysis
- Docker execution
- Git operations
- k6
- scoring rules

Those remain independent services/modules.

## Trusted vs Untrusted Boundaries

```text
                TRUSTED CONTROL PLANE

GitHub App
FastAPI
PostgreSQL
LangGraph
Policy Engine

                      │
                      ▼

               UNTRUSTED EXECUTION

Repository
Build
Tests
Application
Agent tools
k6
Static analyzers

                      │
                      ▼

                TRUSTED CONTROL PLANE
```

The execution environment returns structured results and patches only.

## Control Plane Contract

The trusted control plane sends an immutable job specification to an untrusted runner. At minimum it identifies:

```text
repository installation
base commit SHA
candidate commit SHA
tool and policy revisions
resource and network limits
required verification plan
artifact and trace correlation IDs
```

The runner returns results, evidence references, and a patch. It cannot publish branches, open pull requests, alter policy, or obtain another repository's data. The control plane verifies the response before performing a privileged action.

Webhook delivery and job execution must be idempotent. Use the GitHub delivery ID plus repository and commit identity as a deduplication key, and make retries resume from durable run state rather than silently starting conflicting remediations.

## Software Lifecycle Graph

The long-term graph is broader than a static code graph. It links three views of the system:

```text
Code                    Development               Runtime
functions               commits                   deployments
services                pull requests             traces
APIs and schemas        ownership and reviews     SLOs and incidents
tests and infrastructure CI runs                  rollbacks and cost
       └──────────────────────┬────────────────────────┘
                              ↓
                    Prediction and decisions
```

The graph itself is not the product outcome. Lou uses it to predict debt growth, blast radius, required validation, and remediation risk, then learns from the difference between predicted and observed results.

## Backend Layout

The implemented backend layout is documented in [`../../BACKEND_STRUCTURE.md`](../../BACKEND_STRUCTURE.md). Its top-level shape is:

```text
backend/
├── apps/
│   ├── api/
│   └── cli/
├── lou/
│   ├── core/
│   ├── ingestion/
│   ├── repository/
│   ├── analyzers/
│   ├── sandbox/
│   ├── execution/
│   ├── loadtest/
│   ├── telemetry/
│   ├── evidence/
│   ├── scoring/
│   ├── decision/
│   ├── agents/
│   ├── verification/
│   ├── policies/
│   ├── persistence/
│   └── learning/
│
├── workers/
├── contracts/
├── infra/
├── fixtures/
├── tests/
└── scripts/
```

Frontend code can be added as a separate top-level application when implementation begins; it should consume versioned API contracts rather than importing backend modules.
