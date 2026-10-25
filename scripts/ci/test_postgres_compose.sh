#!/usr/bin/env bash
# Opt-in PostgreSQL 18 Compose database smoke (#630). Never launches PRKS web.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
temp="$(mktemp -d)"
umask 077
export PRKS_DB_PASSWORD_FILE="$temp/db_password"
openssl rand -hex 32 > "$PRKS_DB_PASSWORD_FILE"
project="prkspg${GITHUB_RUN_ID:-$$}${GITHUB_RUN_ATTEMPT:-1}"
# Ensure ordinary SQLite-only Compose startup does not need a PostgreSQL secret.
env -u PRKS_DB_PASSWORD_FILE docker compose --file "$root/docker-compose.yml" config --quiet
compose=(docker compose --project-name "$project" --file "$root/docker-compose.yml" \
  --file "$root/docker-compose.postgres.yml" --profile postgres)

cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 )); then
    printf '%s\n' "PostgreSQL Compose volume smoke failed; recent database logs:"
    "${compose[@]}" logs --tail 35 db 2>&1 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$temp"
  exit "$status"
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" up --detach --wait --wait-timeout 120 db
container=$("${compose[@]}" ps -q db)
if [[ -z "$container" ]]; then
  echo "No PostgreSQL container started" >&2
  exit 1
fi
# Verify the exact Compose-managed named volume, not merely a directory
# mounted at the expected path (a bind mount could survive by accident).
mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql"}}{{.Type}}:{{.Name}}{{end}}{{end}}' "$container")
if [[ "$mount" != "volume:${project}_pgdata" ]]; then
  echo "PostgreSQL does not use the expected named volume at /var/lib/postgresql" >&2
  exit 1
fi
bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$container")
if [[ "$bindings" != "null" && "$bindings" != "{}" ]]; then
  echo "PostgreSQL unexpectedly publishes a host port" >&2
  exit 1
fi
if [[ -n "$("${compose[@]}" ps -q prks)" ]]; then
  echo "Database-only start unexpectedly launched the SQLite PRKS service" >&2
  exit 1
fi

query() {
  # Authenticate over TCP with the actual Compose secret, never a socket's
  # implicit local-trust rule. The password is read inside the container, not
  # passed as a host shell argument or logged.
  "${compose[@]}" exec -T db sh -eu -c '
    password="$(cat /run/secrets/prks_db_password)"
    export PGPASSWORD="$password"
    exec psql --no-psqlrc --tuples-only --no-align \
      --set ON_ERROR_STOP=1 --username prks --dbname prks \
      --host 127.0.0.1 --command "$1"
  ' sh "$1"
}

major=$(query "SELECT current_setting('server_version_num')::int / 10000")
if [[ "$major" != 18 ]]; then
  echo "Expected PostgreSQL 18; got an unexpected major version" >&2
  exit 1
fi
# Cluster creation locale is persistent; a restart will NOT apply changed
# POSTGRES_INITDB_ARGS to an existing PostgreSQL volume.
locale=$(query "SELECT datlocprovider::text || '|' || datlocale FROM pg_database WHERE datname = current_database()")
if [[ "$locale" != "b|PG_UNICODE_FAST" ]]; then
  echo "PostgreSQL database does not use builtin PG_UNICODE_FAST locale" >&2
  exit 1
fi
query "CREATE TABLE public.pg_compose_smoke (id integer PRIMARY KEY, marker text NOT NULL)" >/dev/null
query "INSERT INTO public.pg_compose_smoke VALUES (1, 'kept')" >/dev/null
check_marker() {
  if [[ "$(query 'SELECT marker FROM public.pg_compose_smoke WHERE id = 1')" != 'kept' ]]; then
    echo "PostgreSQL lost the persisted marker row" >&2
    exit 1
  fi
}
check_marker

# Compose down without --volumes must keep the PostgreSQL 18 named volume.
"${compose[@]}" down --remove-orphans
"${compose[@]}" up --detach --wait --wait-timeout 120 db
check_marker

# Removing and recreating the container must not affect the named volume.
old_container=$("${compose[@]}" ps -q db)
"${compose[@]}" rm --force --stop db >/dev/null
"${compose[@]}" up --detach --wait --wait-timeout 120 db
new_container=$("${compose[@]}" ps -q db)
if [[ -z "$new_container" || "$old_container" == "$new_container" ]]; then
  echo "PostgreSQL container was not recreated" >&2
  exit 1
fi
check_marker

echo "PostgreSQL 18 Compose: secret mount, internal network, volume path, down/up and container recreation PASS"
