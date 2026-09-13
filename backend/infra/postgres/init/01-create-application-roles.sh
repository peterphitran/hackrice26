#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname postgres \
  --set=runtime_password="$LOU_RUNTIME_PASSWORD" \
  --set=test_app_password="$LOU_TEST_APP_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE lou LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
    :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lou')
\gexec

SELECT format(
    'CREATE ROLE lou_test_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
    :'test_app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lou_test_app')
\gexec
SQL

if ! psql --username "$POSTGRES_USER" --dbname postgres --tuples-only --no-align \
  --command "SELECT 1 FROM pg_database WHERE datname = 'lou_test'" | grep -q 1; then
  createdb --username "$POSTGRES_USER" --owner "$POSTGRES_USER" lou_test
fi

psql --username "$POSTGRES_USER" --dbname postgres <<'SQL'
GRANT CONNECT ON DATABASE lou TO lou;
GRANT CONNECT ON DATABASE lou_test TO lou_test_app;
SQL
