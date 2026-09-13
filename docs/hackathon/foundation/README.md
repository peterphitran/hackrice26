# Platform Foundation Specification

This specification covers the shared base application required before the four Lou workstreams implement their domain features. It implements SH-002, SH-003, SH-004, PF-001, and the setup portion of PF-002 from the [Engineer 4 assignment](../team/engineer-4/README.md).

## Goal

Provide a reproducible, local-first Python application skeleton with versioned contracts, a health-checked API, a CLI entry point, a PostgreSQL Compose service, artifact conventions, and migration-ready persistence wiring.

## Non-Goals

This foundation does not implement repository analysis, graph traversal, sandbox execution, k6, agent remediation, database migrations, GitHub integration, a worker queue, or a frontend.

## Runtime Contract

```text
Recommended runtime: Python 3.13
Minimum supported runtime: Python 3.11
Database: PostgreSQL 16 through Docker Compose
Package manager: any PEP 517-compatible tool
```

Python 3.11 is the minimum so teammates can run the project locally without requiring Python 3.13. No foundation module may use version-specific syntax beyond Python 3.11.

## Delivered Interfaces

### API

```text
GET /health

200 OK
{
  "status": "ok",
  "service": "lou",
  "version": "0.1.0"
}
```

The health endpoint verifies that the API process is running. It does not query PostgreSQL; database readiness is an explicit later endpoint or application-service concern.

### CLI

```text
lou --help
lou version
lou doctor
lou analyze --repo PATH --base SHA --candidate SHA
```

`lou analyze` validates input and reports that the analysis service is not wired yet. It must not imply that an analysis was performed.

### Configuration

```text
LOU_ENVIRONMENT
LOU_LOG_LEVEL
LOU_DATABASE_URL
LOU_ARTIFACT_ROOT
LOU_POSTGRES_PORT
```

The defaults are local-only. The application never requires a hosted service or an API key to start the API, CLI, or tests.

### Artifact Convention

```text
.lou/artifacts/{analysis_run_id}/
├── baseline/
├── candidate/
├── fix/
├── patches/
└── report/
```

The foundation only creates and validates this root. Execution workstream code owns phase artifacts and cleanup behavior.

## Versioned Contracts

The foundation supplies Pydantic models for these version-one boundaries:

```text
AnalysisJob
RepositoryChange
RepositoryContext
WorkloadSelection
Finding
Evidence
VerificationResult
AgentResult
PatchArtifact
LouDecision
```

All models reject unknown fields and expose `schema_version = "1"`. Tool-specific data belongs in an explicit metadata field, not an unreviewed top-level field.

## Persistence Boundary

The foundation provides settings-driven SQLAlchemy engine and session-factory construction. It does not create tables automatically and does not let API routes access sessions directly. Alembic migrations and repository interfaces are the next platform tasks.

## Acceptance Checks

- [ ] `python -m pytest` passes from `backend/` with development dependencies installed.
- [ ] `ruff check .` passes from `backend/`.
- [ ] `GET /health` returns the documented payload.
- [ ] `lou version` reports the application version.
- [ ] `lou doctor` prints local configuration without exposing secrets.
- [ ] `lou analyze` validates a repository path and reports its unimplemented foundation state clearly.
- [ ] Contract JSON serialization round-trips in tests.
- [ ] `docker compose -f infra/compose.yaml config` validates when Docker Compose is installed.
- [ ] `.env` and `.lou/` are ignored by Git.

## Integration Rules

- Repository, agent, and execution modules consume contracts from `contracts`; they do not duplicate schemas.
- Domain modules depend on `lou.core` and repository interfaces, never `apps` modules.
- CLI and API are adapters; both will call the same application service once the analysis orchestration task begins.
- Add a migration before adding persistent state to a feature.
- Any contract change must update JSON fixtures and the master task board.
