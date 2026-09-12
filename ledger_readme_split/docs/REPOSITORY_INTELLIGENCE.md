# Repository Intelligence

Ledger should understand repository structure and relationships rather than only inspecting changed files.

## Tree-sitter

Tree-sitter provides syntax-level information:

```text
files
functions
classes
methods
imports
AST structure
control structures
```

## SCIP

SCIP provides semantic relationships:

```text
definitions
references
implementations
symbol resolution
cross-file relationships
type relationships
```

Tree-sitter answers:

> What is the structure of this code?

SCIP answers:

> What does this symbol actually refer to?

## Repository Graph

Use NetworkX initially.

Example edges:

```text
FUNCTION ──CALLS────────► FUNCTION
FILE ─────IMPORTS───────► FILE
CLASS ────IMPLEMENTS────► INTERFACE
TEST ─────TESTS─────────► FUNCTION
COMMIT ───MODIFIES──────► FUNCTION
SERVICE ──DEPENDS_ON────► SERVICE
```

Useful graph metrics:

```text
fan-in
fan-out
centrality
dependency depth
blast radius
affected tests
affected services
connected components
```

## Git Intelligence

Potential historical signals:

```text
code churn
commit frequency
file age
ownership
co-change relationships
reverts
bug-fix commits
historical failures
```

These later contribute to:

- debt interest
- remediation risk
- prioritization
- agent autonomy

## Static Analysis

Ledger should wrap existing analyzers.

### Python

```text
Ruff
Bandit
Radon
pytest
coverage.py
Semgrep
```

### JavaScript / TypeScript

```text
ESLint
TypeScript compiler
Vitest / Jest
Semgrep
```

### Cross-language

```text
Semgrep
Trivy
OSV Scanner
```

For the MVP, support one language first.

## SARIF

Use SARIF for analyzer interoperability, then normalize into a Ledger-specific finding model.

Example:

```json
{
  "source": "semgrep",
  "rule": "sql-injection",
  "category": "security",
  "severity": "high",
  "confidence": 0.94,
  "file": "payments.py",
  "symbol": "PaymentService.authorize"
}
```
