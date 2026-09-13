# Lou Backend Foundation

The backend is a local-first Python application. It includes contracts, PostgreSQL persistence,
evidence-driven analysis orchestration, and a narrow live `lou analyze` fixture workflow.

See the [foundation specification](../docs/hackathon/foundation/README.md) for scope and acceptance criteria.

## Optional Local Telemetry

Telemetry is disabled by default and is supporting evidence only: a collector
outage cannot change a verification result. Start the local collector only when
rehearsing runtime correlation:

```bash
docker compose -f infra/compose.yaml --profile telemetry up -d otel-collector
python -m lou.telemetry.int007
docker compose -f infra/compose.yaml --profile telemetry down
```

Set `LOU_TELEMETRY_EXPORTER=memory` for persisted safe summaries, or `otlp` to
mirror them to the local collector. See `demo/README.md` for the full rehearsal.

## Staging canary rehearsal (M8)

M8 is staging-only. It writes an append-only deployment journal below the artifact
root, evaluates validation and telemetry against a deterministic SLO policy, and
can only promote, pause, or roll back a local rehearsal release. It does not read
or store production credentials.

```bash
cd backend
python -m lou.deployment.int008

# Or run a single local canary through the CLI.
lou deploy --run demo-run --commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --output json
lou deploy-status --release <release-id> --output json
lou deploy-rollback --release <release-id> --reason "operator review"
lou deploy-report --release <release-id>
```

The Docker controller prints `__LOU_INT008_RESULT__`. It passes only when a
healthy canary is promoted and a deliberate SLO breach is rolled back. Missing
telemetry pauses the release. The Argo Rollouts adapter is a trusted boundary for
future staging clusters; this local rehearsal uses the credential-free adapter.

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

## Local API

Start the same local composition used by `lou analyze`:

```bash
cd backend
uvicorn apps.api.main:app --reload
```

`GET /health` is database-free. The analysis endpoints use versioned JSON contracts and
the existing PostgreSQL-backed application service:

```bash
curl -X POST http://127.0.0.1:8000/analysis \
  -H 'content-type: application/json' \
  -d '{
    "schema_version": "1",
    "repository_path": "/absolute/path/to/broken-store",
    "base_revision": "good",
    "candidate_revision": "n-plus-one"
  }'

curl http://127.0.0.1:8000/analysis/<analysis-run-id>
curl http://127.0.0.1:8000/analysis/<analysis-run-id>/report
curl 'http://127.0.0.1:8000/analysis/<analysis-run-id>/report?format=markdown'
```

The `POST` response is `202` and includes the durable run ID. Repeating identical
immutable inputs reuses the same run; set `force_new_run` and supply a `force_token`
only when intentionally measuring a new run. Invalid repository/revision/configuration
input returns a versioned `400` error before an analysis row is created. The current
local fixture runs synchronously inside the request; no queue or hosted worker is added.

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
