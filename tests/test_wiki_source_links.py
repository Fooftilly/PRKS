"""docs/wiki must pass the Publish Wiki link validation before it reaches master.

The checks run ``scripts/ci/wiki_links.py``, the same code the Publish Wiki
workflow runs on the published copy, so a bad link fails on the pull request.
"""

from __future__ import annotations

import io
import os
import subprocess  # nosec B404 - fixed local test command
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from scripts.ci.wiki_links import main, publish_errors, rewrite_page, unresolved_links

ROOT = Path(__file__).resolve().parents[1]
WIKI_DIR = ROOT / "docs" / "wiki"
WIKI_BASE = "https://github.com/Fooftilly/PRKS/wiki"


def _published_pages() -> list[Path]:
    # The workflow publishes every page except README.md.
    return [page for page in sorted(WIKI_DIR.glob("*.md")) if page.name != "README.md"]


class WikiSourceLinkTests(unittest.TestCase):
    def test_every_published_page_passes_the_publisher_validation(self) -> None:
        pages = _published_pages()
        self.assertTrue(pages, "docs/wiki has no published pages")
        errors = [
            error
            for page in pages
            for error in publish_errors(
                page.read_text(encoding="utf-8"), WIKI_BASE, page.name
            )[1]
        ]
        self.assertEqual(
            errors,
            [],
            "Link repository files from the wiki with https://github.com/Fooftilly/PRKS/blob/master/<path>",
        )


class WikiLinkRuleTests(unittest.TestCase):
    def test_sibling_page_links_become_wiki_urls(self) -> None:
        self.assertEqual(
            rewrite_page("See [Testing](Testing.md#gates).", WIKI_BASE + "/"),
            f"See [Testing]({WIKI_BASE}/Testing#gates).",
        )

    def test_any_relative_md_link_left_after_the_rewrite_is_rejected(self) -> None:
        for link in (
            "../admin-bootstrap.md",
            "./Testing.md",
            "docs/admin-bootstrap.md",
            "Manuals/admin-bootstrap.md",
        ):
            with self.subTest(link=link):
                text = rewrite_page(f"[guide]({link})", WIKI_BASE)
                self.assertEqual(unresolved_links(text), [f"]({link})"])

    def test_absolute_mailto_and_anchor_links_are_accepted(self) -> None:
        text = "[a](https://example.org/x.md) [b](mailto:x@example.org) [c](#x.md)"
        self.assertEqual(unresolved_links(rewrite_page(text, WIKI_BASE)), [])

    def test_code_examples_are_neither_rewritten_nor_rejected(self) -> None:
        text = "Use `[guide](../admin-bootstrap.md)` here.\n```\n[Testing](Testing.md)\n```\n"
        self.assertEqual(rewrite_page(text, WIKI_BASE), text)
        self.assertEqual(unresolved_links(text), [])

    def test_a_sibling_link_without_an_anchor_keeps_none(self) -> None:
        self.assertEqual(
            rewrite_page("[Testing](Testing.md)", WIKI_BASE),
            f"[Testing]({WIKI_BASE}/Testing)",
        )

    def test_prose_after_a_closed_or_indented_fence_is_still_checked(self) -> None:
        fence = "`" * 3
        for text in (
            f"{fence}\n[a](A.md)\n{fence}\n[bad](../x.md)\n",
            f"   {fence}\n[a](A.md)\n   {fence}\n[bad](../x.md)\n",
            "`one` and `two` then [bad](../x.md)\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(unresolved_links(text), ["](../x.md)"])

    def test_the_cli_rewrites_in_place_and_fails_on_a_bad_link(self) -> None:
        # In-process, so mutation testing can judge main().
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp, "Good.md")
            good.write_text("[Testing](Testing.md)", encoding="utf-8")
            Path(tmp, "Bad.md").write_text(
                "[guide](docs/admin-bootstrap.md)", encoding="utf-8"
            )
            Path(tmp, "Worse.md").write_text("[x](../x.md)", encoding="utf-8")
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"WIKI_BASE_URL": WIKI_BASE}), redirect_stderr(err):
                self.assertEqual(main(["wiki_links.py", tmp]), 1)
            self.assertEqual(err.getvalue(), (
                "Published wiki still contains repository-style Markdown links:\n"
                "Bad.md: ](docs/admin-bootstrap.md)\n"
                "Worse.md: ](../x.md)\n"
            ))
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["wiki_links.py"]), 2)
            self.assertEqual(
                err.getvalue(), "usage: wiki_links.py WIKI_DIR (WIKI_BASE_URL in the environment)\n"
            )
            Path(tmp, "Worse.md").unlink()
            self.assertEqual(
                good.read_text(encoding="utf-8"), f"[Testing]({WIKI_BASE}/Testing)"
            )
            Path(tmp, "Bad.md").unlink()
            with mock.patch.dict(os.environ, {"WIKI_BASE_URL": WIKI_BASE}), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["wiki_links.py", tmp]), 0)

    def test_the_cli_runs_as_a_script(self) -> None:
        # The only subprocess test: mutation testing deselects it.
        # A bad link must fail the script, as Publish Wiki relies on: an
        # empty folder would pass even if the script never called main().
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Bad.md").write_text("[guide](docs/admin-bootstrap.md)", encoding="utf-8")
            result = subprocess.run(  # nosec B603 - fixed interpreter and script
                [sys.executable, str(ROOT / "scripts" / "ci" / "wiki_links.py"), tmp],
                capture_output=True,
                text=True,
                env={**os.environ, "WIKI_BASE_URL": WIKI_BASE},
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Bad.md: ](docs/admin-bootstrap.md)", result.stderr)


if __name__ == "__main__":
    unittest.main()
