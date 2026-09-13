# Data, Evidence & Learning

## Evidence Model

Lou should store evidence separately from conclusions.

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

Then Lou derives:

```text
Finding
Debt Risk
Change Risk
Confidence
Decision
```

## PostgreSQL Entities

The concrete hackathon schema, relationships, indexes, and draft DDL are defined in the [PostgreSQL Schema Plan](../../hackathon/DATABASE_SCHEMA.md).

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

The learning layer should eventually compare predictions against real outcomes and calibrate Lou's models instead of relying on fixed heuristic scores forever.

## Provenance and Reproducibility

Every claim must be traceable to immutable inputs:

```text
repository + commit SHA
analysis and policy version
tool name and version
sandbox image digest
dependency lockfile digest
workload and dataset version
collection timestamp
raw artifact reference
```

Derived scores should record their feature values and model or rule revision. Recomputing a result later must not silently use a newer policy or model.

## Outcome Windows

Not every prediction can be judged immediately. Define observation windows by outcome type:

```text
CI and verification failure       immediate
canary or rollback outcome        minutes to hours
incident and SLO impact           days to weeks
maintenance interest              weeks to months
```

Record `unknown`, `not_yet_observed`, and `not_applicable` separately. Missing outcomes are not successful outcomes.

## Model Evaluation

Track calibration and error, not only accuracy. Examples include risk calibration, precision/recall for affected tests and services, absolute error for performance predictions, patch acceptance rate, rollback rate, and false-positive burden. Compare rules or models against a simple baseline before claiming improvement.

## Retention and Privacy

Source code, patches, logs, traces, prompts, and model inputs may contain secrets or customer data. Store references instead of duplicating source where possible, redact secrets before persistence, scope every record to a tenant and repository, define retention periods, and support deletion without leaving derived embeddings or artifacts behind.
