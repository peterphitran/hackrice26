# Repository Guidelines

## Project Structure & Module Organization

Lou is a local-first Python system for detecting runtime regressions, proposing bounded repairs,
and independently verifying them. Application entry points live in `backend/apps/api/` (FastAPI)
and `backend/apps/cli/` (Typer). Shared versioned Pydantic models belong in `backend/contracts/`.
Domain code is organized under `backend/lou/`, including repository analysis, execution,
verification, agents, scoring, and persistence. Tests mirror those boundaries under
`backend/tests/{unit,integration,end_to_end}/`. Infrastructure is in `backend/infra/`, benchmark
repositories in `backend/fixtures/`, and product and hackathon specifications in `docs/`.

Treat `docs/hackathon/TASKS.md` as the source of truth for task ownership and acceptance criteria.
Domain modules may depend on `lou.core` and repository interfaces, but never on `apps` or
`workers`. Do not duplicate shared contracts inside workstreams.

## Build, Test, and Development Commands

Run commands from `backend/` after installing Python 3.11 or newer:

```bash
python -m pip install -e '.[dev]'       # install application and developer tools
python -m pytest                        # run all tests
ruff check .                            # lint and import-order checks
mypy apps contracts lou                 # strict type checking
docker compose -f infra/compose.yaml up -d postgres
uvicorn apps.api.main:app --reload      # run the local API
lou doctor                              # display safe local configuration
```

The full containerized application stack is managed from the repository root:

```bash
docker compose build
docker compose run --rm backend alembic upgrade head
docker compose up -d
curl http://localhost:8080/api/health
docker compose down
```

Nginx serves the frontend on `http://localhost:8080` and proxies `/api/` to
the internal FastAPI service. The backend connects to PostgreSQL using the
Compose service name `postgres`; the backend port is not published to the host.
The root Compose stack is for the frontend/API/PostgreSQL deployment path.

The live fixture demo remains a separate host-side workflow:

```bash
cd backend
python -m lou.verification.int003
```

It launches disposable Docker sandbox containers through the existing
`backend/infra/compose.yaml` infrastructure. The application backend image
must not receive `/var/run/docker.sock`, Docker credentials, or arbitrary host
repository mounts. Do not treat the containerized API smoke test as proof that
the live sandbox demo ran.

PostgreSQL 16 in Docker Compose is the project database. Do not substitute SQLite for application
persistence or the checkout regression benchmark without an approved contract change. The root
Compose file is the canonical full-application stack; `backend/infra/compose.yaml` remains the
backend-only and fixture-sandbox infrastructure path.

## Coding Style & Naming Conventions

Use four-space indentation, Python 3.11-compatible syntax, type annotations, and a 100-character
line limit. Ruff enforces `E`, `F`, `I`, and `UP` rules; mypy runs in strict mode. Name modules and
functions `snake_case`, classes `PascalCase`, and constants `UPPER_SNAKE_CASE`. Keep CLI and API
layers thin and place behavior in directly testable domain services.

## Testing Guidelines

Use pytest. Name files `test_*.py` and tests `test_<behavior>`. Add unit tests for domain logic and
integration tests for PostgreSQL, sandbox, API, and execution boundaries. Performance claims must
compare baseline, candidate, and fix under equivalent workloads and report noisy runs as
`inconclusive`.

## Commits & Pull Requests

History uses short imperative subjects, such as `Set up Lou platform foundation`. Use task IDs in
branches and PR titles (`ev-001-broken-store`). Keep PRs small, describe behavior and verification,
link the relevant task, and update contract fixtures when schemas change. Include screenshots only
for user-interface changes.

## Security & Configuration

Copy `.env.example` to `.env`; never commit credentials, raw environment dumps, `.lou/` artifacts,
or nested fixture `.git` directories. Sandboxes and the application backend must not receive Docker
sockets or credentials. Use the Nginx `/api/` proxy for browser-to-API traffic; browser code should
not call the internal Docker hostname `http://backend:8000` directly.
