#!/usr/bin/env python3
"""Write (or ``--check``) ``docs/api/openapi.json`` from the route table (#1431).

The document is ``backend.api_contract.openapi.openapi_document()``: every
typed route of ``backend.api.ROUTES``. After changing a route's contract or a
boundary model, run this and ``npm run openapi:types`` in ``frontend-app``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARTIFACT = ROOT / "docs" / "api" / "openapi.json"


def render() -> str:
    from backend.api_contract.openapi import openapi_document

    return json.dumps(openapi_document(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check docs/api/openapi.json.")
    parser.add_argument("--check", action="store_true", help="fail when the checked-in file differs")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = ARTIFACT.read_text(encoding="utf-8") if ARTIFACT.is_file() else ""
        if current != text:
            print(f"{ARTIFACT.relative_to(ROOT)} is stale: run python scripts/write_openapi.py", file=sys.stderr)
            return 1
        return 0
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
