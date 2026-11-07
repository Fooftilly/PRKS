#!/usr/bin/env python3
"""Validate the production Docker API readiness response, not just HTTP 200.

Read a small JSON response from standard input. The fresh volatile storage
must return exactly an empty works array; reject errors or other payloads.
Do not accept caller-selected filesystem paths or log response contents.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys

MAX_RESPONSE_CHARS = 65536
MAX_ASSET_BYTES = 8 * 1024 * 1024
MIN_ASSET_BYTES = 128
JAVASCRIPT_TYPES = frozenset({"text/javascript", "application/javascript"})
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


def valid_browser_asset(
    raw: bytes, content_type: str, kind: str, expected_sha256: str
) -> bool:
    """Validate the exact bytes served against the built container's artifact.

    Missing static assets may fall back to index.html with HTTP 200. Checking
    the MIME type and build marker catches that substitution; comparing the
    full SHA-256 to the file *inside the same Docker image* also catches
    otherwise plausible truncated or stale JavaScript responses.
    """
    if not isinstance(raw, bytes) or not isinstance(content_type, str):
        return False
    if not isinstance(expected_sha256, str) or SHA256_HEX.fullmatch(expected_sha256) is None:
        return False
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in JAVASCRIPT_TYPES or not MIN_ASSET_BYTES <= len(raw) <= MAX_ASSET_BYTES:
        return False
    if raw.lstrip().lower().startswith(b"<"):
        return False
    if kind == "bundle":
        if not raw.startswith(b";\n/* bundled source: /"):
            return False
    elif kind == "worker":
        if b"CLASSIC_BUNDLE_REVISION" not in raw:
            return False
    else:
        return False
    return hashlib.sha256(raw).hexdigest() == expected_sha256


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
    expected_sha256 = ""
    if args:
        if len(args) != 4 or args[0] != "--asset" or args[1] not in ("bundle", "worker"):
            print(
                "usage: check_runtime_smoke.py [--asset bundle|worker MIME SHA256] < RESPONSE",
                file=sys.stderr,
            )
            return 2
        kind, content_type, expected_sha256 = args[1:]
    try:
        if kind:
            # Do not decode or normalize line endings: compare the actual
            # HTTP bytes with the exact built artifact, with a fixed size cap.
            raw_bytes = sys.stdin.buffer.read(MAX_ASSET_BYTES + 1)
            if not valid_browser_asset(raw_bytes, content_type, kind, expected_sha256):
                print("runtime smoke served an invalid browser asset", file=sys.stderr)
                return 1
        else:
            raw = sys.stdin.read(MAX_RESPONSE_CHARS + 1)
            if len(raw) > MAX_RESPONSE_CHARS or not valid_initial_works_catalog(raw):
                print("runtime smoke response is not an empty works catalog", file=sys.stderr)
                return 1
    except (OSError, UnicodeError, AttributeError):
        print("runtime smoke response cannot be read", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
