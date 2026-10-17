"""Licensing and visible source-of-code notice regressions."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"


class LicensingStructureTests(unittest.TestCase):
    def test_agpl_license_is_full_unmodified_family_text(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(
            license_text.startswith(
                "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007"
            )
        )
        self.assertIn(
            "13. Remote Network Interaction; Use with the GNU General Public License.",
            license_text,
        )
        self.assertGreater(len(license_text), 30000)

    def test_readme_has_spdx_and_notices(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("AGPL-3.0-only", readme)
        self.assertIn("THIRD_PARTY_NOTICES.md", readme)
        self.assertIn("CONTRIBUTING.md", readme)
        self.assertIn("Corresponding Source", readme)

    def test_about_notice_is_in_diagnostics_not_general(self):
        index = INDEX.read_text(encoding="utf-8")
        general = index.split('id="prks-settings-panel-general"', 1)[1].split(
            'id="prks-settings-panel-reading"', 1
        )[0]
        diagnostics = index.split('id="prks-settings-panel-diagnostics"', 1)[1].split(
            'id="prks-restore-confirm-modal"', 1
        )[0]
        self.assertNotIn('id="prks-source-code-link"', general)
        for token in (
            'id="prks-settings-about-heading"',
            'id="prks-source-code-link"',
            'href="https://github.com/Fooftilly/PRKS"',
            'id="prks-license-link"',
            'href="https://github.com/Fooftilly/PRKS/blob/master/LICENSE"',
            'rel="noopener noreferrer"',
            "AGPL-3.0-only",
            "no warranty",
        ):
            self.assertIn(token, diagnostics)

    def test_vendor_notices_remain_present(self):
        for name in (
            "codemirror", "cytoscape", "dompurify", "easymde",
            "idb", "inter", "lucide",
        ):
            with self.subTest(vendor=name):
                self.assertTrue((ROOT / "frontend" / "vendor" / name / "LICENSE").is_file())
        viewer = ROOT / "frontend" / "vendor" / "prks-pdf-viewer"
        self.assertTrue((viewer / "THIRD_PARTY.md").is_file())
        self.assertTrue((viewer / "LICENSES" / "pdfium-LICENSE").is_file())


if __name__ == "__main__":
    unittest.main()
