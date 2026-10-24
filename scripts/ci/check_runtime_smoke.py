#!/usr/bin/env python3
"""Validate the production Docker API readiness response, not just HTTP 200.

The runtime smoke uses fresh volatile storage and no authentication. The
expected /api/works payload is exactly an empty JSON array. Do not log the
response body or treat error objects, HTML, or nonempty data as healthy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def valid_initial_works_catalog(raw: str) -> bool:
    try:
        response = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(response, list) and not response


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: check_runtime_smoke.py RESPONSE_FILE", file=sys.stderr)
        return 2
    try:
        raw = Path(args[0]).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("runtime smoke response cannot be read", file=sys.stderr)
        return 1
    if not valid_initial_works_catalog(raw):
        print("runtime smoke response is not an empty works catalog", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
