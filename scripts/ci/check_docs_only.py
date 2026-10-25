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


def _anchors(content: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in content.splitlines():
        for anchor in _HTML_ANCHOR.findall(line):
            anchors.add(anchor)
        match = _HEADING.match(line)
        if match:
            anchor = _slug(match.group(1))
            duplicate = counts.get(anchor, 0)
            counts[anchor] = duplicate + 1
            anchors.add(f"{anchor}-{duplicate}" if duplicate else anchor)
    return anchors


def check_markdown(root: Path, relative: str) -> list[str]:
    current = root / relative
    try:
        # Never allow changed Markdown symlinks to escape the repository.
        if not current.resolve().is_relative_to(root.resolve()):
            return [f"{relative}: path escapes checkout"]
        content = current.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [f"{relative}: unreadable Markdown"]
    errors = []
    fence_char = ""
    fence_width = 0
    cached_anchors: dict[Path, set[str]] = {}
    for number, line in enumerate(content.splitlines(), start=1):
        marker = _FENCE.match(line)
        if marker:
            value = marker.group(1)
            if not fence_char:
                fence_char, fence_width = value[0], len(value)
                continue
            if value[0] == fence_char and len(value) >= fence_width and not line[marker.end():].strip():
                fence_char, fence_width = "", 0
            continue
        if fence_char:
            continue
        # Ignore inline code literals, which may intentionally show bad links.
        scan = re.sub(r"`[^`\n]+`", "", line)
        for link in _LINK.finditer(scan):
            raw = link.group(1).strip("<>")
            try:
                parsed = urlsplit(raw)
            except ValueError:
                errors.append(f"{relative}:{number}: malformed link")
                continue
            if parsed.scheme or parsed.netloc or raw.startswith("//"):
                continue
            path = unquote(parsed.path)
            anchor = unquote(parsed.fragment)
            if not path and not anchor:
                continue
            # Website-root paths are app routes, not repo-relative documents.
            if path.startswith("/"):
                continue
            target = (current.parent / path).resolve() if path else current.resolve()
            if not target.is_relative_to(root.resolve()) or not target.exists():
                errors.append(f"{relative}:{number}: missing relative target {raw}")
                continue
            if anchor and target.suffix.lower() == ".md":
                if target not in cached_anchors:
                    try:
                        cached_anchors[target] = _anchors(target.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError):
                        cached_anchors[target] = set()
                if anchor not in cached_anchors[target]:
                    errors.append(f"{relative}:{number}: missing anchor #{anchor} in {path or relative}")
    if fence_char:
        errors.append(f"{relative}: unclosed {fence_char * fence_width} fence")
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
