# Broken Store Fixture

This fixture models a FastAPI and PostgreSQL checkout whose output remains correct after an N+1
database regression is introduced.

## Contract

- The fixed cart contains 50 distinct products.
- PostgreSQL uses an isolated `broken_store` schema containing 50 products and 50 cart rows.
- `good` completes checkout with 2 SQL queries: one cart query and one batched product query.
- `n-plus-one` returns the same receipt with 51 SQL queries: one cart query and 50 product queries.
- Unit tests pass at both refs because customer-visible behavior is unchanged.
- The candidate is a regression when its median query count is at least 10 queries and 5 times
  the baseline. Query count is the primary deterministic signal; latency is supporting evidence.
- The expected repair replaces per-item product lookups with one batched lookup.

The fixture uses the repository's local PostgreSQL 16 container. It requires no hosted service,
paid dependency, or external network at runtime.

## Create the benchmark repository

From `backend/`:

```powershell
docker compose -f infra/compose.yaml up -d postgres
python fixtures/broken-store/scripts/seed_fixture_repo.py .lou/broken-store
python -m pip install -e '.lou/broken-store[test]'
git -C .lou/broken-store switch good
python -m pytest .lou/broken-store/tests
git -C .lou/broken-store switch n-plus-one
python -m pytest .lou/broken-store/tests
```

Set `BROKEN_STORE_DATABASE_URL` when PostgreSQL is not available at
`postgresql://lou:lou@localhost:5432/lou`. The target directory must not already exist. The seed
script fixes author identity and commit dates so the generated commit SHAs are stable.

To exercise PostgreSQL directly, set `BROKEN_STORE_TEST_DATABASE_URL` before pytest. The k6 runner
should invoke `checkout.js` once with `WARMUP=1`, discard that output, and then run each measured
baseline, candidate, and fix repetition without `WARMUP`.

## Handoff metadata

```text
changed symbol:       store.app.Store.checkout
endpoint:             POST /checkout
tables:               broken_store.cart_items, broken_store.products
pytest:               tests/test_checkout.py::test_checkout_receipt_stays_correct
k6 workload ID:       checkout-load
k6 definition:        loadtests/checkout.js
primary signal:       database_queries (median 2 -> 51)
```
