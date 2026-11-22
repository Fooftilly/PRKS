"""E2E selection shadow report: find master's coverage map, compare its selection with the gate plan."""

from __future__ import annotations

import gzip
import io
import json
import os
import tempfile
import unittest
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.ci import e2e_selection_shadow as shadow

ROOT = Path(__file__).resolve().parents[1]


def _zip(name: str, payload: dict[str, Any], *, extra: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}.json.gz", gzip.compress(json.dumps(payload).encode()))
        if extra:
            archive.writestr(extra, b"x")
    return buffer.getvalue()


class _Runs:
    def __init__(self, runs: list[dict[str, Any]], artifacts: dict[int, list[dict[str, Any]]]):
        self.runs = runs
        self.artifacts = artifacts
        self.paths: list[str] = []

    def request(self, method, path, body=None):
        self.paths.append(path)
        if path.startswith("actions/workflows/"):
            assert "branch=master" in path and "status=success" in path, path
            return {"workflow_runs": self.runs}
        run_id = int(path.split("/")[2])
        return {"artifacts": self.artifacts.get(run_id, [])}


def _artifacts(shards, expired=()):
    return [{"id": 100 + s, "name": f"e2e-coverage-map-{s}", "expired": s in expired} for s in shards]


class LatestMapTests(unittest.TestCase):
    def test_the_newest_complete_master_map_is_used(self):
        client = _Runs([{"id": 2, "event": "schedule", "html_url": "u2"}], {2: _artifacts(range(1, 9))})
        run, artifacts, why = shadow.latest_map_artifacts(client)
        self.assertEqual(run and run["id"], 2)
        self.assertEqual([a["name"] for a in artifacts], [f"e2e-coverage-map-{s}" for s in range(1, 9)])
        self.assertEqual(why, "")

    def test_a_pull_request_recording_is_never_used(self):
        client = _Runs([{"id": 3, "event": "pull_request"}, {"id": 2, "event": "workflow_dispatch"}],
                       {3: _artifacts(range(1, 9)), 2: _artifacts(range(1, 9))})
        run, _artifacts_, _why = shadow.latest_map_artifacts(client)
        self.assertEqual(run and run["id"], 2)

    def test_an_incomplete_or_expired_map_is_reported_not_used(self):
        client = _Runs([{"id": 2, "event": "schedule", "html_url": "u2"}],
                       {2: _artifacts(range(1, 9), expired={4})})
        run, artifacts, why = shadow.latest_map_artifacts(client)
        self.assertIsNone(run)
        self.assertEqual(artifacts, [])
        self.assertIn("not 1-8", why)

    def test_an_incomplete_newest_map_falls_back_to_an_older_complete_one(self):
        client = _Runs([{"id": 3, "event": "schedule", "html_url": "u3"}, {"id": 2, "event": "schedule"}],
                       {3: _artifacts(range(1, 8)), 2: _artifacts(range(1, 9))})
        run, artifacts, why = shadow.latest_map_artifacts(client)
        self.assertEqual(run and run["id"], 2)
        self.assertEqual(len(artifacts), 8)
        self.assertEqual(why, "")

    def test_no_run_yet(self):
        self.assertEqual(shadow.latest_map_artifacts(_Runs([], {}))[2], "no successful master recording run yet")


class ReadMapTests(unittest.TestCase):
    def test_reads_the_one_gzipped_map(self):
        data = _zip("e2e-coverage-map-1", {"commit": "a" * 40})
        self.assertEqual(shadow.read_map(data, "e2e-coverage-map-1"), {"commit": "a" * 40})

    def test_anything_else_in_the_artifact_is_refused(self):
        data = _zip("e2e-coverage-map-1", {}, extra="../evil")
        with self.assertRaisesRegex(RuntimeError, "unexpected artifact contents"):
            shadow.read_map(data, "e2e-coverage-map-1")


class DownloadTests(unittest.TestCase):
    def test_the_token_is_not_sent_to_the_storage_redirect(self):
        headers = Message()
        headers["Location"] = "https://storage.example.test/blob?sig=1"
        redirect = urllib.error.HTTPError("https://api.example.test", 302, "Found", headers, None)
        opener = mock.Mock()
        opener.open.side_effect = redirect
        body = mock.MagicMock()
        body.__enter__.return_value.read.return_value = b"zip"
        with mock.patch.object(shadow.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(shadow.urllib.request, "urlopen", return_value=body) as urlopen:
            self.assertEqual(shadow._download("tok", "https://api.example.test", "o/r", 7), b"zip")
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.full_url, "https://api.example.test/repos/o/r/actions/artifacts/7/zip")
        self.assertEqual(sent.get_header("Authorization"), "Bearer tok")
        # The signed storage URL is fetched as a bare string: no headers, no token.
        self.assertEqual(urlopen.call_args.args[0], "https://storage.example.test/blob?sig=1")

    def test_an_oversized_direct_response_is_refused(self):
        body = mock.MagicMock()
        body.__enter__.return_value.read.return_value = b"x" * 9
        opener = mock.Mock()
        opener.open.return_value = body
        with mock.patch.object(shadow.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(shadow, "MAX_ARTIFACT_BYTES", 8), \
                self.assertRaisesRegex(RuntimeError, "larger than 8 bytes"):
            shadow._download("tok", "https://api.example.test", "o/r", 7)

    def test_a_non_https_redirect_is_refused(self):
        headers = Message()
        headers["Location"] = "http://storage.example.test/blob"
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("u", 302, "Found", headers, None)
        with mock.patch.object(shadow.urllib.request, "build_opener", return_value=opener), \
                self.assertRaises(urllib.error.HTTPError):
            shadow._download("tok", "https://api.example.test", "o/r", 7)


class SummaryTests(unittest.TestCase):
    PLAN = {"mode": "affected", "test_count": 40, "reason": "affected features"}

    def test_both_selections_are_reported(self):
        result = {"mode": "subset", "tests": 12, "total": 900,
                  "files": {"backend/x.py": {"rule": "coverage-map", "tests": 12}}}
        text = shadow.summarize(result, self.PLAN, "", "https://map")
        self.assertIn("Gate plan: **affected**, 40 tests", text)
        self.assertIn("**subset, 12 of 900 tests**", text)
        self.assertIn("| `backend/x.py` | coverage-map | 12 |", text)

    def test_a_full_selection_names_its_reason(self):
        result = {"mode": "full", "tests": 900, "total": 900, "full_because": ["frontend/css/a.css"],
                  "files": {"frontend/css/a.css": {"rule": "full", "why": "frontend-full"}}}
        text = shadow.summarize(result, self.PLAN, "", "u")
        self.assertIn("full, because of `frontend/css/a.css`", text)
        self.assertIn("full (frontend-full)", text)

    def test_a_missing_map_is_said_plainly(self):
        text = shadow.summarize(None, {"mode": "skip"}, "no successful master recording run yet", "")
        self.assertIn("not available (no successful master recording run yet)", text)


class MainTests(unittest.TestCase):
    def test_writes_the_report_and_never_changes_the_plan(self):
        plan = {"mode": "full", "reason": "r", "run": True}
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r",
                                             "GITHUB_STEP_SUMMARY": str(Path(raw) / "summary.md")}), \
                mock.patch.object(shadow, "planner", return_value=plan), \
                mock.patch.object(shadow, "_rev", return_value="c" * 40), \
                mock.patch.object(shadow, "latest_map_artifacts", return_value=(None, [], "no map")):
            out = Path(raw) / "report.json"
            self.assertEqual(shadow.main(["--base", "origin/master", "--output", str(out)]), 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["plan"], plan)
            self.assertIsNone(report["shadow"])
            self.assertEqual(report["unavailable"], "no map")
            self.assertIn("E2E selection (shadow)", (Path(raw) / "summary.md").read_text(encoding="utf-8"))


class MainWithMapTests(unittest.TestCase):
    def _main(self, raw, ids):
        plan_file = Path(raw) / "plan.json"
        plan_file.write_text(json.dumps({"base_sha": "b" * 40, "head_sha": "b" * 40, "plan": {"mode": "skip"}}),
                             encoding="utf-8")
        out = Path(raw) / "report.json"
        run = {"id": 2, "html_url": "https://map"}
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.dict(os.environ, {}, clear=False) as env, \
                mock.patch.object(shadow, "planner", side_effect=AssertionError("plan was given")), \
                mock.patch.object(shadow, "_rev", return_value="b" * 40), \
                mock.patch.object(shadow, "latest_map_artifacts", return_value=(run, [{"id": 7, "name": "m"}], "")), \
                mock.patch.object(shadow, "_download", return_value=b"zip"), \
                mock.patch.object(shadow, "read_map", return_value={}), \
                mock.patch("tests.e2e.run.discover_test_ids", return_value=ids), \
                mock.patch("tests.e2e.coverage_map.select_for_diff",
                           return_value={"mode": "subset", "tests": 1, "total": 2, "files": {}}) as select:
            env.pop("GITHUB_STEP_SUMMARY", None)
            self.assertEqual(shadow.main(["--base", "origin/master", "--plan", str(plan_file),
                                          "--output", str(out)]), 0)
        return json.loads(out.read_text(encoding="utf-8")), select

    def test_a_complete_inventory_is_passed_to_the_selector(self):
        ids = ["tests.e2e.test_a.A.test_x", "tests.e2e.test_b.B.test_y"]
        with tempfile.TemporaryDirectory() as raw:
            report, select = self._main(raw, ids)
        self.assertEqual(select.call_args.kwargs["all_ids"], ids)
        self.assertEqual(report["shadow"]["mode"], "subset")
        self.assertEqual(report["plan"], {"mode": "skip"})
        self.assertEqual(report["base_sha"], "b" * 40)

    def test_an_import_failure_in_discovery_is_reported_not_selected_from(self):
        ids = ["tests.e2e.test_a.A.test_x", "unittest.loader._FailedTest.tests.e2e.test_pg"]
        with tempfile.TemporaryDirectory() as raw:
            report, select = self._main(raw, ids)
        select.assert_not_called()
        self.assertIsNone(report["shadow"])
        self.assertIn("E2E discovery failed: unittest.loader._FailedTest", report["unavailable"])

    def test_plan_only_writes_the_gate_plan_and_needs_no_token(self):
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.dict(os.environ, {}, clear=False) as env, \
                mock.patch.object(shadow, "planner", return_value={"mode": "affected"}), \
                mock.patch.object(shadow, "_rev", return_value="d" * 40):
            env.pop("GITHUB_TOKEN", None)
            out = Path(raw) / "plan.json"
            self.assertEqual(shadow.main(["--base", "origin/master", "--plan-only", "--output", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                             {"base_sha": "d" * 40, "head_sha": "d" * 40, "plan": {"mode": "affected"}})

    def test_a_plan_made_at_other_revisions_is_refused(self):
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(shadow, "_rev", return_value="e" * 40):
            plan_file = Path(raw) / "plan.json"
            plan_file.write_text(json.dumps({"base_sha": "f" * 40, "head_sha": "e" * 40, "plan": {}}),
                                 encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "was planned at"):
                shadow.main(["--base", "origin/master", "--plan", str(plan_file),
                             "--output", str(Path(raw) / "r.json")])


class RefTests(unittest.TestCase):
    def test_plain_revisions_are_accepted(self):
        for ref in ("origin/master", "HEAD", "a" * 40, "HEAD~1", "v1.0^{commit}"):
            with self.subTest(ref=ref):
                self.assertEqual(shadow.checked_ref(ref), ref)

    def test_options_and_odd_names_never_reach_git_or_the_planner(self):
        for ref in ("--output=/tmp/x", "-c", "", "a b", "base..head", "x;rm"):
            with self.subTest(ref=ref), \
                    mock.patch.object(shadow.subprocess, "run", side_effect=AssertionError("ran")):
                with self.assertRaises(ValueError):
                    shadow._rev(ref)
                with self.assertRaises(ValueError):
                    shadow.planner(ref)

    def test_the_command_line_refuses_an_option_as_base(self):
        with tempfile.TemporaryDirectory() as raw, mock.patch("sys.stderr"), \
                self.assertRaises(SystemExit):
            shadow.main(["--base=--upload-pack=x", "--plan-only", "--output", str(Path(raw) / "p.json")])


class WiringTests(unittest.TestCase):
    def test_the_workflow_is_advisory_and_reads_actions_only(self):
        text = (ROOT / ".github/workflows/e2e-selection-shadow.yml").read_text(encoding="utf-8")
        self.assertIn("actions: read", text)
        self.assertNotIn("write", text)
        self.assertIn("scripts/ci/e2e_selection_shadow.py", text)
        # The plan is computed before the E2E dependencies are installed, as in the gate.
        self.assertLess(text.index("--plan-only"), text.index("install_pinned_python.py e2e"))
        self.assertLess(text.index("install_pinned_python.py e2e"), text.index("--plan gate-plan.json"))
        gate = (ROOT / ".github/workflows/e2e-gate.yml").read_text(encoding="utf-8")
        self.assertNotIn("e2e_selection_shadow", gate)


if __name__ == "__main__":
    unittest.main()
