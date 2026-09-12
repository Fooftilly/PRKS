#!/usr/bin/env sh
# Point this clone at tracked hooks in .githooks/ (local config only).
set -eu

ROOT="$(git rev-parse --show-toplevel)" || {
    echo "setup-git-hooks: not inside a git repository" >&2
    exit 1
}

if [ ! -f "$ROOT/prks_app.py" ] || [ ! -f "$ROOT/.githooks/post-commit" ]; then
    echo "setup-git-hooks: this does not look like the PRKS repository" >&2
    exit 1
fi

git -C "$ROOT" config --local core.hooksPath .githooks

echo "Configured local core.hooksPath=.githooks"
echo "Verify with: git config --get core.hooksPath"
