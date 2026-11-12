#!/bin/sh
# The container runs one PRKS role: serve (the default command, bound to every
# container interface so Docker's port forwarding reaches it), migrate, worker
# or admin. PostgreSQL is the only Library backend: every role needs
# PRKS_DATABASE_URL or PRKS_DATABASE_URL_FILE (docker-compose.yml).
set -e
exec python /app/prks_app.py "$@"
