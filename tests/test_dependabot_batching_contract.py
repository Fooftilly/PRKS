"""Guard the single weekly Dependabot batch and its deliberate-migration ignores."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


CONFIG = Path(__file__).resolve().parents[1] / ".github/dependabot.yml"
GROUP = "weekly-dependencies"
ECOSYSTEMS = {
    ("pip", "/"),
    ("npm", "/tools/pdf-viewer"),
    ("npm", "/tools/research-graph"),
    ("npm", "/tools/frontend-vendor"),
    ("npm", "/frontend-app"),
    ("docker", "/"),
    ("github-actions", "/"),
}
MAJOR = ["version-update:semver-major"]


def ignored_majors(entry: dict) -> set[str]:
    return {
        rule["dependency-name"]
        for rule in entry.get("ignore", [])
        if rule.get("update-types") == MAJOR
    }


class DependabotWeeklyBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        cls.entries = {
            (u["package-ecosystem"], u["directory"]): u for u in cls.config["updates"]
        }

    def test_every_ecosystem_joins_the_one_weekly_group(self):
        self.assertEqual(set(self.entries), ECOSYSTEMS)
        self.assertEqual(set(self.config["multi-ecosystem-groups"]), {GROUP})
        for key, entry in self.entries.items():
            with self.subTest(ecosystem=key):
                self.assertEqual(entry["multi-ecosystem-group"], GROUP)
                self.assertEqual(entry["patterns"], ["*"])
                # Per-ecosystem groups would split the batch again.
                self.assertNotIn("groups", entry)

    def test_group_has_weekly_schedule_and_no_update_type_filter(self):
        group = self.config["multi-ecosystem-groups"][GROUP]
        self.assertEqual(group["schedule"]["interval"], "weekly")
        # A multi-ecosystem job skips updates outside the group, so a filter
        # here would silently drop majors instead of opening separate PRs.
        self.assertNotIn("update-types", group)

    def test_deliberate_migrations_stay_ignored(self):
        self.assertTrue(
            {"react", "react-dom", "@types/react", "@types/react-dom", "@embedpdf/*"}
            <= ignored_majors(self.entries[("npm", "/tools/pdf-viewer")])
        )
        self.assertIn(
            "codemirror", ignored_majors(self.entries[("npm", "/tools/frontend-vendor")])
        )


if __name__ == "__main__":
    unittest.main()
