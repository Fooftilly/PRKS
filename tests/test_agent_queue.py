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
