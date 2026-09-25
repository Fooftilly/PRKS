#!/usr/bin/env python3
"""PRKS dependency consistency gate (CLI).

Modes:
  --runtime         Validate the Python environment needed to start PRKS.
  --unit-contract   Runtime + openapi-core only (unit suite; no Playwright).
  --test            Runtime + Playwright (and other requirements-dev pins).
  --repo            Manifests, locks, vendor hashes, SW revision (offline; no npm install required).

Optional:
  --check-latest   Network freshness probe (never used by startup/tests/--repo).
  --write-manifest Regenerate frontend/vendor/DEPENDENCY-MANIFEST.json and SW revision.

Normal startup, tests, and --repo never contact external registries.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.dependency_gate import (  # noqa: E402
    format_gate_report,
    run_repo_gate,
    run_runtime_gate,
    run_test_gate,
    run_unit_contract_gate,
    write_dependency_manifest,
    write_sw_dependency_revision,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--runtime", action="store_true", help="Python runtime pins for starting PRKS")
    mode.add_argument(
        "--unit-contract",
        action="store_true",
        help="Runtime + openapi-core (unit/API contract tests; no Playwright)",
    )
    mode.add_argument("--test", action="store_true", help="Runtime + test (Playwright) pins")
    mode.add_argument(
        "--repo",
        action="store_true",
        help="Repository consistency (offline; no npm install required)",
    )
    mode.add_argument(
        "--check-latest",
        action="store_true",
        help="Optional network freshness probe (not for startup/tests/--repo)",
    )
    mode.add_argument(
        "--write-manifest",
        action="store_true",
        help="Rewrite DEPENDENCY-MANIFEST.json and sw.js DEPENDENCY_REVISION",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root (default: detected)",
    )
    return parser


def check_latest(repo_root: Path) -> int:
    """Explicit network freshness helper. Distinguishes API failure vs updates."""
    try:
        import urllib.request
    except ImportError:
        print("urllib unavailable", file=sys.stderr)
        return 2

    # Only probe PyPI for runtime pins — never during startup.
    from backend.dependency_gate import runtime_requirement_pins

    pins = runtime_requirement_pins(repo_root)
    updates = []
    api_failures = []
    for name, pinned in pins.items():
        url = f"https://pypi.org/pypi/{name}/json"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = data.get("info", {}).get("version")
            if latest and latest != pinned:
                updates.append(f"{name}: pinned {pinned}, latest {latest}")
        except Exception as exc:
            api_failures.append(f"{name}: API failure ({type(exc).__name__})")

    if api_failures:
        print("Freshness check: API failures (could not determine updates):", file=sys.stderr)
        for line in api_failures:
            print("  " + line, file=sys.stderr)
        if updates:
            print("Known updates before failure:", file=sys.stderr)
            for line in updates:
                print("  " + line, file=sys.stderr)
        return 2
    if updates:
        print("Updates available:")
        for line in updates:
            print("  " + line)
        return 1
    print("Freshness check: all probed runtime pins match latest on PyPI")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()

    if args.write_manifest:
        path = write_dependency_manifest(root)
        rev = write_sw_dependency_revision(root)
        print(f"Wrote {path}")
        print(f"Updated sw.js DEPENDENCY_REVISION={rev}")
        return 0

    if args.check_latest:
        return check_latest(root)

    if args.runtime:
        result = run_runtime_gate(repo_root=root)
    elif args.unit_contract:
        result = run_unit_contract_gate(repo_root=root)
    elif args.test:
        result = run_test_gate(repo_root=root)
    else:
        result = run_repo_gate(repo_root=root)

    print(format_gate_report(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
