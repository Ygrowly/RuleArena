#!/usr/bin/env bash
set -Eeuo pipefail

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=control_password="$CONTROL_DB_PASSWORD" \
  --set=sandbox_password="$SANDBOX_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE rulearena_control LOGIN PASSWORD %L', :'control_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rulearena_control') \gexec
SELECT format('CREATE ROLE rulearena_sandbox LOGIN PASSWORD %L', :'sandbox_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rulearena_sandbox') \gexec

REVOKE ALL ON DATABASE rulearena FROM PUBLIC;
GRANT CONNECT ON DATABASE rulearena TO rulearena_control, rulearena_sandbox;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA IF NOT EXISTS control AUTHORIZATION rulearena_control;
CREATE SCHEMA IF NOT EXISTS sandbox AUTHORIZATION rulearena_sandbox;
REVOKE ALL ON SCHEMA control, sandbox FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA control TO rulearena_control;
GRANT USAGE, CREATE ON SCHEMA sandbox TO rulearena_sandbox;
ALTER ROLE rulearena_control IN DATABASE rulearena SET search_path = control;
ALTER ROLE rulearena_sandbox IN DATABASE rulearena SET search_path = sandbox;
SQL

