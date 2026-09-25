import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


class AgentContextBudgetTests(unittest.TestCase):
    def test_root_agent_contract_stays_small(self):
        self.assertLessEqual(
            line_count(ROOT / "AGENTS.md"),
            250,
            "Root AGENTS.md is ambient context; move specialized rules into scoped AGENTS.md files.",
        )

    def test_claude_bridge_stays_small(self):
        self.assertLessEqual(
            line_count(ROOT / "CLAUDE.md"),
            80,
            "CLAUDE.md should route to canonical/scoped instructions instead of duplicating them.",
        )

    def test_scoped_agent_files_have_bounded_size(self):
        oversized = []
        for path in ROOT.rglob("AGENTS.md"):
            if ".git" in path.parts:
                continue
            lines = line_count(path)
            if lines > 500:
                oversized.append(f"{path.relative_to(ROOT)}: {lines} lines")
        self.assertEqual(
            oversized,
            [],
            "Split oversized scoped AGENTS.md files so agents only receive relevant policy: "
            + ", ".join(oversized),
        )

    def test_cursor_rules_are_bounded(self):
        rules_dir = ROOT / ".cursor" / "rules"
        oversized = []
        always_apply_lines = 0

        for path in sorted(rules_dir.glob("*.mdc")):
            text = path.read_text(encoding="utf-8")
            lines = len(text.splitlines())
            if lines > 500:
                oversized.append(f"{path.name}: {lines} lines")
            if re.search(r"(?m)^alwaysApply:\s*true\s*$", text):
                always_apply_lines += lines

        self.assertEqual(
            oversized,
            [],
            "Split oversized Cursor rules: " + ", ".join(oversized),
        )
        self.assertLessEqual(
            always_apply_lines,
            200,
            "Always-applied Cursor rules exceeded the ambient-context budget; scope new rules by path.",
        )


if __name__ == "__main__":
    unittest.main()
