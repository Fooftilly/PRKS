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
MAX_ASSET_CHARS = 8 * 1024 * 1024
MIN_ASSET_CHARS = 128
JAVASCRIPT_TYPES = frozenset({"text/javascript", "application/javascript"})


def valid_browser_asset(raw: str, content_type: str, kind: str) -> bool:
    """Check the bytes *served* by Docker, not just HTML references.

    A missing /js/ path falls back to index.html with HTTP 200. Both the
    content type and the generated build marker must match the requested
    asset so that the fallback (or a truncated/stale body) cannot pass.
    """
    if not isinstance(raw, str) or not isinstance(content_type, str):
        return False
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in JAVASCRIPT_TYPES or not MIN_ASSET_CHARS <= len(raw) <= MAX_ASSET_CHARS:
        return False
    if raw.lstrip().lower().startswith(("<!doctype", "<html", "<")):
        return False
    if kind == "bundle":
        return raw.startswith(";\n/* bundled source: /")
    if kind == "worker":
        return "CLASSIC_BUNDLE_REVISION" in raw
    return False


def valid_initial_works_catalog(raw: str) -> bool:
    try:
        response = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(response, list) and not response


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    kind = None
    content_type = ""
    if args:
        if len(args) != 3 or args[0] != "--asset" or args[1] not in ("bundle", "worker"):
            print("usage: check_runtime_smoke.py [--asset bundle|worker MIME] < RESPONSE", file=sys.stderr)
            return 2
        kind, content_type = args[1:]
    limit = MAX_ASSET_CHARS if kind else MAX_RESPONSE_CHARS
    try:
        raw = sys.stdin.read(limit + 1)
    except (OSError, UnicodeError):
        print("runtime smoke response cannot be read", file=sys.stderr)
        return 1
    if kind:
        if not valid_browser_asset(raw, content_type, kind):
            print("runtime smoke served an invalid browser asset", file=sys.stderr)
            return 1
    elif len(raw) > MAX_RESPONSE_CHARS or not valid_initial_works_catalog(raw):
        print("runtime smoke response is not an empty works catalog", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
