#!/usr/bin/env python3
"""Strict head/body inventory and in-memory candidate for PRKS classic scripts.

Builds two classic script bundles at the original parser-blocking head/body
boundaries, preserving script source order and deferring neither classics nor
existing modules. It never changes the committed frontend;
``scripts.build_classic_distribution`` writes the packaged output from it.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HEAD_URL = "/__e2e_classic_head_bundle.js"
BODY_URL = "/__e2e_classic_body_bundle.js"
# Reviewed classic inventory for the source checkout. A new or removed
# classic script must update this and the distribution gate together.
EXPECTED_CLASSIC_COUNTS = {"head": 5, "body": 80}
CLASSIC_PREFIX = '<script src="'
MODULE_PREFIX = '<script type="module" src="'
SCRIPT_SUFFIX = '"></script>'
SAFE_SOURCE = re.compile(r"/[a-zA-Z0-9_./-]+")


def _source_from_tag(tag: str, prefix: str) -> str | None:
    """Accept only full-line trusted repo script tags, not arbitrary HTML."""
    if not tag.startswith(prefix) or not tag.endswith(SCRIPT_SUFFIX):
        return None
    src = tag[len(prefix):-len(SCRIPT_SUFFIX)]
    return src if SAFE_SOURCE.fullmatch(src) else None



class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.location = "outside"
        self.scripts: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag in ("head", "body"):
            self.location = tag
        elif tag == "script":
            self.scripts.append((self.location, attrs))

    def handle_endtag(self, tag) -> None:
        if tag in ("head", "body"):
            self.location = "outside"


def _scripts(html: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
    parser = _Scripts()
    parser.feed(html)
    parser.close()
    return parser.scripts


def _bundle_script(root: Path, paths: list[str]) -> str:
    chunks = []
    frontend = (root / "frontend").resolve()
    for src in paths:
        path = (frontend / src.lstrip("/")).resolve()
        if not path.is_relative_to(frontend) or not path.is_file():
            raise ValueError(f"script does not resolve under frontend: {src}")
        data = path.read_text(encoding="utf-8")
        # Separate trailing line comments and prevent expression adjacency.
        # The candidate deliberately keeps all top-level global bindings.
        chunks.append(f";\n/* benchmark source: {src} */\n{data}\n;\n")
    return "".join(chunks)


def build_candidate(root: Path) -> tuple[str, dict[str, str], dict[str, int]]:
    """Build the candidate in memory, rejecting unsupported HTML and assets."""
    original = (root / "frontend/index.html").read_text(encoding="utf-8")
    expected = []
    emitted = []
    outputs = []
    groups: dict[str, list[str]] = {"head": [], "body": []}
    head_end = original.find("</head>")
    if head_end < 0:
        raise ValueError("missing head/body script boundary")
    offset = 0
    for line in original.splitlines(keepends=True):
        script_offset = offset
        offset += len(line)
        stripped = line.strip()
        if not stripped.startswith("<script"):
            outputs.append(line)
            continue
        classic = _source_from_tag(stripped, CLASSIC_PREFIX)
        module = _source_from_tag(stripped, MODULE_PREFIX)
        if not classic and not module:
            raise ValueError("unsupported script markup in index.html")
        src = classic or module
        # A script's parser boundary must be independently known.
        location = "head" if script_offset < head_end else "body"
        if classic:
            expected.append((location, [("src", src)]))
            if location not in groups:
                raise ValueError("unexpected classic script placement")
            groups[location].append(classic)
            if len(groups[location]) == 1:
                url = HEAD_URL if location == "head" else BODY_URL
                replacement = line[:len(line) - len(line.lstrip())] + f'<script src="{url}"></script>\n'
                outputs.append(replacement)
                emitted.append((location, [("src", url)]))
        else:
            expected.append((location, [("type", "module"), ("src", src)]))
            outputs.append(line)
            emitted.append((location, [("type", "module"), ("src", src)]))
    if not groups["head"] or not groups["body"]:
        raise ValueError("expected classic scripts at both parser boundaries")
    if _scripts(original) != expected:
        raise ValueError("original script tags differ from strict line inventory")
    candidate = "".join(outputs)
    if _scripts(candidate) != emitted:
        raise ValueError("candidate script ordering/attributes changed unexpectedly")
    bundles = {
        HEAD_URL: _bundle_script(root, groups["head"]),
        BODY_URL: _bundle_script(root, groups["body"]),
    }
    counts = {"head": len(groups["head"]), "body": len(groups["body"])}
    return candidate, bundles, counts
