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


def _defer_script_lines(original: str) -> tuple[str, int]:
    changed = []
    module_lines = []
    count = 0
    found_body_end = False
    for line in original.splitlines(keepends=True):
        tag = line.strip()
        if tag == "</body>":
            found_body_end = True
            changed.extend(module_lines)
            changed.append(line)
        elif tag.startswith('<script type="module" src="/') and tag.endswith(
            '"></script>'
        ):
            # Baseline executes *all* parser-blocking classics before modules.
            # Append the unmodified modules after deferred classics to retain
            # that ordering, rather than introducing a second experiment.
            module_lines.append(line)
        elif tag.startswith('<script src="/') and tag.endswith('"></script>'):
            count += 1
            changed.append(line.replace("<script ", "<script defer ", 1))
        elif tag.startswith("<script"):
            raise ValueError("unsupported or already-deferred script tag")
        else:
            changed.append(line)
    if not found_body_end:
        raise ValueError("missing closing body tag in benchmark source")
    return "".join(changed), count


def _verify_script_tags(before: list, result: str, count: int) -> None:
    classics = [a for a in before if dict(a).get("type") != "module"]
    modules = [a for a in before if dict(a).get("type") == "module"]
    expected = [[("defer", None), *attrs] for attrs in classics] + modules
    if _script_tags(result) != expected:
        raise ValueError("classic/module script attributes or order changed")
    if len(classics) != count:
        raise ValueError("transformed classic script count disagrees")


def deferred_index(original: str) -> tuple[str, int]:
    """Defer classic scripts but preserve classic-before-module evaluation."""
    before = _script_tags(original)
    if any("async" in dict(a) or "defer" in dict(a) for a in before):
        raise ValueError("script already has async/defer attribute")
    result, count = _defer_script_lines(original)
    if result == original:
        raise ValueError("no classic external scripts were found")
    _verify_script_tags(before, result, count)
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
