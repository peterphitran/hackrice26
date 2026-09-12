# Backend Structure

```text
backend/
├── apps/
│   ├── api/         FastAPI entry point and HTTP routes
│   └── cli/         Typer command-line entry point

├── lou/
│   ├── core/        Shared configuration, types, and errors
│   ├── ingestion/   GitHub webhooks and source events
│   ├── repository/  Parsing, semantic indexing, graph, and history
│   ├── analyzers/   Static analyzers and normalized findings
│   ├── sandbox/     Isolated environment lifecycle
│   ├── execution/   Builds and test execution
│   ├── loadtest/    k6 scenarios and load-test execution
│   ├── telemetry/   OpenTelemetry and runtime correlation
│   ├── evidence/    Evidence collection and differential comparison
│   ├── scoring/     Debt and remediation-risk models
│   ├── decision/    Priority, confidence, and autonomy decisions
│   ├── agents/      LangGraph diagnosis and remediation workflows
│   ├── verification/ Independent patch verification
│   ├── policies/    Security and autonomy policies
│   ├── persistence/ Database models, repositories, and migrations
│   └── learning/    Production feedback and model calibration

├── workers/         Background worker entry points
├── contracts/       Shared schemas and service contracts
├── infra/           Containers and deployment infrastructure
├── fixtures/        Sample repositories and analysis data
├── tests/           Unit, integration, and end-to-end tests
└── scripts/         Developer and operational automation
```

## Dependency direction

Application entry points may depend on the `lou` package. Domain modules should not depend on `apps` or `workers`. Agent orchestration coordinates domain services; scoring and decision rules remain independently testable outside LangGraph.
