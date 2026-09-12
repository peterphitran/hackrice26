# Agents & Decision Engine

## LangGraph Workflow

```text
START
  ↓
gather_context
  ↓
analyze_findings
  ↓
diagnose
  ↓
plan_fix
  ↓
generate_patch
  ↓
verify
  ↓
passed?
  ├── yes → risk_check → open_pr
  │
  └── no → retry
              ↓
          max attempts
              ↓
           escalate
```

## Example State

```python
class LouState(TypedDict):
    repo_id: str
    commit_sha: str
    pr_number: int

    changed_files: list[str]
    changed_symbols: list[str]

    findings: list[dict]
    graph_context: dict

    build_result: dict
    test_results: dict
    load_test_results: dict

    diagnosis: str | None
    plan: str | None
    patch: str | None

    debt_score: float
    change_risk: float

    verification_results: dict | None
    attempt: int
    autonomy_level: int
    approval_status: str | None
```

## Technical Debt Model

### Principal

Current estimated remediation cost.

```text
Principal = estimated effort required to fix today
```

### Interest

Expected future cost of leaving the issue unresolved.

Signals can include:

```text
code churn
centrality
complexity
dependency count
incident history
performance impact
change frequency
ownership
```

Conceptually:

```text
Debt(t) = Principal + Interest(t)
```

For V1, use transparent points or engineering-time estimates.

## Two Separate Risk Models

### Debt Risk

> How costly is leaving this issue unresolved?

### Remediation Risk

> How dangerous is changing this issue?

Example:

```text
Debt Risk:          84
Remediation Risk:   20

Decision:
High-value and relatively safe candidate for AI remediation.
```

## Autonomy Levels

```text
A0  Observe only
A1  Recommend a fix
A2  Generate a patch
A3  Open a pull request
A4  Auto-merge
A5  Auto-deploy
```

The MVP should stop at:

```text
A3 — Open pull request
```

## Decision Engine

The decision engine should own:

```text
debt scoring
remediation risk
confidence
priority
required verification
allowed autonomy
```

Keep this logic outside LangGraph so it can be tested independently.

## Decision Objective

A high debt score alone does not justify a change. The decision engine should compare the expected cost of leaving the debt with the full expected cost of remediation:

```text
Expected value of fix
= predicted debt avoided
- remediation effort
- verification compute cost
- expected regression cost
```

The result must include calibrated uncertainty. Low-confidence or incomplete repository context should increase required verification or lower autonomy; it must not be converted into false precision.

## Dynamic Autonomy Inputs

Select autonomy per proposed patch rather than granting a permanent capability to an agent. Inputs include:

```text
blast radius
system criticality
test coverage and verification strength
reversibility and rollback cost
schema or data migration impact
historical failures
policy constraints
model confidence and missing context
```

OPA sets the hard organizational ceiling. The decision engine may choose a stricter action but never exceed policy.

## Agent Roles and Separation

Roles such as diagnosis, planning, patching, test generation, and review may use separate prompts or graph nodes, but they share one explicit workflow state and evidence model. The verifier must be independent of the patch generator and must not accept the generator's narrative as proof.

Agents only propose artifacts. A trusted service validates the patch and performs GitHub writes. Retry limits, token and compute budgets, repeated-patch detection, and an explicit `ABANDONED` outcome prevent unbounded remediation loops.
