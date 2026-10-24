"""Guard Dependabot batching boundaries, especially security and runtime pins."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / ".github/dependabot.yml"


def ecosystem(config: str, kind: str, directory: str) -> str:
    match = re.search(
        r'(?ms)^  - package-ecosystem: "' + re.escape(kind) + r'"\n'
        + r'    directory: "' + re.escape(directory) + r'"\n'
        + r'(.*?)(?=^  - package-ecosystem: |\Z)',
        config,
    )
    if match is None:
        raise AssertionError(f"Missing Dependabot ecosystem {kind}:{directory}")
    return match.group(1)


def group(config: str, name: str) -> str:
    match = re.search(
        r"(?ms)^      " + re.escape(name) + r":\n"
        + r"(.*?)(?=^      [\w-]+:\n|\Z)",
        config,
    )
    if match is None:
        raise AssertionError(f"Missing Dependabot group {name}")
    return match.group(1)


def values(section: str, field: str) -> set[str]:
    match = re.search(
        r"(?m)^        " + re.escape(field) + r":\n"
        + r"((?:^          - \"[^\"]+\"\n)+)",
        section,
    )
    if match is None:
        raise AssertionError(f"Missing or malformed {field}")
    return set(re.findall(r'(?m)^          - "([^"]+)"$', match.group(1)))


class DependabotGroupingSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = CONFIG.read_text(encoding="utf-8")

    def test_psycopg_stack_is_only_database_driver_family(self):
        pip = ecosystem(self.config, "pip", "/")
        stack = group(pip, "psycopg-stack")
        self.assertEqual(
            values(stack, "patterns"),
            {"psycopg", "psycopg-binary", "psycopg-pool"},
        )
        self.assertIn("applies-to: version-updates", stack)
        self.assertIn('update-types: ["minor", "patch"]', stack)
        # Crucially, these must not be grouped even if they move to runtime.
        for pkg in ("argon2-cffi", "openapi-core", "pydantic", "playwright"):
            self.assertNotIn(pkg, values(stack, "patterns"))

    def test_frontend_stacks_are_excluded_from_routine(self):
        frontend = ecosystem(self.config, "npm", "/frontend-app")
        reserved = {
            "storybook", "@storybook/*", "vitest", "@vitest/*", "vite",
            "@vitejs/plugin-vue", "vue-tsc", "vue", "@vue/*",
        }
        for key in ("storybook-stack", "vitest-stack", "vite-toolchain", "vue-core"):
            self.assertTrue(values(group(frontend, key), "patterns"))
        routine = group(frontend, "frontend-routine")
        self.assertEqual(values(routine, "patterns"), {"*"})
        self.assertTrue(reserved.issubset(values(routine, "exclude-patterns")))
        self.assertIn('update-types: ["minor", "patch"]', routine)
        self.assertIn("vue-tsc", values(group(frontend, "vite-toolchain"), "patterns"))

    def test_pdf_react_and_embedpdf_not_swallowed_by_routine(self):
        viewer = ecosystem(self.config, "npm", "/tools/pdf-viewer")
        routine = group(viewer, "pdf-viewer-routine")
        protected = {"@embedpdf/*", "react", "react-dom", "@types/react", "@types/react-dom"}
        self.assertTrue(protected.issubset(values(routine, "exclude-patterns")))
        self.assertEqual(values(routine, "patterns"), {"*"})
        self.assertIn('update-types: ["minor", "patch"]', routine)
        self.assertTrue(protected.intersection(values(group(viewer, "react-stack"), "patterns")))
        self.assertIn("@embedpdf/*", values(group(viewer, "embedpdf"), "patterns"))

    def test_dompurify_exclusion_is_real(self):
        vendor = ecosystem(self.config, "npm", "/tools/frontend-vendor")
        routine = group(vendor, "frontend-vendor-routine")
        self.assertEqual(values(routine, "patterns"), {"*"})
        self.assertIn("dompurify", values(routine, "exclude-patterns"))
        self.assertIn('update-types: ["minor", "patch"]', routine)

    def test_action_security_and_major_updates_remain_separate(self):
        gh = ecosystem(self.config, "github-actions", "/")
        security = group(gh, "codeql-security")
        codeql = group(gh, "codeql-action")
        routine = group(gh, "actions-routine")
        self.assertIn("applies-to: security-updates", security)
        self.assertIn("applies-to: version-updates", codeql)
        self.assertIn("applies-to: version-updates", routine)
        self.assertEqual(values(routine, "patterns"), {"actions/*"})
        self.assertIn('update-types: ["minor", "patch"]', routine)


if __name__ == "__main__":
    unittest.main()
