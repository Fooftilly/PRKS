"""Tests for scripts/project3_migrate.py (#441). A fake GraphQL transport stands in
for Project #3; no network is used."""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "project3_migrate.py"
_spec = importlib.util.spec_from_file_location("project3_migrate", _SCRIPT)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {_SCRIPT}")
pm = importlib.util.module_from_spec(_spec)
sys.modules["project3_migrate"] = pm
_spec.loader.exec_module(pm)

SCOPE = {"owner": "Fooftilly", "repository": "Fooftilly/PRKS", "project_number": 3, "project_id": "PVT_kwHOAsc2_s4BkAo3"}
STATUS = ["Inbox", "Backlog", "Ready", "In Progress", "Review", "Changes requested", "Blocked", "Done", "Idea"]
STAGE = ["Idea", "Research / Design", "Planned", "Parked"]


class FakeAPI:
    def __init__(self, *, fields=None, project_id=pm.PROJECT_ID) -> None:
        self.project_id = project_id
        self.fields = fields if fields is not None else {"Status": STATUS, "Roadmap Stage": STAGE, "Execution": ["Human", "Agent", "Mixed"], "Horizon": ["Now"]}
        self.items: dict[str, dict] = {}
        self.content = {}
        self.calls: list[str] = []
        self.mutations: list[tuple[str, dict]] = []
        # Called before each mutation is applied, with (name, variables).
        self.before_mutation = None
        # Native "Item added" for issues: None (off), "now" (applied with the
        # add) or "late" (applied when the run sleeps to let workflows settle).
        self.item_added = None
        self.pending: list = []
        self.fail_next_set = 0
        # Repository issues and PRs for plan-backfill: number -> (title, state, labels)
        self.repo_issues: dict[int, tuple] = {}
        self.repo_prs: dict[int, tuple] = {}

    def settle(self, _seconds):
        while self.pending:
            self.pending.pop(0)()

    def add_content(self, kind, number, state="OPEN", repo="Fooftilly/PRKS"):
        self.content[number] = {"__typename": kind, "id": f"N{number}", "number": number, "state": state, "repo": repo}

    def put(self, kind, number, state="OPEN", archived=False, **values):
        self.add_content(kind, number, state)
        self.items[f"I{number}"] = {"number": number, "archived": archived, "values": dict(values)}

    def _node(self, iid):
        it = self.items[iid]
        c = self.content[it["number"]]
        return {"id": iid, "isArchived": it["archived"],
                "fieldValues": {"nodes": [{"name": v, "field": {"name": f}} for f, v in it["values"].items()] + [{}]},
                "content": {"__typename": c["__typename"], "number": c["number"], "state": c["state"], "repository": {"nameWithOwner": c["repo"]}}}

    def _fid(self, name):
        return "F_" + name.replace(" ", "_")

    def __call__(self, query, variables):
        self.calls.append(query)
        if query.lstrip().startswith("mutation"):
            return self._mutation(query, variables)
        if "pullRequests(first" in query or "issues(first" in query:
            return self._repo_page(query, variables)
        if "issueOrPullRequest" in query:
            c = self.content[variables["number"]]
            return {"repository": {"issueOrPullRequest": {k: c[k] for k in ("__typename", "id", "number", "state")}}}
        if "items(first" in query:
            nodes = [self._node(iid) for iid in self.items]
            return {"user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}
        if "node(id" in query:
            iid = variables["id"]
            if iid not in self.items:
                return {"node": None}
            return {"node": dict(self._node(iid), project={"id": pm.PROJECT_ID})}
        # A field whose options are None is a text field.
        fields = [{"id": self._fid(n), "name": n, "dataType": "TEXT"} if opts is None else
                  {"id": self._fid(n), "name": n, "dataType": "SINGLE_SELECT", "options": [{"id": f"{n}:{o}", "name": o} for o in opts]}
                  for n, opts in self.fields.items()]
        return {"user": {"projectV2": {"id": self.project_id, "number": 3, "owner": {"login": "Fooftilly"}, "fields": {"nodes": fields}}}}

    def _mutation(self, query, variables):
        name = re.search(r"\{(\w+)\(", query).group(1)
        if self.before_mutation:
            self.before_mutation(name, variables)
        if name == "updateProjectV2ItemFieldValue" and self.fail_next_set:
            self.fail_next_set -= 1
            raise pm.MigrationError("GraphQL error: simulated outage")
        self.mutations.append((name, variables))
        if name == "addProjectV2ItemById":
            return {name: {"item": {"id": self._add(int(variables["content"][1:]))}}}
        item = self.items[variables["item"]]
        field = next(f for f in self.fields if self._fid(f) == variables["field"])
        item["values"][field] = variables["option"].split(":", 1)[1]
        return {name: {"projectV2Item": {"id": variables["item"]}}}

    def _add(self, n):
        iid = f"I{n}"
        if iid in self.items:  # the real API returns an existing item unchanged
            return iid
        self.items[iid] = {"number": n, "archived": False, "values": {}}
        if self.item_added and self.content[n]["__typename"] == "Issue":
            def native(item=self.items[iid]):
                item["values"]["Status"] = "Inbox"
            if self.item_added == "now":
                native()
            else:
                self.pending.append(native)
        return iid

    def _repo_page(self, query, variables):
        prs = "pullRequests(first" in query
        source = self.repo_prs if prs else self.repo_issues
        states = ["OPEN"] if prs else variables["states"]
        labels = None if prs else variables["labels"]
        nodes = [{"number": n, "title": t, "state": st, "labels": {"nodes": [{"name": x} for x in lb]}}
                 for n, (t, st, lb) in sorted(source.items())
                 if st in states and (not labels or set(labels) & set(lb))]
        # two pages, to exercise pagination
        half = len(nodes) // 2
        first = variables.get("after") is None
        page = nodes[:half] if first else nodes[half:]
        return {"repository": {"pullRequests" if prs else "issues": {
            "pageInfo": {"hasNextPage": first and half > 0, "endCursor": "c1"}, "nodes": page if half else nodes}}}


def plan_file(tmp: Path, stage: str, items, scope=None) -> Path:
    p = tmp / f"{stage}.json"
    p.write_text(json.dumps({"scope": SCOPE if scope is None else scope, "stage": stage, "items": items}))
    return p


def epic(n=38, before="Ready", **kw):
    d = {"number": n, "type": "Issue", "title": "t", "github_state": "OPEN", "expected_before": {"Status": before, "Roadmap Stage": None},
         "set": {"Status": "Backlog", "Roadmap Stage": "Planned"}, "approved": True}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.api = FakeAPI()

    def tearDown(self):
        self._tmp.cleanup()

    def run_stage(self, stage, items, apply=False, **kw):
        path = plan_file(self.tmp, stage, items, **kw)
        return pm.run(stage, path, apply=apply, transport=self.api,
                      checkpoint=self.tmp / "ck.json", report_dir=self.tmp / "out", out=io.StringIO(),
                      settle_seconds=0, sleep=self.api.settle, expected_sha256=pm.plan_sha256(path))


class DryRunAndGuards(Base):
    def test_dry_run_is_default_and_sends_no_mutation(self):
        self.api.put("Issue", 38, Status="Ready")
        rc = pm.main(["migrate-existing", "--plan", str(plan_file(self.tmp, "migrate-existing", [epic()])), "--report-dir", str(self.tmp / "o")], env={}, transport=self.api)
        self.assertEqual(rc, 0)
        self.assertEqual(self.api.mutations, [])
        self.assertFalse(any(q.lstrip().startswith("mutation") for q in self.api.calls))
        report = json.loads((self.tmp / "o" / "migrate-existing.json").read_text())
        self.assertEqual(report["counts"], {"would-change": 1})

    def test_apply_refused_without_new_fields_or_backlog(self):
        for fields in ({"Status": STATUS}, {"Status": [s for s in STATUS if s != "Backlog"], "Roadmap Stage": STAGE, "Execution": ["Human"]}):
            self.api = FakeAPI(fields=fields)
            self.api.put("Issue", 38, Status="Ready")
            with self.assertRaises(pm.MigrationError):
                self.run_stage("migrate-existing", [epic()], apply=True)
            self.assertEqual(self.api.mutations, [])

    def test_apply_refused_unless_every_new_field_is_a_complete_single_select(self):
        cases = {"Execution": (None, "'Execution' is TEXT, not single select"),
                 "Roadmap Stage": (["Planned"], "Roadmap Stage option 'Parked' missing")}
        for field, (options, problem) in cases.items():
            with self.subTest(field=field):
                self.api = FakeAPI()
                self.api.fields[field] = options
                self.api.put("Issue", 38, Status="Ready")
                with self.assertRaisesRegex(pm.MigrationError, re.escape(problem)):
                    self.run_stage("migrate-existing", [epic()], apply=True)
                self.assertEqual(self.api.mutations, [])

    def test_apply_refused_when_plan_option_missing(self):
        self.api.fields["Roadmap Stage"] = ["Idea"]
        self.api.put("Issue", 38, Status="Ready")
        with self.assertRaisesRegex(pm.MigrationError, "Planned"):
            self.run_stage("migrate-existing", [epic()], apply=True)

    def test_scope_validation(self):
        bad = dict(SCOPE, repository="someone/else")
        with self.assertRaises(pm.MigrationError):
            self.run_stage("migrate-existing", [epic()], scope=bad)
        with self.assertRaises(pm.MigrationError):
            pm.load_plan(plan_file(self.tmp, "migrate-existing", [epic()]), "backfill")
        self.api = FakeAPI(project_id="PVT_other")
        with self.assertRaisesRegex(pm.MigrationError, "scope"):
            self.run_stage("migrate-existing", [epic()])

    def test_cli_paths_are_confined_and_kept_out_of_the_repository(self):
        ok = str(plan_file(self.tmp, "backfill", []))
        for argv in (["backfill", "--plan", str(_ROOT / "README.md")],
                     ["backfill", "--plan", str(_ROOT / ".github" / "project-sync.json")],
                     ["backfill", "--plan", "/etc/hostname.json"],
                     ["backfill", "--plan", ok, "--report-dir", str(_ROOT / "docs")],
                     ["backfill", "--plan", ok, "--report-dir", str(self.tmp), "--checkpoint", str(_ROOT / "ck.json")],
                     ["plan-backfill", "--out", str(_ROOT / "docs" / "plan.json")]):
            err = io.StringIO()
            with unittest.mock.patch("sys.stderr", err):
                self.assertEqual(pm.main(argv, env={}, transport=self.api), 2, argv)
            self.assertIn("must be", err.getvalue())
        self.assertEqual(self.api.calls, [])

    def test_a_run_needs_an_explicit_plan_and_apply_needs_its_hash(self):
        ok = str(plan_file(self.tmp, "backfill", []))
        for argv in (["backfill"], ["backfill", "--plan", ok, "--apply"], ["plan-backfill"],
                     ["plan-backfill", "--out", str(self.tmp / "p.json"), "--apply"]):
            with unittest.mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(pm.main(argv, env={}, transport=self.api), 2, argv)
        self.assertEqual(self.api.calls, [])

    def test_apply_refuses_a_plan_that_differs_from_the_reviewed_one(self):
        self.api.put("Issue", 38, Status="Ready")
        path = plan_file(self.tmp, "migrate-existing", [epic()])
        reviewed = pm.plan_sha256(path)
        path.write_text(path.read_text().replace('"Planned"', '"Parked"'))
        with self.assertRaisesRegex(pm.MigrationError, "sha256"):
            pm.run("migrate-existing", path, apply=True, transport=self.api, checkpoint=self.tmp / "ck.json",
                   report_dir=self.tmp / "out", out=io.StringIO(), sleep=self.api.settle, expected_sha256=reviewed)
        self.assertEqual(self.api.mutations, [])

    def test_missing_token_fails_cleanly(self):
        ok = str(plan_file(self.tmp, "backfill", []))
        with unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(pm.main(["backfill", "--plan", ok, "--report-dir", str(self.tmp)], env={}), 2)


class Writes(Base):
    def test_apply_sets_only_listed_fields(self):
        self.api.put("Issue", 38, Status="Ready", Horizon="Now")
        r = self.run_stage("migrate-existing", [epic()], apply=True)
        self.assertEqual(r["counts"], {"changed": 1})
        self.assertEqual(self.api.items["I38"]["values"], {"Status": "Backlog", "Roadmap Stage": "Planned", "Horizon": "Now"})
        self.assertEqual({m[0] for m in self.api.mutations}, {"updateProjectV2ItemFieldValue"})

    def test_drift_is_skipped_not_overwritten(self):
        self.api.put("Issue", 38, Status="In Progress")
        r = self.run_stage("migrate-existing", [epic()], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})
        self.assertEqual(self.api.mutations, [])
        self.assertEqual(self.api.items["I38"]["values"]["Status"], "In Progress")

    def test_an_apply_that_skipped_drift_is_not_ok_and_exits_non_zero(self):
        self.api.put("Issue", 38, Status="In Progress")      # drifted
        self.api.put("Issue", 40, Status="Ready")            # still as reviewed
        path = plan_file(self.tmp, "migrate-existing", [epic(38), epic(40)])
        rc = pm.main(["migrate-existing", "--apply", "--plan", str(path), "--plan-sha256", pm.plan_sha256(path),
                      "--report-dir", str(self.tmp / "o")], env={}, transport=self.api)
        self.assertEqual(rc, 1)
        report = json.loads((self.tmp / "o" / "migrate-existing.json").read_text())
        self.assertEqual(report["counts"], {"drift": 1, "changed": 1})
        self.assertFalse(report["ok"])
        self.assertEqual(self.api.items["I38"]["values"], {"Status": "In Progress"})
        self.assertEqual(self.api.items["I40"]["values"], {"Status": "Backlog", "Roadmap Stage": "Planned"})

    def test_drift_in_a_dry_run_and_intended_skips_in_an_apply_stay_ok(self):
        self.api.put("Issue", 38, Status="In Progress")
        self.assertTrue(self.run_stage("migrate-existing", [epic()])["ok"])
        self.api.put("Issue", 38, Status="Ready")
        self.api.put("Issue", 39, state="CLOSED")
        self.api.put("Issue", 41, Status="Backlog", **{"Roadmap Stage": "Planned"})
        held = {"number": 39, "type": "Issue", "set": {"Status": "Done"}, "approved": False}
        plan = [epic(), held, epic(41)]
        self.assertTrue(self.run_stage("migrate-existing", plan, apply=True)["ok"])
        r = self.run_stage("migrate-existing", plan, apply=True)
        self.assertEqual(r["counts"], {"checkpointed": 1, "held": 1, "unchanged": 1})
        self.assertTrue(r["ok"])

    def test_an_issue_closed_during_a_write_fails_the_apply_and_is_not_completed(self):
        self.api.put("Issue", 38, Status="Ready")

        def close_during_status_write(name, variables):
            if name == "updateProjectV2ItemFieldValue" and variables["field"] == "F_Status":
                self.api.content[38]["state"] = "CLOSED"   # closed after the pre-write check
        self.api.before_mutation = close_during_status_write
        r = self.run_stage("migrate-existing", [epic()], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})
        self.assertFalse(r["ok"])
        self.assertIn("GitHub state changed to CLOSED while Status was written", r["items"][0]["detail"])
        self.assertEqual(r["items"][0]["writes"], [("Status", "Backlog")])
        ck = self.tmp / "ck.json"
        self.assertFalse(ck.exists() and "Issue#38" in json.loads(ck.read_text())["done"])
        n = len(self.api.mutations)
        self.assertEqual(self.run_stage("migrate-existing", [epic()], apply=True)["counts"], {"drift": 1})
        self.assertEqual(len(self.api.mutations), n)   # a rerun never writes the closed issue

    def test_existing_stage_value_is_not_overwritten(self):
        self.api.put("Issue", 38, Status="Ready", **{"Roadmap Stage": "Parked"})
        r = self.run_stage("migrate-existing", [epic()], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})
        self.assertEqual(self.api.mutations, [])

    def test_state_drift_skips(self):
        self.api.put("Issue", 38, state="CLOSED", Status="Ready")
        self.assertEqual(self.run_stage("migrate-existing", [epic()], apply=True)["counts"], {"drift": 1})

    def test_held_items_never_written(self):
        self.api.put("Issue", 39, state="CLOSED", Status="In Progress")
        item = {"number": 39, "type": "Issue", "github_state": "CLOSED", "expected_before": {"Status": "In Progress"}, "set": {"Status": "Done"}, "approved": False}
        self.assertEqual(self.run_stage("migrate-existing", [item], apply=True)["counts"], {"held": 1})
        self.assertEqual(self.api.mutations, [])

    def test_rerun_is_idempotent_with_checkpoint(self):
        self.api.put("Issue", 38, Status="Ready")
        self.run_stage("migrate-existing", [epic()], apply=True)
        n = len(self.api.mutations)
        r = self.run_stage("migrate-existing", [epic()], apply=True)
        self.assertEqual(r["counts"], {"checkpointed": 1})
        (self.tmp / "ck.json").unlink()
        r = self.run_stage("migrate-existing", [epic()], apply=True)
        self.assertEqual(r["counts"], {"unchanged": 1})
        self.assertEqual(len(self.api.mutations), n)


class Backfill(Base):
    def test_pr_cannot_be_set_to_inbox(self):
        pr = {"number": 480, "type": "PullRequest", "state": "OPEN", "set": {"Status": "Inbox"}}
        with self.assertRaisesRegex(pm.MigrationError, "Inbox"):
            self.run_stage("backfill", [pr])

    def test_backfill_adds_issue_inbox_pr_without_status_and_closed_research_done(self):
        self.api.add_content("Issue", 500)
        self.api.add_content("PullRequest", 480)
        self.api.add_content("Issue", 181, state="CLOSED")
        items = [
            open_issue(),
            {"number": 480, "type": "PullRequest", "state": "OPEN", "set": {}},
            research(),
        ]
        r = self.run_stage("backfill", items, apply=True)
        self.assertEqual(r["counts"], {"changed": 3})
        self.assertEqual(self.api.items["I500"]["values"], {"Status": "Inbox"})
        self.assertEqual(self.api.items["I480"]["values"], {})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})

    def test_backfill_existing_item_with_status_is_drift(self):
        self.api.put("Issue", 500, Status="Ready")
        r = self.run_stage("backfill", [open_issue()], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})

    def _refused_before_any_call(self, stage, row, pattern):
        self.api.put("Issue", 38, Status="Ready")
        for apply in (False, True):
            with self.assertRaisesRegex(pm.MigrationError, pattern):
                self.run_stage(stage, [row], apply=apply)
        self.assertEqual((self.api.calls, self.api.mutations), ([], []))

    def test_a_writable_row_without_github_state_is_refused(self):
        row = {"number": 38, "type": "Issue", "expected_before": {"Status": None}, "set": {"Status": "Backlog"}}
        self._refused_before_any_call("migrate-existing", row, "Issue#38: .*github_state")

    def test_a_writable_row_with_an_unknown_github_state_is_refused(self):
        row = {"number": 38, "type": "Issue", "github_state": "open", "expected_before": {"Status": None}, "set": {"Status": "Backlog"}}
        self._refused_before_any_call("migrate-existing", row, "github_state")

    def test_a_writable_row_without_expected_before_is_refused(self):
        row = {"number": 38, "type": "Issue", "github_state": "OPEN", "set": {"Status": "Backlog"}}
        self._refused_before_any_call("migrate-existing", row, "expected_before entry for Status")

    def test_a_row_missing_one_expected_before_field_is_refused(self):
        row = epic(expected_before={"Status": "Ready"})
        self._refused_before_any_call("migrate-existing", row, "expected_before entry for Roadmap Stage")

    def test_a_backfill_row_without_state_is_refused(self):
        row = {"number": 500, "type": "Issue", "expected_before": {"Status": None}, "set": {"Status": "Inbox"}}
        self._refused_before_any_call("backfill", row, "state")

    def test_approved_must_be_a_json_boolean(self):
        for value in ("false", "no", 0, None):
            with self.subTest(value=value):
                self._refused_before_any_call("migrate-existing", epic(approved=value), "approved must be JSON true or false")

    def test_held_and_no_op_rows_need_no_guards(self):
        held = {"number": 39, "type": "Issue", "set": {"Status": "Done"}, "approved": False}
        no_op = {"number": 40, "type": "Issue", "set": {}}
        self.api.put("Issue", 39, state="OPEN")
        self.api.put("Issue", 40, Status="Ready")
        r = self.run_stage("migrate-existing", [held, no_op], apply=True)
        self.assertEqual(r["counts"], {"held": 1, "unchanged": 1})
        self.assertEqual(self.api.mutations, [])

    def test_an_add_only_backfill_row_without_state_is_refused(self):
        row = {"number": 480, "type": "PullRequest", "expected_before": {}, "set": {}}
        self._refused_before_any_call("backfill", row, "PullRequest#480: .*state")

    def test_a_pr_reviewed_as_closed_and_merged_since_is_drift(self):
        self.api.put("PullRequest", 470, state="MERGED", Status="Done")
        row = {"number": 470, "type": "PullRequest", "github_state": "CLOSED", "expected_before": {"Status": "Done"}, "set": {"Status": "Backlog"}}
        r = self.run_stage("migrate-existing", [row], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})
        self.assertFalse(r["ok"])
        self.assertEqual(self.api.mutations, [])

    def test_an_add_only_backfill_row_for_a_pr_merged_since_is_not_added(self):
        self.api.add_content("PullRequest", 480, state="MERGED")
        r = self.run_stage("backfill", [{"number": 480, "type": "PullRequest", "state": "OPEN", "set": {}}], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})
        self.assertEqual(self.api.mutations, [])

    def test_unwritable_field_rejected(self):
        with self.assertRaises(pm.MigrationError):
            self.run_stage("backfill", [{"number": 1, "type": "Issue", "state": "OPEN", "set": {"Execution": "Agent"}}])


def research(n=181):
    return {"number": n, "type": "Issue", "state": "CLOSED", "expected_before": {"Status": None}, "set": {"Status": "Done"}}


def open_issue(n=500):
    return {"number": n, "type": "Issue", "state": "OPEN", "expected_before": {"Status": None}, "set": {"Status": "Inbox"}}


class DriftDuringTheRun(Base):
    """The snapshot is minutes old by the time a late item is written."""

    def test_a_status_changed_after_the_snapshot_is_not_overwritten(self):
        self.api.put("Issue", 38, Status="Ready")
        self.api.put("Issue", 40, Status="Ready")

        def person(name, variables):
            if variables.get("item") == "I38":
                self.api.items["I40"]["values"]["Status"] = "In Progress"
        self.api.before_mutation = person
        r = self.run_stage("migrate-existing", [epic(38), epic(40)], apply=True)
        self.assertEqual(r["counts"], {"changed": 1, "drift": 1})
        self.assertEqual(self.api.items["I40"]["values"], {"Status": "In Progress"})
        self.assertFalse(any(v.get("item") == "I40" for _, v in self.api.mutations))

    def test_a_field_changed_between_two_writes_stops_the_item(self):
        self.api.put("Issue", 38, Status="Ready")

        def person(name, variables):
            if variables.get("field") == "F_Status":
                self.api.items["I38"]["values"]["Roadmap Stage"] = "Parked"
        self.api.before_mutation = person
        r = self.run_stage("migrate-existing", [epic(38)], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})
        self.assertEqual(self.api.items["I38"]["values"], {"Status": "Backlog", "Roadmap Stage": "Parked"})

    def test_an_issue_closed_during_the_run_is_not_written(self):
        self.api.put("Issue", 38, Status="Ready")
        self.api.put("Issue", 40, Status="Ready")

        def close(name, variables):
            if variables.get("item") == "I38":
                self.api.content[40]["state"] = "CLOSED"
        self.api.before_mutation = close
        r = self.run_stage("migrate-existing", [epic(38), epic(40)], apply=True)
        self.assertEqual(r["counts"], {"changed": 1, "drift": 1})
        self.assertEqual(self.api.items["I40"]["values"], {"Status": "Ready"})


class NativeWorkflowRaces(Base):
    def test_a_late_item_added_inbox_does_not_replace_done_on_closed_research(self):
        self.api.item_added = "late"
        self.api.add_content("Issue", 181, state="CLOSED")
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})
        self.assertEqual(r["counts"], {"changed": 1})
        self.assertTrue(r["ok"])
        self.assertIn("late native Inbox", r["items"][0]["detail"])

    def test_an_immediate_item_added_inbox_is_the_expected_entry_state(self):
        self.api.item_added = "now"
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.add_content("Issue", 500)
        r = self.run_stage("backfill", [research(), open_issue()], apply=True)
        self.assertEqual(r["counts"], {"changed": 2})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})
        self.assertEqual(self.api.items["I500"]["values"], {"Status": "Inbox"})

    def test_auto_add_before_the_tools_add_keeps_a_status_set_meanwhile(self):
        self.api.add_content("Issue", 181, state="CLOSED")

        def auto_add_then_person(name, variables):
            if name == "addProjectV2ItemById" and "I181" not in self.api.items:
                self.api.items["I181"] = {"number": 181, "archived": False, "values": {"Status": "In Progress"}}
        self.api.before_mutation = auto_add_then_person
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "In Progress"})
        self.assertEqual([m[0] for m in self.api.mutations], ["addProjectV2ItemById"])

    def test_auto_add_with_native_inbox_before_the_tools_add_still_ends_in_done(self):
        self.api.add_content("Issue", 181, state="CLOSED")

        def auto_add(name, variables):
            if name == "addProjectV2ItemById" and "I181" not in self.api.items:
                self.api.items["I181"] = {"number": 181, "archived": False, "values": {"Status": "Inbox"}}
        self.api.before_mutation = auto_add
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"changed": 1})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})

    def _settle_then(self, change):
        """Backfill closed research #181 to Done; during the settle wait,
        apply ``change`` and let its Status become Inbox."""
        self.api.add_content("Issue", 181, state="CLOSED")

        def during_settle():
            change()
            if "I181" in self.api.items:
                self.api.items["I181"]["values"]["Status"] = "Inbox"
        self.api.pending.append(during_settle)
        r = self.run_stage("backfill", [research()], apply=True)
        sets = [m for m in self.api.mutations if m[0] == "updateProjectV2ItemFieldValue"]
        return r, sets

    def test_verify_never_writes_done_onto_an_issue_reopened_while_settling(self):
        r, sets = self._settle_then(lambda: self.api.content[181].update(state="OPEN"))
        self.assertEqual(len(sets), 1)   # only the original Done, no repair
        self.assertEqual(r["counts"], {"verify-failed": 1})
        self.assertIn("GitHub state changed to OPEN", r["items"][0]["detail"])
        self.assertFalse(r["ok"])
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Inbox"})

    def test_verify_never_writes_an_item_archived_while_settling(self):
        r, sets = self._settle_then(lambda: self.api.items["I181"].update(archived=True))
        self.assertEqual(len(sets), 1)
        self.assertEqual(r["counts"], {"verify-failed": 1})
        self.assertIn("archived", r["items"][0]["detail"])
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Inbox"})

    def test_verify_never_writes_an_item_removed_while_settling(self):
        r, sets = self._settle_then(lambda: self.api.items.pop("I181"))
        self.assertEqual(len(sets), 1)
        self.assertEqual(r["counts"], {"verify-failed": 1})
        self.assertIn("removed", r["items"][0]["detail"])
        self.assertNotIn("Issue#181", json.loads((self.tmp / "ck.json").read_text())["done"])

    def test_verify_reports_an_issue_reopened_during_the_repair_write(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.pending.append(lambda: self.api.items["I181"]["values"].update(Status="Inbox"))   # late native Inbox

        def reopen_during_repair(name, variables):
            sets = [m for m in self.api.mutations if m[0] == "updateProjectV2ItemFieldValue"]
            if name == "updateProjectV2ItemFieldValue" and len(sets) == 1:   # the repair, after the first Done
                self.api.content[181]["state"] = "OPEN"
        self.api.before_mutation = reopen_during_repair
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"verify-failed": 1})
        self.assertIn("GitHub state changed to OPEN while the repair was written", r["items"][0]["detail"])
        self.assertFalse(r["ok"])
        self.assertNotIn("Issue#181", json.loads((self.tmp / "ck.json").read_text())["done"])

    def test_an_added_item_stays_pending_when_verification_aborts(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        original = pm.Project.refresh
        settled = []

        def outage_after_settling(project, item_id):
            if settled:   # the verification read, after the write succeeded
                raise pm.MigrationError("GraphQL error: simulated outage")
            return original(project, item_id)

        def reopen_while_settling():
            settled.append(True)
            self.api.content[181]["state"] = "OPEN"
        self.api.pending.append(reopen_while_settling)
        with unittest.mock.patch.object(pm.Project, "refresh", outage_after_settling):
            r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"changed": 1})
        self.assertFalse(r["ok"])
        self.assertNotIn("Issue#181", json.loads((self.tmp / "ck.json").read_text())["done"])
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})   # re-planned from live data, not "checkpointed"
        self.assertFalse(r["ok"])

    def test_a_rerun_repair_that_fails_verification_is_re_planned_next_time(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        self.run_stage("backfill", [research()], apply=True)
        self.api.items["I181"]["values"]["Status"] = "Inbox"   # late native Inbox
        self.api.pending.append(lambda: self.api.content[181].update(state="OPEN"))   # reopened while settling
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"verify-failed": 1})
        self.assertNotIn("Issue#181", json.loads((self.tmp / "ck.json").read_text())["done"])
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})   # not "checkpointed"
        self.assertFalse(r["ok"])

    def test_an_add_whose_response_was_lost_is_still_verified_and_repaired(self):
        self.api.item_added = "late"
        self.api.add_content("Issue", 181, state="CLOSED")
        original = self.api._mutation

        def add_commits_then_response_is_lost(query, variables):
            result = original(query, variables)
            if "addProjectV2ItemById" in query:
                self.api._mutation = original
                raise pm.MigrationError("connection reset after the add was sent")
            return result
        self.api._mutation = add_commits_then_response_is_lost
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"failed": 1})
        self.assertIn("I181", self.api.items)   # the add did commit
        r = self.run_stage("backfill", [research()], apply=True)   # the retry sees an existing item
        self.assertEqual(r["counts"], {"changed": 1})
        self.assertTrue(r["ok"])
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})   # late Inbox written over
        self.api.items["I181"]["values"]["Status"] = "Inbox"   # an even later native Inbox
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"repaired": 1})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})

    def test_an_added_item_left_unchanged_by_a_retry_is_still_verified(self):
        self.api.item_added = "late"
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.add_content("Issue", 246, state="CLOSED")

        def fail_the_second_items_write(name, variables):
            if name == "updateProjectV2ItemFieldValue" and variables["item"] == "I246":
                self.api.before_mutation = None
                raise pm.MigrationError("GraphQL error: simulated outage")
        self.api.before_mutation = fail_the_second_items_write
        r = self.run_stage("backfill", [research(181), research(246)], apply=True)
        self.assertEqual(r["counts"], {"changed": 1, "failed": 1})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})   # its late Inbox is still pending
        r = self.run_stage("backfill", [research(181), research(246)], apply=True)
        self.assertEqual(r["counts"], {"unchanged": 1, "changed": 1})
        self.assertTrue(r["ok"])
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})   # the late Inbox was written over
        self.assertEqual(self.api.items["I246"]["values"], {"Status": "Done"})
        self.assertEqual(json.loads((self.tmp / "ck.json").read_text())["done"], ["Issue#181", "Issue#246"])

    def test_an_added_item_is_done_once_it_verifies(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(json.loads((self.tmp / "ck.json").read_text())["done"], ["Issue#181"])

    def test_a_person_changing_an_added_item_while_settling_is_reported_not_overwritten(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.pending.append(lambda: self.api.items["I181"]["values"].update(Status="Ready"))
        self.api.before_mutation = None
        r = self.run_stage("backfill", [research()], apply=True)
        self.assertEqual(r["counts"], {"verify-failed": 1})
        self.assertFalse(r["ok"])
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Ready"})

    def test_a_native_inbox_after_the_run_is_repaired_by_a_rerun_and_nothing_else_is(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.add_content("Issue", 246, state="CLOSED")
        self.run_stage("backfill", [research(181), research(246)], apply=True)
        self.api.items["I181"]["values"]["Status"] = "Inbox"   # native, much later
        self.api.items["I246"]["values"]["Status"] = "Ready"   # a person
        r = self.run_stage("backfill", [research(181), research(246)], apply=True)
        self.assertEqual(r["counts"], {"repaired": 1, "checkpointed": 1})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})
        self.assertEqual(self.api.items["I246"]["values"], {"Status": "Ready"})


class PartialFailure(Base):
    def test_a_failed_write_after_an_add_stops_reports_and_a_rerun_completes(self):
        self.api.item_added = "now"
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.add_content("Issue", 500)
        self.api.fail_next_set = 1
        r = self.run_stage("backfill", [research(), open_issue()], apply=True)
        self.assertEqual(r["counts"], {"failed": 1, "not-run": 1})
        self.assertFalse(r["ok"])
        self.assertTrue((self.tmp / "out" / "backfill.json").exists())
        self.assertEqual(json.loads((self.tmp / "ck.json").read_text())["added"], ["Issue#181"])
        self.assertNotIn("I500", self.api.items)
        r = self.run_stage("backfill", [research(), open_issue()], apply=True)
        self.assertEqual(r["counts"], {"changed": 2})
        self.assertTrue(r["ok"])
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})

    def test_a_checkpoint_that_cannot_be_saved_after_a_write_fails_the_run_with_a_report(self):
        self.api.put("Issue", 38, Status="Ready")
        self.api.put("Issue", 40, Status="Ready")

        def disk_full(_ck):
            raise OSError(28, "No space left on device")
        with unittest.mock.patch.object(pm.Checkpoint, "save", disk_full):
            r = self.run_stage("migrate-existing", [epic(38), epic(40)], apply=True)
        self.assertEqual(r["counts"], {"failed": 1, "not-run": 1})
        self.assertIn("written, but the checkpoint could not be saved", r["items"][0]["detail"])
        self.assertFalse(r["ok"])
        self.assertTrue((self.tmp / "out" / "migrate-existing.json").exists())
        self.assertEqual(self.api.items["I40"]["values"], {"Status": "Ready"})   # stopped before the next item

    def test_main_exits_non_zero_after_a_failure(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.fail_next_set = 1
        path = plan_file(self.tmp, "backfill", [research()])
        rc = pm.main(["backfill", "--apply", "--settle-seconds", "0", "--plan", str(path), "--plan-sha256", pm.plan_sha256(path),
                      "--report-dir", str(self.tmp / "o")], env={}, transport=self.api)
        self.assertEqual(rc, 1)

    def test_the_plan_that_runs_is_the_plan_that_was_hashed(self):
        self.api.put("Issue", 38, Status="Ready")
        path = plan_file(self.tmp, "migrate-existing", [epic()])
        digest = pm.plan_sha256(path)
        original = pm.load_plan

        def swap_file_after_hashing(p, stage, raw=None):
            plan_file(self.tmp, "migrate-existing", [epic(set={"Status": "Done"})])   # edited in between
            return original(p, stage, raw)
        with unittest.mock.patch.object(pm, "load_plan", swap_file_after_hashing):
            r = pm.run("migrate-existing", path, apply=True, transport=self.api, checkpoint=self.tmp / "ck.json",
                       report_dir=self.tmp / "out", out=io.StringIO(), settle_seconds=0, sleep=self.api.settle,
                       expected_sha256=digest)
        self.assertEqual(r["plan_sha256"], digest)
        self.assertEqual(self.api.items["I38"]["values"]["Status"], "Backlog")   # the reviewed target, not Done

    def test_a_checkpoint_from_another_stage_is_refused(self):
        (self.tmp / "ck.json").write_text(json.dumps({"stage": "backfill", "done": ["Issue#38"]}))
        self.api.put("Issue", 38, Status="Ready")
        with self.assertRaisesRegex(pm.MigrationError, "checkpoint"):
            self.run_stage("migrate-existing", [epic()], apply=True)

    def test_a_checkpoint_from_a_different_plan_is_refused(self):
        self.api.put("Issue", 38, Status="Ready")
        self.run_stage("migrate-existing", [epic()], apply=True)
        self.assertIn("Issue#38", json.loads((self.tmp / "ck.json").read_text())["done"])
        self.api.items["I38"]["values"] = {"Status": "Ready"}   # back to the state plan B was reviewed against
        n = len(self.api.mutations)
        revised = epic(set={"Status": "In Progress", "Roadmap Stage": "Planned"})
        for apply in (True, False):
            with self.assertRaisesRegex(pm.MigrationError, "checkpoint .* plan sha256"):
                self.run_stage("migrate-existing", [revised], apply=apply)
        self.assertEqual(len(self.api.mutations), n)
        (self.tmp / "ck.json").unlink()   # the explicit reset
        r = self.run_stage("migrate-existing", [revised], apply=True)
        self.assertEqual(r["counts"], {"changed": 1})
        self.assertEqual(self.api.items["I38"]["values"]["Status"], "In Progress")

    def test_a_checkpoint_that_names_no_plan_is_refused(self):
        (self.tmp / "ck.json").write_text(json.dumps({"stage": "migrate-existing", "project_id": pm.PROJECT_ID, "done": ["Issue#38"]}))
        self.api.put("Issue", 38, Status="Ready")
        with self.assertRaisesRegex(pm.MigrationError, "plan sha256 None"):
            self.run_stage("migrate-existing", [epic()], apply=True)

    def test_the_checkpoint_records_the_plan_digest(self):
        self.api.put("Issue", 38, Status="Ready")
        self.run_stage("migrate-existing", [epic()], apply=True)
        digest = pm.plan_sha256(self.tmp / "migrate-existing.json")
        self.assertEqual(json.loads((self.tmp / "ck.json").read_text())["plan_sha256"], digest)

    def test_dry_run_writes_no_checkpoint_and_does_not_wait(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        waits = []
        pm.run("backfill", plan_file(self.tmp, "backfill", [research()]), apply=False, transport=self.api,
               checkpoint=self.tmp / "ck.json", report_dir=self.tmp / "out", out=io.StringIO(), sleep=waits.append)
        self.assertEqual((waits, self.api.mutations), ([], []))
        self.assertFalse((self.tmp / "ck.json").exists())


class NeverMutations(unittest.TestCase):
    def test_only_allowlisted_mutations_exist(self):
        text = _SCRIPT.read_text()
        names = set(re.findall(r"mutation\([^)]*\)\{(\w+)", text))
        self.assertEqual(names, set(pm.ALLOWED_MUTATIONS))
        for bad in ("deleteProjectV2", "archiveProjectV2Item", "clearProjectV2ItemFieldValue", "closeIssue", "mergePullRequest",
                    "reopenIssue", "deleteProjectV2Field", "updateProjectV2Field(", "addPullRequestReview", "enablePullRequestAutoMerge"):
            self.assertNotIn(bad, text)

    def test_cannot_reach_project_sync_settings_or_rest(self):
        text = _SCRIPT.read_text()
        self.assertEqual(re.findall(r"https://[^\s\"']+", text), ["https://api.github.com/graphql"])
        for bad in ("PROJECT_SYNC", "actions/variables", "actions/secrets", "updateRepository", "createIssue", "updateIssue("):
            self.assertNotIn(bad, text)

    def test_the_http_opener_refuses_every_scheme_but_https(self):
        opener = pm._https_only_opener()
        for url in ("file:///etc/passwd", "ftp://example.com/x", "http://example.com/"):
            with self.subTest(url=url), self.assertRaisesRegex(urllib.error.URLError, "unknown url type"):
                opener.open(url, timeout=1)

    def test_graphql_wrapper_refuses_other_mutations(self):
        gql = pm.GraphQL(lambda q, v: {}, allow_mutations=True)
        with self.assertRaises(pm.MigrationError):
            gql.mutate("deleteProjectV2Item", "mutation{deleteProjectV2Item(input:{}){x}}", {})
        with self.assertRaises(pm.MigrationError):
            gql.query("mutation{x}", {})
        with self.assertRaises(pm.MigrationError):
            pm.GraphQL(lambda q, v: {}, allow_mutations=False).mutate("addProjectV2ItemById", pm.ADD_M, {})


class PlanBackfill(Base):
    """plan-backfill reads live data only; counts and lists are runtime inputs."""

    def setUp(self):
        super().setUp()
        self.api.put("Issue", 38, Status="Ready")              # already on the board
        self.api.repo_issues = {
            38: ("Roadmap", "OPEN", ["roadmap"]),
            500: ("Open finding", "OPEN", ["audit-finding"]),
            501: ("Another", "OPEN", []),
            181: ("Old research", "CLOSED", ["research"]),
            295: ("Research-y feature", "CLOSED", ["enhancement"]),
        }
        self.api.repo_prs = {480: ("Bump x", "OPEN", ["dependencies"])}

    def plan(self):
        return pm.plan_backfill(self.api, generated_at="2026-01-01T00:00:00Z")

    def test_proposes_only_what_is_missing_with_the_right_target(self):
        plan = self.plan()
        got = {(i["type"], i["number"]): i["set"] for i in plan["items"]}
        self.assertEqual(got, {("Issue", 500): {"Status": "Inbox"}, ("Issue", 501): {"Status": "Inbox"},
                               ("PullRequest", 480): {}, ("Issue", 181): {"Status": "Done"}})
        self.assertEqual(plan["counts"], {"open-issue": 2, "open-pr": 1, "closed-research": 1})
        self.assertEqual(self.api.mutations, [])
        self.assertFalse(any(q.lstrip().startswith("mutation") for q in self.api.calls))

    def test_the_generated_plan_runs_and_validates(self):
        path = self.tmp / "plan.json"
        path.write_text(json.dumps(self.plan()))
        pm.load_plan(path, "backfill")
        for n, state in ((500, "OPEN"), (501, "OPEN"), (181, "CLOSED")):
            self.api.add_content("Issue", n, state=state)
        self.api.add_content("PullRequest", 480)
        r = pm.run("backfill", path, apply=True, transport=self.api, checkpoint=self.tmp / "ck.json", report_dir=self.tmp / "out",
                   out=io.StringIO(), settle_seconds=0, sleep=self.api.settle, expected_sha256=pm.plan_sha256(path))
        self.assertEqual(r["counts"], {"changed": 4})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})
        self.assertEqual(self.api.items["I480"]["values"], {})

    def test_cli_writes_a_new_plan_and_never_overwrites_one(self):
        out = self.tmp / "plans" / "backfill.json"
        buf = io.StringIO()
        with unittest.mock.patch("sys.stdout", buf):
            self.assertEqual(pm.main(["plan-backfill", "--out", str(out)], env={}, transport=self.api), 0)
        self.assertIn(pm.plan_sha256(out), buf.getvalue())
        before = out.read_bytes()
        with unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(pm.main(["plan-backfill", "--out", str(out)], env={}, transport=self.api), 2)
        self.assertEqual(out.read_bytes(), before)


class ReviewedMigratePlan(Base):
    """The migrate-existing plan is the maintainer's reviewed decisions."""

    def test_items_on_the_board_but_not_in_the_plan_are_reported_and_untouched(self):
        self.api.put("Issue", 38, Status="Ready")
        self.api.put("Issue", 77, Status="Ready")
        r = self.run_stage("migrate-existing", [epic(38)], apply=True)
        self.assertEqual(r["counts"], {"changed": 1, "not-in-plan": 1})
        self.assertEqual(self.api.items["I77"]["values"], {"Status": "Ready"})

    def test_a_held_decision_and_a_keep_status_decision(self):
        self.api.put("Issue", 39, Status="Ready")
        self.api.put("Issue", 179, Status="In Progress")
        held = {"number": 39, "type": "Issue", "github_state": "OPEN", "expected_before": {}, "set": {}, "approved": False}
        keep = {"number": 179, "type": "Issue", "github_state": "OPEN", "expected_before": {"Status": "In Progress", "Roadmap Stage": None},
                "set": {"Roadmap Stage": "Planned"}, "approved": True}
        r = self.run_stage("migrate-existing", [held, keep], apply=True)
        self.assertEqual(r["counts"], {"held": 1, "changed": 1})
        self.assertEqual(self.api.items["I39"]["values"], {"Status": "Ready"})
        self.assertEqual(self.api.items["I179"]["values"], {"Status": "In Progress", "Roadmap Stage": "Planned"})


class ProjectSyncLeavesBacklogAlone(unittest.TestCase):
    """Backlog is a lifecycle option project-sync does not know; it must never
    be treated as an empty Status (#441)."""

    def test_backlog_is_unknown_and_never_allowed_from(self):
        spec = importlib.util.spec_from_file_location("project_sync_for_backlog", _ROOT / "scripts" / "project_sync.py")
        if spec is None or spec.loader is None:
            self.fail("cannot load scripts/project_sync.py")
        ps = importlib.util.module_from_spec(spec)
        sys.modules["project_sync_for_backlog"] = ps
        spec.loader.exec_module(ps)
        config = ps.Config.load(_ROOT / ".github" / "project-sync.json")
        key = config.key_of("Backlog")
        self.assertIsNotNone(key)
        for _draft, _ready, allowed in ps.RULES.values():
            self.assertNotIn(key, allowed)


if __name__ == "__main__":
    unittest.main()
