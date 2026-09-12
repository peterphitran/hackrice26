# Data, Evidence & Learning

## Evidence Model

Ledger should store evidence separately from conclusions.

Example:

```text
Claim:
"Checkout performance regression"

Evidence:
├── changed database query
├── high-centrality repository node
├── k6 p95 regression
├── OpenTelemetry DB span
├── increased CPU
└── failing threshold
```

Then Ledger derives:

```text
Finding
Debt Risk
Change Risk
Confidence
Decision
```

## PostgreSQL Entities

Initial entities:

```text
Repository
PullRequest
Commit
AnalysisRun
Finding
Evidence
DebtItem
AgentRun
VerificationRun
Prediction
Outcome
```

Potential graph tables:

```text
GraphNode
GraphEdge
```

A dedicated graph database is not required for V1.

## Learning Record

Store learning data from day one, even before using ML.

Every important run should preserve:

```text
prediction
    ↓
decision
    ↓
action
    ↓
observed outcome
```

Example:

```text
Predicted p95 improvement       15%
Actual p95 improvement          11%

Predicted affected tests        14
Actual affected tests           17

Predicted regression risk       7%
Actual regression               false
```

This later enables:

```text
future debt prediction
blast-radius prediction
remediation-risk prediction
agent autonomy calibration
performance prediction
```

## Long-Term Goal

The learning layer should eventually compare predictions against real outcomes and calibrate Ledger's models instead of relying on fixed heuristic scores forever.
