# Lou End-to-End Demo

This is the shortest reliable demo path for Lou's local-first verification loop:

```text
good commit → N+1 candidate → runtime evidence → bounded fix → independent verification
```

The demo uses the checked-in `broken-store` fixture. It does not require GitHub, a model API, or the frontend.

## Prerequisites

- Python 3.11+ (3.13 recommended)
- Docker Desktop running
- `k6` on `PATH` for the live benchmark

## One-command rehearsal

From the repository root:

```bash
cd backend
python -m lou.verification.int003
```

The controller starts an isolated PostgreSQL container, seeds the fixture, runs baseline and candidate verification, applies the recorded fix, and verifies the fix independently.

Success prints a line beginning with `__LOU_INT003_RESULT__`. The successful result should include the query-count comparison `2 -> 51` and a succeeded status.

## Manual demo path

From `backend/`:

```bash
cp .env.example .env
python -m pip install -e '.[dev]'
docker compose --env-file .env -f infra/compose.yaml up -d postgres
alembic upgrade head
python fixtures/broken-store/scripts/seed_fixture_repo.py .lou/broken-store
lou analyze --repo .lou/broken-store --base good --candidate n-plus-one --output json
```

Copy the `analysis_run_id` from the output, then render the persisted evidence:

```bash
lou report --run <analysis-run-id> --output markdown
```

The expected result is that unit tests pass while the candidate shows a runtime regression and a query-count delta of `49`.

For CI-style exit codes, add `--ci` to `lou analyze`:

```bash
lou analyze --repo .lou/broken-store --base good --candidate n-plus-one --ci
```

## Presentation fallback

After a successful rehearsal, save a deterministic fallback report:

```bash
python -m scripts.record_demo_fallback --run <good-run-id>
```

The fallback is written under `.lou/` and contains the same JSON evidence, Markdown report, and SHA-256 manifest used by the report reader. Use it if Docker or a live benchmark is unavailable during the presentation.

## Demo narration

1. Start with the known-good and candidate refs.
2. Show that ordinary unit tests pass in both states.
3. Explain that Lou selects the checkout workload from repository relationships.
4. Show the runtime evidence: queries increase from `2` to `51`.
5. Show the bounded recorded patch.
6. Show that the same verification rejects the deliberate no-op patch and accepts the real fix.
7. End on the persisted report and decision.

## Cleanup

The one-command rehearsal leaves its isolated container running for inspection. Remove it when finished:

```bash
docker compose -p lou-int002 -f infra/compose.yaml down -v
```

The manual path can be stopped with:

```bash
docker compose --env-file .env -f infra/compose.yaml down
```

## Troubleshooting

- Missing `k6`: install it or present the saved fallback; do not claim a live runtime result.
- Docker unavailable: use the recorded fallback.
- Database connection errors: confirm Docker Desktop is running and inspect `docker compose ps`.
- Repeated identical analysis: use `--force --force-token <unique-token>` only when a fresh run is needed.
- Inconclusive measurements: rerun under stable local resource conditions; inconclusive is a valid result, not a regression verdict.
