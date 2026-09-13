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
python -m pytest tests/unit/analyzers \
  --cov=lou.analyzers \
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

The repository and analyzer coverage commands are required gates for RI-001 and RI-006. They fail
when branch coverage for their respective packages falls below 90 percent.

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

# Use the returned run ID to render persisted evidence later.
lou report --run <analysis-run-id> --output markdown
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

## Clean-clone demo rehearsal

Use this rehearsal before a presentation or after cloning on another machine. It
creates an isolated Postgres container on port `55432`, runs migrations, seeds a
temporary copy of the fixture repository, and invokes the same application
composition used by `lou analyze`. It leaves the database container running so
you can inspect its evidence; remove it when finished.

```bash
cd backend
python -m lou.verification.int002

# Optional cleanup after inspecting the persisted run and artifacts.
docker compose -p lou-int002 -f infra/compose.yaml down -v
```

Success prints a `__LOU_INT002_RESULT__` line with `"status": "succeeded"`,
plus the measured `queries 2 -> 51` result. The controller proves that graph
selection, equivalent baseline/candidate workloads, PostgreSQL persistence, and
the evidence report path work together. It requires Docker Desktop, Python 3.11+
and the dependencies from the setup section; it does not depend on a cloud API.

For a second-machine check, repeat the setup and this exact command from a fresh
clone. Record the resulting run ID and commit SHA in the team demo notes. A
different verdict is a failure to investigate, not a result to present as a
regression.

For a disposable clone rehearsal on the same machine, and the recorded fallback
used during a presentation, see [demo/README.md](demo/README.md).

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
