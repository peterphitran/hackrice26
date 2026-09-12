# Verification Workflow

```mermaid
flowchart TD
    A[Baseline Commit] --> B[Run Baseline Verification]
    C[Pull Request Commit] --> D[Run Candidate Verification]

    B --> E[Baseline Evidence]
    D --> F[Candidate Evidence]

    E --> G[Differential Comparison]
    F --> G

    G --> G1[Build]
    G --> G2[Unit / Integration Tests]
    G --> G3[Static Analysis]
    G --> G4[Security]
    G --> G5[Coverage]
    G --> G6[Load / Performance]
    G --> G7[Runtime Metrics]

    G1 --> H{Regression Detected?}
    G2 --> H
    G3 --> H
    G4 --> H
    G5 --> H
    G6 --> H
    G7 --> H

    H -- No --> I[Candidate Passes]
    H -- Yes --> J[Remediation Required]

    J --> K[AI Patch]
    K --> L[Run Exact Verification Again]

    L --> M[Fixed Evidence]
    M --> N[Compare Baseline vs PR vs Fix]

    N --> O{Verified Improvement?}

    O -- Yes --> P[Verified Fix]
    O -- No --> Q[Retry / Escalate]
```

## Example Comparison

```text
                BASE       PR       FIX

Unit tests      818        818      821
Coverage        81%        81%      84%
p95             182ms      420ms    191ms
Error rate      0.1%       4.2%     0.2%
CPU             42%        67%      45%
Risk score      32         76       29
```

The core principle is:

> The exact workload that exposed the problem should be rerun after remediation.
