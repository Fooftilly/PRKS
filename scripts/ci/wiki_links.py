"""Rewrite and validate the links of the published GitHub Wiki copy.

``docs/wiki`` keeps ``*.md`` sibling links so the pages read well in normal
GitHub code views. The Publish Wiki workflow copies the pages into the wiki
checkout and runs this script on that copy: sibling page links become Wiki
page URLs, and any repository-style relative ``.md`` link that is left would
404 on the wiki, so publication stops. Fenced code blocks and inline code
spans are left alone, so a page that documents link syntax is neither
rewritten inside its own examples nor rejected.

``tests/test_wiki_source_links.py`` runs the same functions on ``docs/wiki`` so
a bad link fails on the pull request instead of on ``master``.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

# Sibling page links in the repository source, e.g. [Testing](Testing.md).
SIBLING_LINK = re.compile(
    r"(?P<prefix>\]\()(?P<page>[A-Za-z0-9_-]+)\.md"
    r"(?P<anchor>#[^)]*)?(?P<suffix>\))"
)
# Any repository-style relative link left over after the rewrite.
RELATIVE_MD_LINK = re.compile(r"\]\((?!https?://|mailto:|#)[^)]+\.md(?:#[^)]*)?\)")
_FENCE = re.compile(r"(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+[^`]*`+)")


def map_prose(content: str, transform: Callable[[str], str]) -> str:
    """Apply ``transform`` to Markdown prose only, skipping code."""
    out: list[str] = []
    fence = ""
    for line in content.split("\n"):
        stripped = line.lstrip()
        if fence:
            out.append(line)
            if stripped.startswith(fence):
                fence = ""
            continue
        opening = _FENCE.match(stripped)
        if opening:
            fence = opening.group(1)
            out.append(line)
            continue
        parts = _INLINE_CODE.split(line)
        out.append(
            "".join(
                part if index % 2 else transform(part)
                for index, part in enumerate(parts)
            )
        )
    return "\n".join(out)


def rewrite_page(content: str, wiki_base: str) -> str:
    """Turn sibling ``Page.md`` links in prose into Wiki page URLs."""
    base = wiki_base.rstrip("/")

    def to_wiki_url(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{base}/{match.group('page')}{match.group('anchor') or ''}{match.group('suffix')}"

    return map_prose(content, lambda text: SIBLING_LINK.sub(to_wiki_url, text))


def unresolved_links(content: str) -> list[str]:
    """Relative ``.md`` links left in the prose of a rewritten page."""
    found: list[str] = []

    def collect(text: str) -> str:
        found.extend(match.group(0) for match in RELATIVE_MD_LINK.finditer(text))
        return text

    map_prose(content, collect)
    return found


def publish_errors(content: str, wiki_base: str, name: str) -> tuple[str, list[str]]:
    """Rewrite one page and report the links that would break on the wiki."""
    rewritten = rewrite_page(content, wiki_base)
    return rewritten, [f"{name}: {link}" for link in unresolved_links(rewritten)]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: wiki_links.py WIKI_DIR (WIKI_BASE_URL in the environment)",
            file=sys.stderr,
        )
        return 2
    wiki_dir = Path(argv[1])
    wiki_base = os.environ["WIKI_BASE_URL"]
    unresolved: list[str] = []
    for path in sorted(wiki_dir.glob("*.md")):
        rewritten, errors = publish_errors(
            path.read_text(encoding="utf-8"), wiki_base, path.name
        )
        path.write_text(rewritten, encoding="utf-8")
        unresolved.extend(errors)
    if unresolved:
        print(
            "Published wiki still contains repository-style Markdown links:",
            file=sys.stderr,
        )
        print("\n".join(unresolved), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
