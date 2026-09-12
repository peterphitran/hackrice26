# Architecture

## Architectural Layers

Ledger is organized into six logical layers:

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

## Suggested Monorepo

```text
ledger/
├── README.md
├── apps/
│   ├── api/
│   ├── web/
│   └── cli/
│
├── ledger/
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
├── scripts/
└── docs/
```
