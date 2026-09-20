#!/usr/bin/env bash
# Resolve a Python 3 interpreter the way PRKS expects on POSIX / Git Bash:
# prefer python3, then python, then the Windows py launcher. Never require Node.
set -euo pipefail

HOOK="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/run_hook.py"

if ! command -v sonar >/dev/null 2>&1; then
  exit 0
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT" "$HOOK"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT" "$HOOK"
fi
if command -v py >/dev/null 2>&1; then
  exec py -3 "$SCRIPT" "$HOOK"
fi
# No interpreter on PATH — same soft no-op as a missing sonar CLI.
exit 0
