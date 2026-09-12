# Project Lou

Project Lou is an AI-native software engineering platform that analyzes repositories, detects technical debt and runtime regressions, proposes remediations, verifies fixes through testing and load testing, and eventually learns from deployment outcomes.

Lou is not primarily another code-review bot or a detector for supposedly AI-authored code. It is a vendor-neutral decision and learning layer for software maintenance: it determines which debt is worth fixing, predicts the consequences of a proposed change, decides how much autonomy an agent should receive, and checks whether the intervention delivered the predicted outcome.

## Core Goal

Lou should answer five questions:

1. **What changed?**
2. **What does that change impact?**
3. **Is the change introducing technical debt, reliability risk, security issues, or performance regressions?**
4. **Can an AI agent safely fix the issue?**
5. **Did the fix actually improve the system?**

The long-term loop is:

```text
Code / PR
   ↓
Repository understanding
   ↓
Static + runtime analysis
   ↓
Technical debt / risk scoring
   ↓
AI diagnosis + remediation
   ↓
Verification
   ↓
Deployment / canary
   ↓
Production observation
   ↓
Outcome learning
   ↺
```

> **AI proposes changes. Independent evidence determines whether those changes are trustworthy.**

## MVP Scope

The first version should focus on one complete end-to-end workflow:

```text
GitHub PR
   ↓
Clone repository
   ↓
Static analysis
   ↓
Build
   ↓
Unit / integration tests
   ↓
Run application
   ↓
Load test
   ↓
Detect issue
   ↓
AI diagnosis
   ↓
Generate patch
   ↓
Re-run exact verification
   ↓
Compare before vs after
   ↓
Open remediation PR / GitHub check
```

The MVP should prove one thing extremely well:

> Lou can find a problem ordinary CI misses, generate a fix, and prove the fix works.

### MVP success criteria

The first demo is successful when it can repeatedly:

1. detect a known runtime or performance regression that the normal test suite misses;
2. preserve the raw evidence that explains the finding;
3. generate a minimal patch in an isolated worktree;
4. rerun the same workload under the same resource limits;
5. show a meaningful baseline → PR → fix comparison; and
6. open a remediation pull request without giving the agent write credentials.

The MVP intentionally excludes multi-language indexing, Kubernetes, auto-merge, auto-deploy, learned scoring models, enterprise policy management, and a large dashboard suite.

## Core Stack

| Area | Technology |
|---|---|
| Backend | Python + FastAPI |
| Frontend | React + TypeScript + Vite |
| Database | PostgreSQL |
| Agent orchestration | LangGraph |
| Repository parsing | Tree-sitter |
| Semantic indexing | SCIP |
| Graph analysis | NetworkX |
| Static analysis | Semgrep + native tools |
| Analyzer interchange | SARIF |
| Sandbox | Docker |
| Load testing | k6 |
| Observability | OpenTelemetry |
| Git integration | GitHub App |
| Policy engine | Open Policy Agent |
| CLI | Typer + Rich |
| CI | GitHub Actions |

## High-Level Architecture

```text
                         GitHub
                            │
                            ▼
                       GitHub App
                            │
                            ▼
                         FastAPI
                            │
                            ▼
                        LangGraph
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
 Repository Intelligence   Sandbox          Evidence
        │                   │                   │
 Tree-sitter             Docker             Postgres
 SCIP                    pytest
 NetworkX                k6
 Semgrep                 OpenTelemetry
        │                   │
        └───────────┬───────┘
                    ▼
              Decision Engine
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
      Diagnose     Patch    Risk Eval
          │         │         │
          └─────────┼─────────┘
                    ▼
               Verification
                    │
              ┌─────┴─────┐
              ▼           ▼
            PASS         FAIL
              │           │
              ▼           ▼
           Open PR      Retry /
                        Escalate
```

## Documentation

- [Tech Stack](docs/TECH_STACK.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Repository Intelligence](docs/REPOSITORY_INTELLIGENCE.md)
- [Agents & Decision Engine](docs/AGENTS_AND_DECISIONS.md)
- [Execution, Testing & Verification](docs/EXECUTION_AND_VERIFICATION.md)
- [Security & Policy](docs/SECURITY_AND_POLICY.md)
- [Data & Learning](docs/DATA_AND_LEARNING.md)
- [Team Ownership](docs/TEAM.md)
- [Roadmap](docs/ROADMAP.md)

## Design Principles

### Evidence over LLM opinion

Bad:

```text
AI says:
"This looks risky."
```

Good:

```text
Remediation Risk: 78

Evidence:
centrality             0.81
coverage               43%
affected functions     31
affected services      4
historical reverts     2
performance regression 38%
```

### Differential over absolute

Prefer:

```text
baseline → PR → fix
```

over:

```text
candidate passes
```

### Human review first

The MVP should automatically create pull requests, not automatically merge or deploy.

### Existing tools over reinvention

Use mature tools such as SCIP, SARIF, Semgrep, k6, OpenTelemetry, OPA, GitHub, and Argo Rollouts rather than rebuilding them.

### Keep product logic outside LangGraph

LangGraph orchestrates. Lou modules decide.

```python
risk = decision_engine.evaluate(change)
```

rather than embedding core business rules directly inside agent nodes.

### Predict, then measure

Lou should record its expectation before executing or deploying a change. A useful result includes both the prediction and the observed outcome, including uncertainty and any missed impact. This makes debt, blast-radius, and remediation-risk models testable rather than decorative scores.

## Long-Term Vision

The mature version of Lou becomes a closed-loop software engineering control system:

```text
Repository
   ↓
Understand
   ↓
Predict
   ↓
Decide
   ↓
Remediate
   ↓
Verify
   ↓
Deploy
   ↓
Observe
   ↓
Learn
   ↺
```

Lou should eventually answer:

- What technical debt should we fix?
- What will happen if we do nothing?
- What will happen if we change it?
- How much autonomy should an AI agent receive?
- Did the intervention actually improve the system?
