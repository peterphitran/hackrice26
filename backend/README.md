# Lou Backend Foundation

The backend is a local-first Python application. It includes contracts, PostgreSQL persistence,
evidence-driven analysis orchestration, and a narrow live `lou analyze` fixture workflow.

See the [foundation specification](../docs/hackathon/foundation/README.md) for scope and acceptance criteria.

## Prerequisites

- Python 3.11 or newer; Python 3.13 is recommended
- Docker Compose only when working on PostgreSQL-backed tasks

## Local Setup

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

The direct dependencies in `pyproject.toml` are pinned to exact versions.

## Development Commands

```bash
python -m pytest
python -m pytest tests/unit/repository \
  --cov=lou.repository \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=90
ruff check .
mypy apps contracts lou tests
pytest

uvicorn apps.api.main:app --reload
lou version
lou doctor
```

The repository-intelligence coverage command is a required gate for RI-001. It fails when
branch coverage for `lou.repository` falls below 90 percent.

## Live Fixture Analysis

The first live slice intentionally supports only Lou's checked-in `broken-store` fixture. It creates
separate detached baseline and candidate worktrees, runs the same pytest and k6 workloads in each,
stores immutable evidence in PostgreSQL, and reports a deterministic result. It does not execute
arbitrary third-party repository commands or generate patches.

```bash
cd backend
docker compose --env-file .env -f infra/compose.yaml up -d postgres
alembic upgrade head
python fixtures/broken-store/scripts/seed_fixture_repo.py .lou/broken-store
lou analyze \
  --repo .lou/broken-store \
  --base good \
  --candidate n-plus-one \
  --output json
```

The expected candidate summary has a `query_count_delta` of `49` (2 baseline queries versus 51
candidate queries) and a `runtime_regression` classification. Normal interactive runs return zero
after printing a completed clean, regression, or inconclusive analysis. Add `--ci` to return `1`
for a measured regression, `2` for an inconclusive measurement, and `3` for an operational failure.

Run the opt-in Docker proof against the disposable `lou_test` database:

```bash
RUN_LOU_LIVE_E2E=1 \
LOU_TEST_DATABASE_URL=postgresql+psycopg://lou_migrator:lou_migrator@127.0.0.1:5432/lou_test \
LOU_FIXTURE_DATABASE_URL=postgresql://lou_migrator:lou_migrator@postgres:5432/lou_test \
python -m pytest -m integration tests/integration/application/test_live_fixture.py
```

## PostgreSQL (optional until PF-002)

```bash
docker compose --env-file .env -f infra/compose.yaml up -d postgres
docker compose --env-file .env -f infra/compose.yaml down
```

Docker is not required for the unit-test quality gate. It is required once
database migrations and persistence integration tests are introduced.

## Troubleshooting

```bash
# Confirm you are using a supported Python runtime.
python3.13 --version

# Recreate the local environment after dependency changes.
rm -rf .venv
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Update a direct dependency version in `pyproject.toml`, then recreate the local
environment and run the full quality gate before committing.
