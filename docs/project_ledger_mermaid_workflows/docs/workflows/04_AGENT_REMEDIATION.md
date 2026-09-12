# AI Remediation Workflow

```mermaid
flowchart TD
    A[Failure / Finding] --> B[Gather Evidence]

    B --> B1[Static Findings]
    B --> B2[Repository Graph Context]
    B --> B3[Test Failures]
    B --> B4[Load Test Results]
    B --> B5[Runtime Metrics / Traces]

    B1 --> C[LangGraph Agent]
    B2 --> C
    B3 --> C
    B4 --> C
    B5 --> C

    C --> D[Diagnose Root Cause]
    D --> E[Create Minimal Remediation Plan]
    E --> F[Generate Patch]

    F --> G[Independent Verification]
    G --> H{Passed?}

    H -- Yes --> I[Risk Evaluation]
    I --> J{Allowed Action}

    J -->|Suggest| K[Return Recommendation]
    J -->|Generate Patch| L[Return Patch]
    J -->|Open PR| M[Create Remediation PR]

    H -- No --> N{Attempts Remaining?}

    N -- Yes --> O[Feed Verification Failure Back to Agent]
    O --> D

    N -- No --> P[Escalate to Human]
```

## Agent Design Rule

The agent:

```text
diagnoses
plans
proposes
patches
```

The verifier:

```text
builds
tests
measures
decides whether evidence improved
```

The agent should never be its own final judge.
