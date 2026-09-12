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
class LedgerState(TypedDict):
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
