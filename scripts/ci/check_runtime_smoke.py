#!/usr/bin/env python3
"""Validate the production Docker API readiness response, not just HTTP 200.

Read a small JSON response from standard input. The fresh volatile storage
must return exactly an empty works array; reject errors or other payloads.
Do not accept caller-selected filesystem paths or log response contents.
"""

from __future__ import annotations

import json
import sys

MAX_RESPONSE_CHARS = 65536


def valid_initial_works_catalog(raw: str) -> bool:
    try:
        response = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(response, list) and not response


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("usage: check_runtime_smoke.py < RESPONSE_FILE", file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read(MAX_RESPONSE_CHARS + 1)
    except (OSError, UnicodeError):
        print("runtime smoke response cannot be read", file=sys.stderr)
        return 1
    if len(raw) > MAX_RESPONSE_CHARS or not valid_initial_works_catalog(raw):
        print("runtime smoke response is not an empty works catalog", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
