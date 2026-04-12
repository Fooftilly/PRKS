#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
docker build "$@" --label "prks.managed=true" -t prks:latest .
docker image prune -f --filter "label=prks.managed=true"