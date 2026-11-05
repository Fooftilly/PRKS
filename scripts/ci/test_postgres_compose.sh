#!/usr/bin/env bash
# PostgreSQL Compose acceptance (docs/postgresql-architecture.md §12.3, §11.1; L-D-1 #630).
#
# Runs docker-compose.postgres.yml as an operator would: db, migrate, web and
# worker on a fresh data root, first-run setup with `prks_app.py admin`, then
# restarts (web and worker alone too), an upgrade-style migrate beside a running
# stack, database copies refused after the original moved on (by migrate, web
# and worker), a simulated migrate killed between its database bump and its
# marker write (set with psql: no fault hooks in the image), and a recreated
# database volume that bootstrap refuses.
#
# Environment: PRKS_COMPOSE_SKIP_BUILD=1 reuses an existing prks:latest;
# PRKS_COMPOSE_EXTRA_FILE adds one more Compose file (a local image override).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
temp="$(mktemp -d)"
umask 077
project="prkspg${GITHUB_RUN_ID:-$$}${GITHUB_RUN_ATTEMPT:-1}"
port="${PRKS_PUBLISH_PORT:-18080}"

password="$(openssl rand -hex 32)"
printf '%s\n' "$password" > "$temp/db_password"
printf 'postgresql://prks:%s@db:5432/prks\n' "$password" > "$temp/database_url"
mkdir -m 700 "$temp/data"
export PRKS_DB_PASSWORD_FILE="$temp/db_password"
export PRKS_DB_URL_FILE="$temp/database_url"
export PRKS_DATA_DIR="$temp/data"
export PRKS_PUBLISH_PORT="$port"
export UID
GID="$(id -g)"
export GID

# The SQLite Compose file still needs no PostgreSQL secret.
env -u PRKS_DB_PASSWORD_FILE -u PRKS_DB_URL_FILE docker compose --file "$root/docker-compose.yml" config --quiet

compose=(docker compose --project-name "$project" --file "$root/docker-compose.postgres.yml")
if [[ -n "${PRKS_COMPOSE_EXTRA_FILE:-}" ]]; then
  compose+=(--file "$PRKS_COMPOSE_EXTRA_FILE")
fi

cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 )); then
    printf '%s\n' "PostgreSQL Compose acceptance failed; recent logs:"
    "${compose[@]}" logs --tail 40 2>&1 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$temp"
  exit "$status"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() {
  echo "ok: $*"
}

query() {
  # Over the bridge network with the secret, read inside the container.
  "${compose[@]}" exec -T db sh -eu -c '
    PGPASSWORD="$(cat /run/secrets/prks_db_password)"
    export PGPASSWORD
    exec psql --no-psqlrc --tuples-only --no-align \
      --set ON_ERROR_STOP=1 --username prks --dbname "${2:-prks}" \
      --host db --command "$1"
  ' sh "$1" "${2:-prks}"
}

db_state() {
  query "SELECT start_generation || ':' || start_token FROM prks.instance" "${1:-prks}"
}

marker_state() {
  python3 -c '
import json, sys
doc = json.load(open(sys.argv[1]))
print("%s:%s" % (doc.get("start_generation", 0), doc.get("start_token", "")))
' "$temp/data/prks-root.json"
}

readyz() {
  curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 \
    "http://127.0.0.1:$port/readyz" || true
}

wait_for() {
  # wait_for SECONDS DESCRIPTION COMMAND...
  local deadline=$((SECONDS + $1)) what=$2
  shift 2
  until "$@"; do
    (( SECONDS < deadline )) || fail "timed out waiting for $what"
    sleep 2
  done
}

is_ready() { [[ "$(readyz)" == 200 ]]; }
is_healthy() {
  [[ "$(docker inspect --format '{{.State.Health.Status}}' "$("${compose[@]}" ps -q web)")" == healthy ]]
}
is_paired() { [[ "$(db_state)" == "$(marker_state)" ]]; }

stale_start() {
  # stale_start SERVICE DATABASE ROLE...: runs prks_app.py ROLE in a one-off
  # SERVICE container against DATABASE (a copy) instead of prks.
  local service=$1
  shift
  "${compose[@]}" run --rm --no-deps --entrypoint sh "$service" -c '
    copy=$1
    shift
    PRKS_DATABASE_URL="$(sed "s#/prks\$#/$copy#" /run/secrets/prks_database_url)" \
    PRKS_DATABASE_URL_FILE= exec python /app/prks_app.py "$@"
  ' sh "$@"
}

refuses_stale() {
  # refuses_stale SERVICE DATABASE ROLE...: the start fails as a stale copy and
  # changes neither the copy nor the marker.
  local copy_before refusal
  copy_before=$(db_state "$2")
  if refusal=$(stale_start "$@" 2>&1); then
    fail "$1 started on the stale copy $2"
  fi
  grep -q "stale copy" <<<"$refusal" || fail "$1 refused $2 for another reason: $refusal"
  [[ "$(db_state "$2")" == "$copy_before" ]] || fail "$1 bumped the refused copy $2"
  is_paired || fail "$1 on the refused copy $2 changed the marker"
}

wait_settled() {
  # web ready and healthy, every start done: database and marker equal, at
  # generation $1.
  local deadline database marker
  wait_for 120 "web to be ready" is_ready
  wait_for 120 "web to be healthy" is_healthy
  deadline=$((SECONDS + 60))
  while true; do
    database=$(db_state)
    marker=$(marker_state)
    [[ "${database%%:*}" == "$1" && "$database" == "$marker" ]] && return 0
    (( SECONDS < deadline )) \
      || fail "generation $1 on both sides; database ${database%%:*}, marker ${marker%%:*}"
    sleep 2
  done
}

if [[ "${PRKS_COMPOSE_SKIP_BUILD:-0}" != 1 ]]; then
  "${compose[@]}" build web
fi
"${compose[@]}" config --quiet

# --- first run --------------------------------------------------------------------

"${compose[@]}" up --detach
db=$("${compose[@]}" ps -q db)
mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql"}}{{.Type}}:{{.Name}}{{end}}{{end}}' "$db")
[[ "$mount" == "volume:${project}_pgdata" ]] || fail "db does not use the named volume at /var/lib/postgresql"
bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$db")
[[ "$bindings" == "null" || "$bindings" == "{}" ]] || fail "db publishes a host port"
for service in web worker; do
  container=$("${compose[@]}" ps -q "$service")
  secret=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/run/secrets/prks_database_url"}}yes{{end}}{{end}}' "$container")
  [[ "$secret" == yes ]] || fail "$service does not list the prks_database_url secret"
done
pass "volume at /var/lib/postgresql, no db host port, web and worker list the URL secret"

# The bridge interface needs the password (never trust), and the cluster has
# the builtin locale, which a restart never changes on an existing volume.
if "${compose[@]}" exec -T db sh -eu -c '
  export PGPASSWORD="definitely-not-the-generated-password"
  exec psql --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --username prks --dbname prks --host db --command "SELECT 1"
' >/dev/null 2>&1; then
  fail "db accepted an incorrect password on its bridge interface"
fi
[[ "$(query "SELECT current_setting('server_version_num')::int / 10000")" == 18 ]] || fail "not PostgreSQL 18"
[[ "$(query "SELECT datlocprovider::text || '|' || datlocale FROM pg_database WHERE datname = current_database()")" \
   == "b|PG_UNICODE_FAST" ]] || fail "db does not use the builtin PG_UNICODE_FAST locale"
pass "db refuses a wrong password; PostgreSQL 18 with the builtin locale"

# migrate applied the schema and left the root alone: nothing is set up yet.
[[ "$(query "SELECT count(*) FROM prks.instance")" == 0 ]] || fail "an instance row before setup"
wait_for 60 "web to bind the new root" test -f "$temp/data/prks-root.json"
[[ "$(readyz)" != 200 ]] || fail "web is ready before setup"
"${compose[@]}" ps --status running --services | grep -qx worker || fail "the worker is not waiting"
pass "first run: schema applied, root bound by web, web not ready and worker waiting before setup"

# --- setup -------------------------------------------------------------------------

token="$(openssl rand -hex 32)"
printf '%s' "$token" | "${compose[@]}" exec -T web sh -c 'umask 077; cat > /tmp/bootstrap-token'
printf '%s' "owner-password-$(openssl rand -hex 8)" \
  | "${compose[@]}" exec -T web sh -c 'umask 077; cat > /tmp/owner-password'
admin=("${compose[@]}" exec -T -e PRKS_BOOTSTRAP_TOKEN_FILE=/tmp/bootstrap-token web
       python /app/prks_app.py admin bootstrap)
"${admin[@]}" issue-token --root /data
"${admin[@]}" complete --root /data --login owner --password-file /tmp/owner-password
# The claim is generation 1; web and the worker each bump once when they see setup ready.
wait_settled 3
pass "bootstrap; web ready and healthy; database and marker at generation 3"
[[ "$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:$port/healthz")" == 200 ]] \
  || fail "/healthz"

# --- an upgrade-style migrate beside the running stack ------------------------------

out=$("${compose[@]}" run --rm --no-deps migrate)
grep -q "start generation 4" <<<"$out" || fail "migrate beside web and worker did not bump: $out"
wait_settled 4
pass "migrate runs and bumps under the shared lease while web and worker hold it"

# --- restart; a copy taken before it is refused afterwards --------------------------

"${compose[@]}" stop web worker
query "CREATE DATABASE prks_copy TEMPLATE prks" >/dev/null
"${compose[@]}" down
"${compose[@]}" up --detach
# migrate, web and worker each bump once.
wait_settled 7
[[ "$(query "SELECT count(*) FROM prks.users")" == 1 ]] || fail "the owner did not survive down/up"
pass "down/up keeps the database volume; every start bumped once (generation 7)"

refuses_stale migrate prks_copy migrate
pass "a database copy taken before the restart is refused against the root"

"${compose[@]}" up --detach --force-recreate
wait_settled 10
pass "container recreation keeps the data; generation 10"

# web and worker restarted without migrate still move the marker, one bump each.
"${compose[@]}" restart web worker
wait_settled 12
pass "restart of web and worker alone bumps database and marker (generation 12)"

# --- migrate killed between its database bump and its marker write ----------------

# A copy taken before the interrupted bump. web and worker come back without
# migrate (`compose start` would also start the finished migrate again).
"${compose[@]}" stop web worker
query "CREATE DATABASE prks_before_bump TEMPLATE prks" >/dev/null
"${compose[@]}" up --detach --no-deps web worker
wait_settled 14

query "UPDATE prks.instance SET prev_start_token = start_token, start_token = md5(random()::text), \
       start_generation = start_generation + 1" >/dev/null
is_not_ready() { [[ "$(readyz)" == 503 ]]; }
wait_for 30 "web to turn not ready" is_not_ready
out=$("${compose[@]}" run --rm --no-deps migrate)
grep -q "reconciled an interrupted start" <<<"$out" || fail "migrate did not reconcile: $out"
wait_settled 16
pass "a bump without its marker write keeps web not ready until the next start reconciles it"

# The copy from before that bump is now an older cluster: every role refuses it.
# A second web would first meet the running web's content lock, so web stops.
refuses_stale migrate prks_before_bump migrate
refuses_stale worker prks_before_bump worker
"${compose[@]}" stop web
refuses_stale web prks_before_bump serve --host 0.0.0.0
"${compose[@]}" up --detach --no-deps web
wait_settled 17
pass "a copy taken before the interrupted bump is refused by migrate, worker and web"

# --- a recreated database volume: bootstrap refuses the used root -------------------

"${compose[@]}" down
docker volume rm "${project}_pgdata" >/dev/null
marker_before=$(sha256sum "$temp/data/prks-root.json")
"${compose[@]}" up --detach
schema_applied() { [[ "$(query "SELECT count(*) FROM prks.schema_migrations" 2>/dev/null)" =~ ^[1-9] ]]; }
wait_for 60 "migrate to apply the schema" schema_applied
token="$(openssl rand -hex 32)"
printf '%s' "$token" | "${compose[@]}" exec -T web sh -c 'umask 077; cat > /tmp/bootstrap-token'
printf '%s' "owner-password-$(openssl rand -hex 8)" \
  | "${compose[@]}" exec -T web sh -c 'umask 077; cat > /tmp/owner-password'
"${admin[@]}" issue-token --root /data
if refusal=$("${admin[@]}" complete --root /data --login owner --password-file /tmp/owner-password 2>&1); then
  fail "bootstrap claimed a root an earlier database used"
fi
# Refused for the marker's instance id: the root holds no stored file yet.
grep -q "belongs to an earlier database" <<<"$refusal" \
  || fail "bootstrap refused the used root for another reason: $refusal"
holds=$("${compose[@]}" exec -T web python -c \
  'from backend.storage.objects import root_holds_objects; print(root_holds_objects("/data"))')
[[ "$holds" == False ]] || fail "the root holds stored objects, so the refusal does not isolate the marker"
[[ "$(sha256sum "$temp/data/prks-root.json")" == "$marker_before" ]] || fail "the used root's marker changed"
[[ "$(readyz)" != 200 ]] || fail "web is ready against an empty database"
pass "a recreated database volume: bootstrap refuses the used root and leaves it untouched"

echo "PostgreSQL Compose acceptance PASS"
