"""Static safety contract for PRKS Dependabot PR consolidation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


CONFIG = (Path(__file__).resolve().parents[1] / ".github/dependabot.yml")


def _ecosystem(config: str, kind: str, directory: str) -> str:
    match = re.search(
        r'(?ms)^  - package-ecosystem: "' + re.escape(kind) + r'"\n'
        + r'    directory: "' + re.escape(directory) + r'"\n'
        + r'(.*?)(?=^  - package-ecosystem: |\Z)',
        config,
    )
    if match is None:
        raise AssertionError(f"Dependabot ecosystem {kind}:{directory} missing")
    return match.group(1)


class DependabotGroupingSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = CONFIG.read_text(encoding="utf-8")

    def test_python_routine_group_does_not_include_runtime_or_browser(self):
        pip = _ecosystem(self.config, "pip", "/")
        group = pip.split("      python-maintainer-routine:", 1)[1]
        for name in ("psycopg", "psycopg-binary", "psycopg-pool", "argon2-cffi", "openapi-core"):
            self.assertIn(f'          - "{name}"', group)
        for name in ("PyMuPDF", "Pillow", "pydantic", "playwright"):
            self.assertNotIn(f'          - "{name}"', group)
        self.assertIn("applies-to: version-updates", group)
        self.assertIn('update-types: ["minor", "patch"]', group)

    def test_frontend_stacks_remain_separate_from_routine(self):
        frontend = _ecosystem(self.config, "npm", "/frontend-app")
        for group in ("storybook-stack", "vitest-stack", "vite-toolchain", "vue-core", "frontend-routine"):
            self.assertIn(f"      {group}:", frontend)
        routine = frontend.split("      frontend-routine:", 1)[1]
        self.assertIn("exclude-patterns:", routine)
        for name in ("storybook", "@storybook/*", "vitest", "@vitest/*", "vite", "@vitejs/plugin-vue", "vue-tsc", "vue", "@vue/*"):
            self.assertIn(f'          - "{name}"', routine)
        self.assertIn('update-types: ["minor", "patch"]', routine)
        self.assertIn('          - "vue-tsc"', frontend.split("      vite-toolchain:", 1)[1].split("      vue-core:", 1)[0])

    def test_pdf_react_and_embedpdf_remain_independent(self):
        viewer = _ecosystem(self.config, "npm", "/tools/pdf-viewer")
        for group in ("embedpdf", "react-stack", "pdf-viewer-routine"):
            self.assertIn(f"      {group}:", viewer)
        routine = viewer.split("      pdf-viewer-routine:", 1)[1]
        for name in ("@embedpdf/*", "react", "react-dom", "@types/react", "@types/react-dom"):
            self.assertIn(f'          - "{name}"', routine)
        self.assertIn('update-types: ["minor", "patch"]', routine)

    def test_security_updates_and_majors_are_not_batched(self):
        gh = _ecosystem(self.config, "github-actions", "/")
        self.assertIn("      codeql-security:\n        applies-to: security-updates", gh)
        self.assertIn("      codeql-action:\n        applies-to: version-updates", gh)
        routine = gh.split("      actions-routine:", 1)[1]
        self.assertIn('          - "actions/*"', routine)
        self.assertIn('update-types: ["minor", "patch"]', routine)
        self.assertIn("applies-to: version-updates", routine)
        vendor = _ecosystem(self.config, "npm", "/tools/frontend-vendor")
        self.assertIn('          - "dompurify"', vendor.split("      frontend-vendor-routine:", 1)[1])


if __name__ == "__main__":
    unittest.main()
