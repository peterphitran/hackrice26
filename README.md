# Project Lou

Lou is an evidence-driven software maintenance system that detects technical debt and runtime regressions, evaluates whether remediation is worthwhile and safe, generates bounded fixes, and independently verifies their outcomes.

## Repository layout

- [`backend/`](backend/) — Python services, domain modules, workers, contracts, infrastructure, fixtures, and tests
- [Backend structure](docs/BACKEND_STRUCTURE.md) — module responsibilities and dependency direction
- [Hackathon plan](docs/hackathon/README.md) — local-first scope, demo contract, cost guardrails, and build order
- [Hackathon task board](docs/hackathon/TASKS.md) — parallel workstreams, dependencies, acceptance criteria, and integration gates
- [PostgreSQL schema plan](docs/hackathon/DATABASE_SCHEMA.md) — MVP data model, draft DDL, and persistence lifecycle
- [Product and architecture documentation](docs/ledger_readme_split/README.md) — product thesis, technical design, security, execution, learning, ownership, and roadmap
- [Workflow diagrams](docs/project_ledger_mermaid_workflows/README.md) — Mermaid views of the end-to-end system

The MVP centers on one claim: Lou can find a regression ordinary CI misses, propose a minimal fix, and prove the fix works through repeatable baseline → PR → fix evidence.
