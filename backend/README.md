# Lou Backend Foundation

The backend is a local-first Python application. Its current scope is the platform foundation: contracts, settings, health endpoint, CLI shell, PostgreSQL Compose configuration, and persistence wiring.

See the [foundation specification](../docs/hackathon/foundation/README.md) for scope and acceptance criteria.

## Prerequisites

- Python 3.11 or newer; Python 3.13 is recommended
- Docker Compose for local PostgreSQL

## Local Setup

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
docker compose -f infra/compose.yaml up -d postgres
```

## Development Commands

```bash
python -m pytest
ruff check .
mypy apps contracts lou
uvicorn apps.api.main:app --reload
lou version
lou doctor
```

`lou analyze` is intentionally a foundation placeholder until the analysis application service is integrated.
