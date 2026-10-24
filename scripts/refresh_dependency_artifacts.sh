#!/usr/bin/env bash
# Maintainer-only dependency refresh. Never invoked at PRKS application startup.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

case "${1:-}" in
  frontend-app) target="frontend-app" ;;
  frontend-vendor) target="tools/frontend-vendor" ;;
  research-graph) target="tools/research-graph" ;;
  pdf-viewer) target="tools/pdf-viewer" ;;
  *)
    echo "Usage: bash scripts/refresh_dependency_artifacts.sh {frontend-app|frontend-vendor|research-graph|pdf-viewer}" >&2
    exit 2
    ;;
esac

# EmbedPDF applies reviewed local patches in postinstall; other islands do not
# need package lifecycle scripts during their installs.
if [[ "$target" == "tools/pdf-viewer" ]]; then
  npm ci --no-audit --prefix "$target"
else
  npm ci --ignore-scripts --no-audit --prefix "$target"
fi
npm run build --prefix "$target"
if [[ "$target" == "tools/pdf-viewer" ]]; then
  npm run guard --prefix "$target"
fi
# Verify all derived notices, hashes, and the service-worker revision.
python3 scripts/dependency_gate.py --repo
git status --short
