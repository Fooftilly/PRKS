#!/usr/bin/env python3
"""Generate a diagnostic, never-committed classic-script deferral variant.

Only the manual E2E Script Delivery A/B workflow uses this tool. It copies
production index.html to an external temporary file and changes *only* the
loading attribute of classic external scripts. It never rewrites production
assets, module tags, their order, or application JavaScript.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

_SCRIPT_TAG = re.compile(r'<script(?P<attrs>\s+[^>]*)></script>')
_SRC = re.compile(r'\bsrc="(/[^"]+)"')
_TYPE = re.compile(r'\btype="([^"]+)"')
_ATTRIBUTE = re.compile(r'\b(?:defer|async)(?=\s|$)')


def deferred_index(original: str) -> tuple[str, int]:
    """Add defer only to classic external shell script tags, preserving order."""
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        attrs = match.group("attrs")
        if _SRC.search(attrs) is None:
            return match.group()
        type_match = _TYPE.search(attrs)
        if type_match is not None:
            # Module scripts already defer by default; do not change them.
            if type_match.group(1) == "module":
                return match.group()
            raise ValueError("unexpected external script type")
        if _ATTRIBUTE.search(attrs):
            raise ValueError("classic script already has async/defer attribute")
        count += 1
        return "<script defer" + attrs + "></script>"

    result = _SCRIPT_TAG.sub(replace, original)
    # Fail closed on HTML structure drift instead of measuring an accidental
    # partially-transformed page. The measured reference has 84 classic tags.
    if count != 84:
        raise ValueError(f"expected 84 classic external scripts; found {count}")
    source_order = re.findall(r'<script[^>]*\bsrc="([^"]+)"', original)
    transformed_order = re.findall(r'<script[^>]*\bsrc="([^"]+)"', result)
    if source_order != transformed_order:
        raise ValueError("script source order changed")
    return result, count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    original = args.input.read_text(encoding="utf-8")
    candidate, n = deferred_index(original)
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be distinct files")
    args.output.write_text(candidate, encoding="utf-8")
    print(f"Prepared deferred-script diagnostic variant ({n} classic scripts)")


if __name__ == "__main__":
    main()
