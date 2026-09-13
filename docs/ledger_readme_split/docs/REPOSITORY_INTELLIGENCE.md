# Repository Intelligence

Lou should understand repository structure and relationships rather than only inspecting changed files.

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

Lou should wrap existing analyzers.

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

Use SARIF for analyzer interoperability, then normalize into a Lou-specific finding model.

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

## Vendor-Neutral Ingestion

Lou should integrate existing sources rather than attempting to replace them. Adapters may ingest code-quality, security, CI, observability, incident, and planning data while keeping downstream models independent of any vendor.

```text
Semgrep / CodeQL / native linters ─┐
GitHub / GitLab / CI providers ────┤
OpenTelemetry / incident tools ────┼── normalized evidence ── graph
Issue trackers / ownership data ───┘
```

Every imported record needs a source, source-native ID, repository and commit identity, collection time, schema version, and confidence or completeness indicator.

## Hybrid Context Retrieval

Graph traversal answers what is structurally related; semantic retrieval answers what appears conceptually related. Use both, rerank the combined result, and enforce a context budget.

The implemented v1 semantic layer is a deterministic, dependency-free lexical ranker. It indexes
Python paths, stable source-symbol names, identifiers, docstrings, and comments directly from
immutable candidate-commit Git blobs. Results are supplemental, bounded by explicit indexing and
output budgets, and never displace graph-selected symbols, tests, endpoints, data dependencies, or
workloads. Incomplete indexing and retrieval are reported separately from a complete query with no
matches. A narrow repository-retriever interface permits a future local SCIP or embedding backend
without making either one a current dependency.

The context bundle for an agent should contain the finding, changed symbol, callers and callees, interfaces, affected tests, relevant historical changes, runtime evidence, and explicit notices about relationships the indexer could not resolve. Missing graph context must reduce confidence rather than being treated as evidence that no dependency exists.

## Impact Prediction Evaluation

Before verification, store the predicted affected symbols, tests, services, contracts, data stores, and runtime paths. After execution, record what actually changed or failed. Evaluate precision and recall so the graph becomes measurable infrastructure rather than an untested source of agent context.
