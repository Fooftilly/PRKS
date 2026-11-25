#!/usr/bin/env python3
"""High-signal architectural invariant checks for production Python.

This is intentionally narrower than a general linter. It protects bug classes
where PRKS has a canonical capability boundary and where a direct low-level
call is almost certainly a regression.

Keep semantic invariants in tests; add checks here only when the forbidden
syntax has a clear approved replacement/boundary.

The checks live in ``scripts/invariants/`` by topic: ``boundaries`` (the
reviewed homes of each primitive), ``provenance`` (value tracking),
``code_rules`` (the AST rules), ``pyright_slice`` and ``schema_snapshots``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.invariants.code_rules import (  # noqa: E402
    check_repo,
    check_source,
    iter_production_python,
)
from scripts.invariants.findings import REPO_ROOT, Finding  # noqa: E402
from scripts.invariants.pyright_slice import (  # noqa: E402
    PYRIGHT_TYPED_SLICE_INCLUDE,
    PYRIGHT_TYPED_SLICE_SCOPES,
    PYRIGHT_TYPED_SLICE_STRICT,
    check_pyright_configs,
)
from scripts.invariants.schema_snapshots import (  # noqa: E402
    SCHEMA_VERSION_SNAPSHOT_ALLOWLIST,
    check_test_schema_snapshots,
    check_test_schema_snapshots_repo,
)

__all__ = [
    "PYRIGHT_TYPED_SLICE_INCLUDE",
    "PYRIGHT_TYPED_SLICE_SCOPES",
    "PYRIGHT_TYPED_SLICE_STRICT",
    "REPO_ROOT",
    "SCHEMA_VERSION_SNAPSHOT_ALLOWLIST",
    "Finding",
    "check_pyright_configs",
    "check_repo",
    "check_source",
    "check_test_schema_snapshots",
    "check_test_schema_snapshots_repo",
    "iter_production_python",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (defaults to the checker script's parent repository)",
    )
    args = parser.parse_args(argv)

    findings = check_repo(args.root.resolve())
    findings.extend(check_pyright_configs(args.root.resolve()))
    findings.extend(check_test_schema_snapshots_repo(args.root.resolve()))
    if findings:
        for finding in findings:
            print(finding.render())
        print(f"engineering invariant check failed: {len(findings)} violation(s)")
        return 1

    print("engineering invariant check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
