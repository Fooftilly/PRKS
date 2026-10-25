"""Validate links, anchors and balanced fences in changed Wiki Markdown (#643).

Uses pinned markdown-it-py (CommonMark plus tables), not hand-written link
regular expressions: references, parenthesized paths, escaped syntax and
rendered heading content follow Markdown semantics.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from scripts.ci.docs_only import changed_documentation

_MARKDOWN = MarkdownIt("commonmark").enable("table")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_PUNCT = re.compile(r"[^\w\- ]", re.UNICODE)
_HTML_ANCHOR = re.compile(r"<a\s+(?:id|name)=[\"']([^\"']+)[\"']", re.I)


def _slug(text: str) -> str:
    # GitHub keeps underscores, removes punctuation, and turns each individual
    # space into a hyphen (without collapsing adjacent spaces).
    return _PUNCT.sub("", text.lower().strip()).replace(" ", "-")


def _visible_lines(content: str) -> tuple[list[tuple[int, str]], str]:
    """Report visible Markdown source lines and any unclosed fenced block."""
    visible = []
    fence_char = ""
    fence_width = 0
    for number, line in enumerate(content.splitlines(), start=1):
        marker = _FENCE.match(line)
        if marker:
            value = marker.group(1)
            if not fence_char:
                fence_char, fence_width = value[0], len(value)
            elif (
                value[0] == fence_char
                and len(value) >= fence_width
                and not line[marker.end():].strip()
            ):
                fence_char, fence_width = "", 0
            continue
        if not fence_char:
            visible.append((number, line))
    return visible, fence_char * fence_width


def _visible_heading_text(children: list) -> str:
    parts = []
    for child in children:
        if child.type in ("text", "code_inline", "image"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


def _anchors(content: str) -> set[str]:
    anchors: set[str] = set()
    tokens = _MARKDOWN.parse(content)
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            if inline.type == "inline":
                slug = _slug(_visible_heading_text(inline.children or []))
                candidate = slug
                suffix = 1
                while candidate in anchors:
                    candidate = f"{slug}-{suffix}"
                    suffix += 1
                anchors.add(candidate)
        elif token.type in ("inline", "html_block"):
            fragments = (
                [child.content for child in token.children or [] if child.type == "html_inline"]
                if token.type == "inline" else [token.content]
            )
            for fragment in fragments:
                anchors.update(_HTML_ANCHOR.findall(fragment))
    return anchors


def _check_link(
    root: Path, current: Path, relative: str, number: int, href: str,
    cache: dict[Path, set[str]],
) -> str | None:
    try:
        parsed = urlsplit(href)
    except ValueError:
        return f"{relative}:{number}: malformed link"
    if parsed.scheme or parsed.netloc or href.startswith("//"):
        return None
    path, anchor = unquote(parsed.path), unquote(parsed.fragment)
    if not (path or anchor) or path.startswith("/"):
        # Website-root URLs may be application routes, not repo file paths.
        return None
    target = (current.parent / path).resolve() if path else current.resolve()
    if not target.is_relative_to(root.resolve()) or not target.exists():
        return f"{relative}:{number}: missing relative target {href}"
    if anchor and target.suffix.lower() == ".md":
        if target not in cache:
            try:
                cache[target] = _anchors(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                cache[target] = set()
        if anchor not in cache[target]:
            return f"{relative}:{number}: missing anchor #{anchor} in {path or relative}"
    return None


def check_markdown(root: Path, relative: str) -> list[str]:
    current = root / relative
    try:
        if not current.resolve().is_relative_to(root.resolve()):
            return [f"{relative}: path escapes checkout"]
        content = current.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [f"{relative}: unreadable Markdown"]
    errors: list[str] = []
    cache: dict[Path, set[str]] = {}
    for token in _MARKDOWN.parse(content):
        if token.type != "inline":
            continue
        number = (token.map or [0])[0] + 1
        for child in token.children or []:
            if child.type in ("link_open", "image"):
                attr = "href" if child.type == "link_open" else "src"
                href = child.attrGet(attr)
                if not href:
                    continue
                error = _check_link(root, current, relative, number, href, cache)
                if error:
                    errors.append(error)
    _, open_fence = _visible_lines(content)
    if open_fence:
        errors.append(f"{relative}: unclosed {open_fence} fence")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    root = Path.cwd()
    docs = changed_documentation(root, args.base)
    if docs is None:
        print("Docs validation refused: not a verified docs-only PR")
        return 1
    errors = []
    for path in docs:
        errors.extend(check_markdown(root, path))
    if errors:
        for msg in errors:
            print(msg)
        return 1
    print(f"Validated {len(docs)} changed Wiki Markdown files (links, anchors, fences)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
