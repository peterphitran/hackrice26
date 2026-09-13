# Lou Backend Foundation

The backend is a local-first Python application. Its current scope is the platform foundation: contracts, settings, health endpoint, CLI shell, PostgreSQL Compose configuration, and persistence wiring.

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
ruff format --check .
ruff check .
mypy apps contracts lou tests
pytest

uvicorn apps.api.main:app --reload
lou version
lou doctor
```

`lou analyze` is intentionally a foundation placeholder until the analysis application service is integrated.

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
