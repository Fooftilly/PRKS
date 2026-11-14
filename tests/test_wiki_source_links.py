"""The wiki pages under docs/wiki must publish without repository-style links.

The Publish Wiki workflow rewrites sibling page links such as
``[Testing](Testing.md)`` and refuses to publish when a relative link into the
repository (``../file.md``) is left, because it would 404 on the wiki. This
check fails on the pull request instead of on master after the merge.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parents[1] / "docs" / "wiki"
REPO_RELATIVE_LINK = re.compile(r"\]\((?:\.\.?/)[^)\s]*\.md(?:#[^)]*)?\)")


class WikiSourceLinkTests(unittest.TestCase):
    def test_wiki_pages_link_repository_files_by_absolute_url(self) -> None:
        pages = sorted(WIKI_DIR.glob("*.md"))
        self.assertTrue(pages, "docs/wiki has no pages")
        offenders = [
            f"{page.name}: {match.group(0)}"
            for page in pages
            for match in REPO_RELATIVE_LINK.finditer(page.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders,
            [],
            "Link repository files from the wiki with "
            "https://github.com/Fooftilly/PRKS/blob/master/<path>",
        )


if __name__ == "__main__":
    unittest.main()
