# Risk & Autonomy Decision Workflow

```mermaid
flowchart TD
    A[Verified Finding / Proposed Patch] --> B[Decision Engine]

    B --> C[Debt Risk]
    B --> D[Remediation Risk]
    B --> E[Confidence]
    B --> F[Reversibility]
    B --> G[Policy]

    C --> H[Autonomy Evaluator]
    D --> H
    E --> H
    F --> H
    G --> H

    H --> I{Autonomy Level}

    I -->|A0| J[Observe Only]
    I -->|A1| K[Recommend Fix]
    I -->|A2| L[Generate Patch]
    I -->|A3| M[Open Pull Request]
    I -->|A4| N[Auto-Merge]
    I -->|A5| O[Auto-Deploy]

    N --> P[Production Guardrails]
    O --> P
```

## Suggested Autonomy Levels

```text
A0  Observe only
A1  Recommend
A2  Generate patch
A3  Open PR
A4  Auto-merge
A5  Auto-deploy
```

For the MVP, Lou should stop at:

```text
A3 — Open Pull Request
```

Human review remains the merge authority.
