# Team Ownership

For a team of four, divide ownership by subsystem rather than by generic frontend/backend labels.

## Engineer 1 — Repository Intelligence

Owns:

```text
repository parsing
Tree-sitter
SCIP
repository graph
Git history
static analyzers
blast-radius analysis
```

Primary output:

```text
PR
 ↓
changed symbols
 ↓
affected code
 ↓
findings
 ↓
graph context
```

## Engineer 2 — Agents & Decision Engine

Owns:

```text
LangGraph
agent state
diagnosis
planning
patch generation
debt scoring
change-risk scoring
autonomy decisions
```

Primary output:

```text
evidence
 ↓
diagnosis
 ↓
plan
 ↓
patch
 ↓
recommended action
```

## Engineer 3 — Execution & Verification

Owns:

```text
Docker sandbox
build execution
pytest
k6
OpenTelemetry
before/after comparison
verification
```

Primary output:

```text
baseline
 ↓
candidate
 ↓
performance / test comparison
 ↓
verified result
```

## Engineer 4 — Platform & Product

Owns:

```text
FastAPI
PostgreSQL
GitHub App
webhooks
React frontend
API contracts
product integration
```

Primary output:

```text
GitHub PR
 ↓
Ledger pipeline
 ↓
results
 ↓
GitHub check + dashboard
```

## Shared Contracts

Define these before working independently.

### Finding

```python
class Finding:
    id: str
    source: str
    category: str
    severity: str
    confidence: float
    file: str
    symbol: str | None
    message: str
```

### RepositoryContext

```python
class RepositoryContext:
    changed_files: list[str]
    changed_symbols: list[str]
    affected_symbols: list[str]
    affected_tests: list[str]
    blast_radius: float
```

### VerificationResult

```python
class VerificationResult:
    passed: bool
    tests: dict
    performance_before: dict
    performance_after: dict
    static_findings_before: list
    static_findings_after: list
```

### AgentResult

```python
class AgentResult:
    diagnosis: str
    plan: str
    patch: str | None
    confidence: float
```

### LedgerDecision

```python
class LedgerDecision:
    debt_score: float
    change_risk: float
    action: Literal[
        "REPORT",
        "SUGGEST",
        "GENERATE_PATCH",
        "OPEN_PR",
    ]
```
