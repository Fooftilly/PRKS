"""Check links, heading anchors, and balanced fences in changed Markdown (#643).

Use only the files from the same fail-closed docs-only PR classifier. External
links are intentionally not fetched, and nothing writes into the checkout.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from scripts.ci.docs_only import changed_documentation

_LINK = re.compile(r"!?(?:\[[^\]\n]*\])\(\s*(<[^>]*>|[^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TAG = re.compile(r"<[^>]+>")
_PUNCT = re.compile(r"[^\w\- ]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_HTML_ANCHOR = re.compile(r"<a\s+(?:id|name)=[\"']([^\"']+)[\"']", re.I)


def _slug(heading: str) -> str:
    title = _TAG.sub("", heading)
    title = re.sub(r"[\\`*_~]", "", title)
    return _SPACES.sub("-", _PUNCT.sub("", title.lower()).strip())


def _visible_lines(content: str) -> tuple[list[tuple[int, str]], str]:
    """Ignore fenced code; return (line number, text) and any open fence."""
    visible = []
    fence_char = ""
    fence_width = 0
    for number, line in enumerate(content.splitlines(), start=1):
        marker = _FENCE.match(line)
        if marker:
            fence = marker.group(1)
            if not fence_char:
                fence_char, fence_width = fence[0], len(fence)
            elif (
                fence[0] == fence_char
                and len(fence) >= fence_width
                and not line[marker.end():].strip()
            ):
                fence_char, fence_width = "", 0
            continue
        if not fence_char:
            visible.append((number, line))
    return visible, fence_char * fence_width


def _anchors(content: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    visible, _open_fence = _visible_lines(content)
    for _number, line in visible:
        anchors.update(_HTML_ANCHOR.findall(line))
        match = _HEADING.match(line)
        if match:
            anchor = _slug(match.group(1))
            duplicate = counts.get(anchor, 0)
            counts[anchor] = duplicate + 1
            anchors.add(f"{anchor}-{duplicate}" if duplicate else anchor)
    return anchors


def _check_link(
    root: Path, current: Path, relative: str, number: int, raw: str,
    anchor_cache: dict[Path, set[str]],
) -> str | None:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return f"{relative}:{number}: malformed link"
    if parsed.scheme or parsed.netloc or raw.startswith("//"):
        return None
    path, anchor = unquote(parsed.path), unquote(parsed.fragment)
    if not (path or anchor) or path.startswith("/"):
        # Website-root paths address app routes, not repository documents.
        return None
    target = (current.parent / path).resolve() if path else current.resolve()
    if not target.is_relative_to(root.resolve()) or not target.exists():
        return f"{relative}:{number}: missing relative target {raw}"
    if anchor and target.suffix.lower() == ".md":
        if target not in anchor_cache:
            try:
                anchor_cache[target] = _anchors(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                anchor_cache[target] = set()
        if anchor not in anchor_cache[target]:
            return f"{relative}:{number}: missing anchor #{anchor} in {path or relative}"
    return None


def check_markdown(root: Path, relative: str) -> list[str]:
    current = root / relative
    try:
        # A changed Markdown symlink must never escape this checkout.
        if not current.resolve().is_relative_to(root.resolve()):
            return [f"{relative}: path escapes checkout"]
        content = current.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [f"{relative}: unreadable Markdown"]
    errors = []
    anchor_cache: dict[Path, set[str]] = {}
    visible, open_fence = _visible_lines(content)
    for number, line in visible:
        # Ignore inline code examples, which can contain invalid links.
        scan = re.sub(r"\x60[^\x60\n]+\x60", "", line)
        for match in _LINK.finditer(scan):
            error = _check_link(
                root, current, relative, number, match.group(1).strip("<>"),
                anchor_cache,
            )
            if error:
                errors.append(error)
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
    print(f"Validated {len(docs)} changed Markdown files (relative links, anchors, fences)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
