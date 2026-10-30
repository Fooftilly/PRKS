"""Contract and failure-mode tests for the public Project #3 agent queue."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project3_agent_queue as queue  # noqa: E402
import project3_readiness as readiness  # noqa: E402

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)
WORKFLOW = ROOT / ".github" / "workflows" / "project3-agent-queue.yml"
SHELL = ROOT / "scripts" / "agent-queue.sh"


def item(number: int, agent: str | None, *, archived: bool = False,
         state: str = "OPEN", status: str | None = "Ready",
         execution: str | None = "Agent") -> dict:
    return {
        "number": number, "state": state, "in_project": True,
        "project_item_id": f"PVTI_{number}", "archived": archived,
        "status": status, "execution": execution, "agent": agent, "assignees": [],
    }


def snapshot(rows: list[dict] | None = None, *, public: bool = True,
             age: timedelta = timedelta()) -> dict:
    when = NOW - age
    issues = rows if rows is not None else [
        item(101, "Claude Code"), item(102, "Cursor Cloud", status="Blocked"),
        item(103, "ChatGPT/Codex", status="Backlog", execution="Mixed"),
    ]
    return {
        "schema": readiness.SCHEMA, "complete": True,
        "generated_at": readiness._iso(when),
        "stale_after": readiness._iso(when + timedelta(hours=30)),
        "max_age_hours": 30, "repository": "Fooftilly/PRKS",
        "project": {"id": readiness.PROJECT_ID, "owner": "Fooftilly",
                    "number": 3, "public": public, "closed": False},
        "fields": dict(readiness.FIELDS),
        "coverage": "every open issue of the repository, plus every closed issue that has a Project #3 item",
        "source": {"workflow_run_id": 1234, "run_attempt": "1", "sha": "abc", "event": "schedule"},
        "counts": {"issues": len(issues),
                   "in_project": sum(r["in_project"] for r in issues),
                   "open_not_in_project": sum(not r["in_project"] for r in issues),
                   "skipped_items": {}},
        "issues": issues,
    }


class PublisherTests(unittest.TestCase):
    def test_public_agent_assignments_are_sorted_and_minimal(self) -> None:
        src = snapshot([
            item(101, "claude", status=None, execution=None),
            item(102, None), item(103, "Cursor Cloud", archived=True),
            item(104, "Claude Code", state="CLOSED"),
            item(105, "ChatGPT/Codex"),
            {"number": 106, "state": "OPEN", "in_project": False,
             "project_item_id": None, "archived": None,
             "status": None, "execution": None, "agent": None, "assignees": []},
            item(107, "Manual"), item(108, "Unassigned"),
        ])
        result = queue.build_queue(src, now=NOW)
        self.assertEqual([r["number"] for r in result["issues"]], [101, 105])
        self.assertEqual(result["issues"][0]["agent"], "Claude Code")
        self.assertIsNone(result["issues"][0]["status"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["source_run_id"], 1234)
        self.assertEqual(result["stale_after"], "2026-10-30T08:00:00Z")
        text = json.dumps(result)
        self.assertNotIn("assignees", text)
        self.assertNotIn("project_item_id", text)
        self.assertIn("#101\t(unset)\t(unset)", queue.render(result, "claude", now=NOW))
        self.assertEqual(len(queue.render(result, "cursor", now=NOW).splitlines()), 1)

    def test_never_publish_private_or_invalid_snapshot(self) -> None:
        for edit in (
            lambda s: s["project"].update(public=False),
            lambda s: s["project"].update(id="wrong"),
            lambda s: s["counts"].update(issues=1000),
            lambda s: s.update(complete=False),
            lambda s: s["issues"].append(copy.deepcopy(s["issues"][0])),
            lambda s: s["issues"][0].update(agent="unrecognized new agent"),
        ):
            with self.subTest(edit=edit):
                data = snapshot()
                edit(data)
                with self.assertRaises(readiness.ReadinessError):
                    queue.build_queue(data, now=NOW)

    def test_publisher_rejects_snapshot_over_30_hours_old(self) -> None:
        with self.assertRaises(readiness.ReadinessError):
            queue.build_queue(snapshot(age=timedelta(hours=31)), now=NOW)

    def test_queue_rejects_stale_future_duplicate_and_tampered_data(self) -> None:
        valid = queue.build_queue(snapshot(), now=NOW)
        mutants = []
        aged = copy.deepcopy(valid)
        mutants.append((aged, NOW + timedelta(hours=13)))
        for change in (
            lambda r: r["issues"].append(copy.deepcopy(r["issues"][0])),
            lambda r: r.update(stale_after="2099-01-01T00:00:00Z"),
            lambda r: r.update(count=999),
            lambda r: r["project"].update(public=False),
            lambda r: r["issues"][0].update(agent="Unassigned"),
            lambda r: r["issues"][0].update(state="OPEN"),
            lambda r: r.update(schema="unexpected"),
        ):
            data = copy.deepcopy(valid)
            change(data)
            mutants.append((data, NOW))
        mutants.append((valid, NOW - timedelta(minutes=6)))
        for data, instant in mutants:
            with self.subTest(data=data, now=instant), self.assertRaises(readiness.ReadinessError):
                queue.check_queue(data, now=instant)

    def test_cli_publishes_and_renders_and_does_not_overwrite_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.json"
            out = Path(td) / "agent-queue.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            self.assertEqual(queue.main(["publish", "--snapshot", str(source), "--out", str(out)], now=NOW), 0)
            published = out.read_text(encoding="utf-8")
            self.assertEqual(queue.main(["render", "--snapshot", str(out), "--agent", "claude"], now=NOW), 0)
            source.write_text(json.dumps(snapshot(public=False)), encoding="utf-8")
            self.assertEqual(queue.main(["publish", "--snapshot", str(source), "--out", str(out)], now=NOW), 1)
            self.assertEqual(out.read_text(encoding="utf-8"), published)

    def test_non_json_and_oversized_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("<html>404</html>", encoding="utf-8")
            with self.assertRaises(readiness.Malformed):
                queue.read_json(str(p))
            p.write_bytes(b" " * (queue.MAX_FILE_BYTES + 1))
            with self.assertRaises(readiness.Malformed):
                queue.read_json(str(p))


class ShellTests(unittest.TestCase):
    def test_bash_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(SHELL)], check=True)

    def _run(self, data: dict, *, force_gh_failure: bool = True) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fixture = tmp / "queue.json"
            fixture.write_text(json.dumps(data), encoding="utf-8")
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            git = bin_dir / "git"
            git.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *'remote get-url origin'*) exit 0;;\n"
                "  *'fetch --quiet --no-tags --depth=1 origin refs/heads/project3-agent-queue'*) exit 0;;\n"
                "  *'show FETCH_HEAD:agent-queue.json'*) cat \"$FAKE_QUEUE_JSON\"; exit $?;;\n"
                "  *) exit 2;;\n"
                "esac\n", encoding="utf-8")
            curl = bin_dir / "curl"
            curl.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
            for program in (gh, git, curl):
                program.chmod(0o755)
            env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                   "FAKE_QUEUE_JSON": str(fixture)}
            return subprocess.run(["bash", str(SHELL), "claude"], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=12)

    def test_invalid_gh_token_rejects_stale_git_queue(self) -> None:
        moment = datetime.now(timezone.utc) - timedelta(hours=13)
        src = snapshot()
        src["generated_at"] = readiness._iso(moment)
        src["stale_after"] = readiness._iso(moment + timedelta(hours=30))
        # The publisher would have accepted this at generation time.
        result = self._run(queue.build_queue(src, now=moment))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale", result.stderr)

    def test_git_remote_fallback_accepts_fresh_snapshot(self) -> None:
        moment = datetime.now(timezone.utc)
        src = snapshot()
        src["generated_at"] = readiness._iso(moment)
        src["stale_after"] = readiness._iso(moment + timedelta(hours=30))
        result = self._run(queue.build_queue(src, now=moment))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#101\tReady\tAgent", result.stdout)
        self.assertNotIn("#102", result.stdout)
        self.assertIn("ownership only", result.stderr)


class WorkflowTests(unittest.TestCase):
    def test_publisher_is_master_only_and_uses_protected_token(self) -> None:
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("environment: project-automation", content)
        self.assertIn("github.ref == 'refs/heads/master'", content)
        self.assertNotIn("workflow_run:", content)
        self.assertNotIn("github.event.workflow_run", content)
        self.assertIn("push:", content)
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("permissions: {}", content)
        self.assertEqual(content.count("secrets."), 1)
        self.assertIn("PROJECT3_READINESS_TOKEN: ${{ secrets.PROJECT_SYNC_TOKEN }}", content)
        self.assertIn("contents: write", content)
        self.assertIn("git push origin HEAD:refs/heads/project3-agent-queue", content)
        self.assertIn("git switch --orphan project3-agent-queue", content)
        self.assertNotIn("git rm -rfq -- .", content)
        self.assertIn("scripts/project3_readiness.py snapshot", content)
        self.assertIn("scripts/project3_agent_queue.py publish", content)
        for ref in re.findall(r"uses:\s*(\S+)", content):
            self.assertRegex(ref, r"@[0-9a-f]{40}$")


    def test_legacy_comment_assignment_workflow_is_retired(self) -> None:
        self.assertFalse(
            (ROOT / ".github/workflows/project3-assignment-bridge.yml").exists()
        )
        self.assertTrue(
            (ROOT / ".github/workflows/project3-assignment-request-processor.yml").is_file()
        )


if __name__ == "__main__":
    unittest.main()
