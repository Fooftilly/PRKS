#!/usr/bin/env python3
"""Generate a diagnostic, never-committed classic-script deferral variant.

Only the manual E2E Script Delivery A/B workflow uses this tool. It copies
production index.html to an external temporary file and changes *only* the
loading attribute of classic external scripts. It never rewrites production
assets, module tags, their order, or application JavaScript.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path


class _ScriptReader(HTMLParser):
    """Independently verify script attributes/order after the line-based edit."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[list[tuple[str, str | None]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "script":
            self.scripts.append(attrs)


def _script_tags(text: str) -> list[list[tuple[str, str | None]]]:
    reader = _ScriptReader()
    reader.feed(text)
    return reader.scripts


def deferred_index(original: str) -> tuple[str, int]:
    """Add defer to classic external script lines, preserving bytewise order."""
    changed = []
    count = 0
    for line in original.splitlines(keepends=True):
        tag = line.strip()
        if not tag.startswith("<script"):
            changed.append(line)
        elif tag.startswith('<script type="module" src="/') and tag.endswith(
            '"></script>'
        ):
            # Module scripts already defer implicitly; leave these untouched.
            changed.append(line)
        elif tag.startswith('<script src="/') and tag.endswith('"></script>'):
            count += 1
            changed.append(line.replace("<script ", "<script defer ", 1))
        elif "defer" in tag or "async" in tag:
            raise ValueError("classic script already has async/defer attribute")
        else:
            # Reject new inline/attribute variations rather than applying an
            # unreliable partial HTML transformation.
            raise ValueError("unsupported script tag in benchmark source")

    result = "".join(changed)
    if result == original:
        raise ValueError("no classic external scripts were found")

    before = _script_tags(original)
    after = _script_tags(result)
    if len(before) != len(after):
        raise ValueError("script tag list changed")
    verified = 0
    for old_attrs, new_attrs in zip(before, after):
        original_type = dict(old_attrs).get("type")
        if original_type == "module":
            if new_attrs != old_attrs:
                raise ValueError("module script was modified")
        elif old_attrs and old_attrs[0][0] == "src":
            if new_attrs != [("defer", None), *old_attrs]:
                raise ValueError("classic script attributes/order changed")
            verified += 1
        else:
            raise ValueError("unrecognized script tag in benchmark source")
    if verified != count:
        raise ValueError("transformed classic script count disagrees")
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
