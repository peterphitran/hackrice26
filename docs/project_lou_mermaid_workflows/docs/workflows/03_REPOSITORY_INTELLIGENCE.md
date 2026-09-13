# Repository Intelligence Workflow

```mermaid
flowchart TD
    A[Repository / Pull Request] --> B[Change Detection]

    B --> C1[Changed Files]
    B --> C2[Changed Symbols]
    B --> C3[Commit / Git Metadata]

    C1 --> D[Tree-sitter Parser]
    C2 --> E[SCIP Semantic Index]
    C3 --> F[Git History Analyzer]

    D --> G[Syntax Structure]
    E --> H[Definitions / References / Implementations]
    F --> I[Churn / Ownership / Co-change / Reverts]

    G --> J[Repository Graph Builder]
    H --> J
    I --> J

    J --> K[Repository Graph]

    K --> K1[Callers / Callees]
    K --> K2[Imports / Dependencies]
    K --> K3[Tests]
    K --> K4[Services / APIs]
    K --> K5[Historical Relationships]

    K1 --> L[Impact Analysis]
    K2 --> L
    K3 --> L
    K4 --> L
    K5 --> L

    L --> M[Blast Radius]
    L --> N[Affected Tests]
    L --> O[Relevant Agent Context]
    L --> P[Risk Features]
```

## Purpose

The repository intelligence layer answers:

```text
What changed?
What depends on it?
What tests are relevant?
What is the likely blast radius?
What context should the AI agent receive?
```
