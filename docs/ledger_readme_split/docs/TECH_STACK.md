# Tech Stack

## Core Technologies

| Area | Technology | Purpose |
|---|---|---|
| Backend language | Python 3.13+ | Analysis, orchestration, agents, integrations |
| API | FastAPI | REST API, GitHub webhooks, product backend |
| Frontend | React + TypeScript | Dashboard and analysis UI |
| Frontend build | Vite | Local development and production builds |
| Server state | TanStack Query | API fetching and polling |
| Routing | React Router | Client-side routing |
| Styling | Tailwind CSS | UI styling |
| Charts | Recharts | Risk, debt, and performance visualization |
| Database | PostgreSQL | Persistent product and analysis data |
| Agent orchestration | LangGraph | Stateful workflows, retries, approvals |
| Repository parsing | Tree-sitter | AST and syntax extraction |
| Semantic indexing | SCIP | Definitions, references, symbol resolution |
| Graph analysis | NetworkX | Dependency traversal and blast radius |
| Static analysis | Semgrep + native tools | Security, quality, linting, code smells |
| Analyzer interchange | SARIF | Standardized analyzer results |
| Sandbox | Docker | Isolated build/test/runtime execution |
| Load testing | k6 | Performance and stress testing |
| Observability | OpenTelemetry | Metrics, traces, runtime correlation |
| Git integration | GitHub App | Webhooks, PRs, checks, branches |
| Policy engine | Open Policy Agent | Autonomy and safety policies |
| CLI | Typer + Rich | Local Ledger commands |
| CI | GitHub Actions | CI integration |

## Future Infrastructure

| Area | Technology | When to Introduce |
|---|---|---|
| Stronger sandboxing | gVisor / Firecracker | Multi-tenant execution |
| Progressive deployment | Argo Rollouts | Production canary phase |
| Durable workflows | Temporal | Only if workflows become long-lived and distributed |
| Dedicated graph DB | Neo4j / Memgraph | Only if PostgreSQL + NetworkX becomes limiting |

## Frontend

```text
React
TypeScript
Vite
TanStack Query
React Router
Tailwind CSS
Recharts
```

Main pages:

```text
Dashboard
Repository
Pull Request Analysis
Analysis Run
Debt Item
Agent Run
Verification Results
Repository Graph (later)
```

The **PR Analysis** page should be the highest-priority screen.

## Backend

FastAPI owns:

- GitHub webhooks
- repository registration
- PR analysis
- findings
- agent runs
- verification runs
- frontend API

Example endpoints:

```text
POST /webhooks/github
POST /analysis

GET /analysis/{id}
GET /repositories/{id}
GET /pull-requests/{id}
GET /findings/{id}
GET /agent-runs/{id}
GET /verification-runs/{id}
```

## CLI

Using Typer + Rich:

```bash
ledger scan
ledger run
ledger explain
ledger fix
ledger verify
```

Use the CLI for local development, debugging, CI usage, and workflows that do not require GitHub.
