#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
docker build "$@" -t prks:latest .
docker image prune -f
