"""The owner-only API reference page, ``GET /api/docs``."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.api.diagnostics import API_DOCS_PAGE
from backend.api.session import route_capability
from backend.domain.identity.authorization import Capability

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class ApiDocsPageTests(unittest.TestCase):
    page = API_DOCS_PAGE.decode("utf-8")

    def test_only_the_library_owner_may_open_it(self) -> None:
        self.assertIs(route_capability("GET", "/api/docs"), Capability.ADMIN_LIBRARY)

    def test_every_asset_is_a_shipped_file(self) -> None:
        assets = re.findall(r'(?:src|href)="(/[^"]+)"', self.page)
        self.assertIn("/vendor/swagger-ui/swagger-ui-bundle.js", assets)
        self.assertIn("/js/api-docs.js", assets)
        for asset in assets:
            with self.subTest(asset=asset):
                self.assertTrue((FRONTEND / asset.lstrip("/")).is_file(), asset)

    def test_the_csp_needs_no_exception(self) -> None:
        self.assertNotRegex(self.page, r"<script(?![^>]*\bsrc=)")
        self.assertNotIn("<style", self.page)
        self.assertNotIn(" style=", self.page)
        self.assertNotRegex(self.page, r"\son[a-z]+=")


if __name__ == "__main__":
    unittest.main()
