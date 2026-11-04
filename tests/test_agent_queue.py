"""Offline tests for the read-only Project #3 agent queue helper."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent-queue.sh"


def item(number, *, agent, status="Ready", execution="Agent", repository="Fooftilly/PRKS", kind="Issue"):
    return {
        "content": {"number": number, "repository": repository, "type": kind, "title": f"Issue {number}"},
        "title": f"Issue {number}",
        "agent": agent,
        "status": status,
        "execution": execution,
    }


class AgentQueueTests(unittest.TestCase):
    def run_queue(self, role, *, items=None, total=None, fail_gh=False):
        with tempfile.TemporaryDirectory() as temp:
            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            payload = Path(temp) / "project.json"
            rows = items if items is not None else [
                item(815, agent="Cursor Cloud"),
                item(969, agent="Cursor Cloud", status="Inbox", execution=None),
                item(740, agent="ChatGPT/Codex"),
                item(635, agent="Claude Code"),
                item(123, agent="Cursor Cloud", kind="PullRequest"),
                item(456, agent="Cursor Cloud", repository="SomeOther/repo"),
            ]
            payload.write_text(json.dumps({"items": rows, "totalCount": len(rows) if total is None else total}))
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "[ \"$*\" = 'project item-list 3 --owner Fooftilly --format json --limit 1000' ] || exit 83\n"
                + ("exit 84\n" if fail_gh else 'cat "$AGENT_QUEUE_FIXTURE"\n')
            )
            gh.chmod(0o755)
            # The published queue fallback must not consult a real remote or
            # public URL when the mocked Project API fails. Keep this fixture
            # hermetic for both the direct-API and fallback implementations.
            for command in ("git", "curl"):
                stub = fake_bin / command
                stub.write_text("#!/bin/sh\nexit 85\n")
                stub.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}", AGENT_QUEUE_FIXTURE=str(payload))
            return subprocess.run(["bash", str(SCRIPT), role], env=env, capture_output=True, text=True)

    def test_cursor_sees_only_prks_issues_without_claiming_readiness(self):
        run = self.run_queue("cursor")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("#815\tReady\tAgent", run.stdout)
        self.assertIn("#969\tInbox\t(unset)", run.stdout)
        self.assertNotIn("#740", run.stdout)
        self.assertNotIn("#123", run.stdout)
        self.assertNotIn("#456", run.stdout)
        self.assertIn("Ownership only", run.stderr)

    def test_chatgpt_and_claude_names(self):
        self.assertIn("#740", self.run_queue("chatgpt").stdout)
        self.assertIn("#740", self.run_queue("codex").stdout)
        self.assertIn("#635", self.run_queue("claude").stdout)

    def test_chatgpt_queue_accepts_historical_codex_field_value(self):
        # PR Lens/Devin assessed the old field configuration. The live option
        # was renamed to ChatGPT/Codex, but old snapshots can still say Codex.
        rows = [item(740, agent="Codex"), item(741, agent="ChatGPT/Codex")]
        for role in ("chatgpt", "codex", "ChatGPT/Codex"):
            with self.subTest(role=role):
                run = self.run_queue(role, items=rows)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertIn("#740", run.stdout)
                self.assertIn("#741", run.stdout)

    def test_historical_codex_value_does_not_leak_to_other_agents(self):
        run = self.run_queue("cursor", items=[
            item(740, agent="Codex"), item(815, agent="Cursor Cloud")
        ])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("#815", run.stdout)
        self.assertNotIn("#740", run.stdout)

    def test_input_aliases_are_equivalent(self):
        groups = {
            740: ("chatgpt", "codex", "ChatGPT/Codex", "CHATGPT/CODEX", "chatgpt-codex", "chatgpt_codex", " Codex "),
            815: ("cursor", "Cursor", "Cursor Cloud", "cursor-cloud", "CURSOR_CLOUD", " Cursor  Cloud "),
            635: ("claude", "Claude", "Claude Code", "claude-code", "CLAUDE_CODE", " claude code "),
        }
        for number, names in groups.items():
            for name in names:
                with self.subTest(name=name):
                    run = self.run_queue(name)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn(f"#{number}\t", run.stdout)
                    for other in groups:
                        if other != number:
                            self.assertNotIn(f"#{other}\t", run.stdout)

    def test_project_agent_field_aliases_are_equivalent(self):
        rows = [
            item(740, agent="Codex"), item(741, agent="ChatGPT"), item(742, agent="ChatGPT/Codex"),
            item(815, agent="Cursor Cloud"), item(816, agent="Cursor"),
            item(635, agent="Claude Code"), item(636, agent="Claude"),
        ]
        expectations = {
            "codex": ({740, 741, 742}, {815, 816, 635, 636}),
            "cursor": ({815, 816}, {740, 741, 742, 635, 636}),
            "claude": ({635, 636}, {740, 741, 742, 815, 816}),
        }
        for role, (expected, excluded) in expectations.items():
            with self.subTest(role=role):
                run = self.run_queue(role, items=rows)
                self.assertEqual(run.returncode, 0, run.stderr)
                for number in expected:
                    self.assertIn(f"#{number}\t", run.stdout)
                for number in excluded:
                    self.assertNotIn(f"#{number}\t", run.stdout)

    def test_similar_but_unknown_agent_is_rejected(self):
        for role in ("chatgpt pro", "Cursor Cloud Backup", "claudecodev", "", "Manual"):
            with self.subTest(role=role):
                run = self.run_queue(role)
                self.assertEqual(run.returncode, 2)
                self.assertIn("Unknown agent", run.stderr)

    def test_truncated_project_is_rejected(self):
        run = self.run_queue("cursor", total=1001)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Incomplete Project #3", run.stderr)

    def test_unknown_agent_is_rejected(self):
        run = self.run_queue("unassigned")
        self.assertEqual(run.returncode, 2)
        self.assertIn("Unknown agent", run.stderr)

    def test_project_lookup_failure_is_not_silenced(self):
        run = self.run_queue("cursor", fail_gh=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn("#815", run.stdout)


if __name__ == "__main__":
    unittest.main()
