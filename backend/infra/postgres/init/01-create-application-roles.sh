#!/bin/sh
set -eu

if ! psql --username "$POSTGRES_USER" --dbname postgres --tuples-only --no-align \
  --command "SELECT 1 FROM pg_roles WHERE rolname = 'lou_app'" | grep -q 1; then
  psql --username "$POSTGRES_USER" --dbname postgres --set=app_password="$LOU_APP_PASSWORD" \
    --command "CREATE ROLE lou_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password'"
fi

if ! psql --username "$POSTGRES_USER" --dbname postgres --tuples-only --no-align \
  --command "SELECT 1 FROM pg_roles WHERE rolname = 'lou_test_app'" | grep -q 1; then
  psql --username "$POSTGRES_USER" --dbname postgres --set=test_app_password="$LOU_TEST_APP_PASSWORD" \
    --command "CREATE ROLE lou_test_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'test_app_password'"
fi

if ! psql --username "$POSTGRES_USER" --dbname postgres --tuples-only --no-align \
  --command "SELECT 1 FROM pg_database WHERE datname = 'lou_test'" | grep -q 1; then
  createdb --username "$POSTGRES_USER" --owner "$POSTGRES_USER" lou_test
fi

psql --username "$POSTGRES_USER" --dbname postgres <<'SQL'
GRANT CONNECT ON DATABASE lou TO lou_app;
GRANT CONNECT ON DATABASE lou_test TO lou_test_app;
SQL
