# Technical Debt Lifecycle

```mermaid
flowchart TD
    A[Code Change] --> B[Lou Analysis]

    B --> C{Debt Detected?}

    C -- No --> D[No New Debt Entry]
    C -- Yes --> E[Create Debt Entry]

    E --> F[Estimate Principal]
    E --> G[Estimate Interest]
    E --> H[Estimate Runtime / Business Impact]

    F --> I[Debt Portfolio]
    G --> I
    H --> I

    I --> J[Prioritize Debt]

    J --> K{Worth Remediating Now?}

    K -- No --> L[Track Over Time]
    K -- Yes --> M[Generate Remediation Candidate]

    M --> N[Estimate Remediation Risk]
    N --> O[Verify Candidate]
    O --> P{Verified?}

    P -- No --> Q[Retry / Human Review]
    P -- Yes --> R[Debt Repaid]

    R --> S[Record Before / After Outcome]
    S --> T[Update Debt History]

    L --> U[Recalculate Interest Later]
    U --> J
```

## Lou Model

```text
Principal
=
Cost to fix today

Interest
=
Cost created by leaving the issue unresolved
```

For V1, use normalized debt points or engineering-time estimates rather than
pretending dollar estimates are exact.
