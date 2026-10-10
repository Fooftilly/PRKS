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
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "project3_migrate.py"
_spec = importlib.util.spec_from_file_location("project3_migrate", _SCRIPT)
assert _spec and _spec.loader
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
            name = re.search(r"\{(\w+)\(", query).group(1)
            if self.before_mutation:
                self.before_mutation(name, variables)
            if name == "updateProjectV2ItemFieldValue" and self.fail_next_set:
                self.fail_next_set -= 1
                raise pm.MigrationError("GraphQL error: simulated outage")
            self.mutations.append((name, variables))
            if name == "addProjectV2ItemById":
                n = int(variables["content"][1:])
                iid = f"I{n}"
                if iid not in self.items:  # the real API returns an existing item unchanged
                    self.items[iid] = {"number": n, "archived": False, "values": {}}
                    if self.item_added and self.content[n]["__typename"] == "Issue":
                        def native(item=self.items[iid]):
                            item["values"]["Status"] = "Inbox"
                        if self.item_added == "now":
                            native()
                        else:
                            self.pending.append(native)
                return {name: {"item": {"id": iid}}}
            item = self.items[variables["item"]]
            field = next(f for f in self.fields if self._fid(f) == variables["field"])
            item["values"][field] = variables["option"].split(":", 1)[1]
            return {name: {"projectV2Item": {"id": variables["item"]}}}
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
        fields = [{"id": self._fid(n), "name": n, "dataType": "SINGLE_SELECT", "options": [{"id": f"{n}:{o}", "name": o} for o in opts]} for n, opts in self.fields.items()]
        return {"user": {"projectV2": {"id": self.project_id, "number": 3, "owner": {"login": "Fooftilly"}, "fields": {"nodes": fields}}}}


def manifest(tmp: Path, stage: str, items, scope=SCOPE) -> Path:
    p = tmp / f"{stage}.json"
    p.write_text(json.dumps({"scope": scope, "stage": stage, "items": items}))
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
        return pm.run(stage, manifest(self.tmp, stage, items, **kw), apply=apply, transport=self.api,
                      checkpoint=self.tmp / "ck.json", report_dir=self.tmp / "out", out=io.StringIO(),
                      settle_seconds=0, sleep=self.api.settle)


class DryRunAndGuards(Base):
    def test_dry_run_is_default_and_sends_no_mutation(self):
        self.api.put("Issue", 38, Status="Ready")
        rc = pm.main(["migrate-existing", "--manifest", str(manifest(self.tmp, "migrate-existing", [epic()])), "--report-dir", str(self.tmp / "o")], env={}, transport=self.api)
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

    def test_apply_refused_when_manifest_option_missing(self):
        self.api.fields["Roadmap Stage"] = ["Idea"]
        self.api.put("Issue", 38, Status="Ready")
        with self.assertRaisesRegex(pm.MigrationError, "Planned"):
            self.run_stage("migrate-existing", [epic()], apply=True)

    def test_scope_validation(self):
        bad = dict(SCOPE, repository="someone/else")
        with self.assertRaises(pm.MigrationError):
            self.run_stage("migrate-existing", [epic()], scope=bad)
        with self.assertRaises(pm.MigrationError):
            pm.load_manifest(manifest(self.tmp, "migrate-existing", [epic()]), "backfill")
        self.api = FakeAPI(project_id="PVT_other")
        with self.assertRaisesRegex(pm.MigrationError, "scope"):
            self.run_stage("migrate-existing", [epic()])

    def test_cli_paths_are_confined(self):
        ok = str(manifest(self.tmp, "backfill", []))
        for argv in (["--manifest", str(_ROOT / "README.md")],
                     ["--manifest", str(_ROOT / ".github" / "project-sync.json")],
                     ["--manifest", ok, "--report-dir", str(_ROOT / "docs")],
                     ["--manifest", ok, "--report-dir", str(self.tmp), "--checkpoint", str(_ROOT / "ck.json")]):
            err = io.StringIO()
            with unittest.mock.patch("sys.stderr", err):
                self.assertEqual(pm.main(["backfill", *argv], env={}, transport=self.api), 2, argv)
            self.assertIn("must be", err.getvalue())
        self.assertEqual(self.api.calls, [])

    def test_missing_token_fails_cleanly(self):
        self.assertEqual(pm.main(["backfill"], env={}), 2)


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
            {"number": 500, "type": "Issue", "state": "OPEN", "set": {"Status": "Inbox"}},
            {"number": 480, "type": "PullRequest", "state": "OPEN", "set": {}},
            {"number": 181, "type": "Issue", "state": "CLOSED", "set": {"Status": "Done"}},
        ]
        r = self.run_stage("backfill", items, apply=True)
        self.assertEqual(r["counts"], {"changed": 3})
        self.assertEqual(self.api.items["I500"]["values"], {"Status": "Inbox"})
        self.assertEqual(self.api.items["I480"]["values"], {})
        self.assertEqual(self.api.items["I181"]["values"], {"Status": "Done"})

    def test_backfill_existing_item_with_status_is_drift(self):
        self.api.put("Issue", 500, Status="Ready")
        r = self.run_stage("backfill", [{"number": 500, "type": "Issue", "state": "OPEN", "set": {"Status": "Inbox"}}], apply=True)
        self.assertEqual(r["counts"], {"drift": 1})

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

    def test_main_exits_non_zero_after_a_failure(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        self.api.fail_next_set = 1
        rc = pm.main(["backfill", "--apply", "--settle-seconds", "0", "--manifest", str(manifest(self.tmp, "backfill", [research()])),
                      "--report-dir", str(self.tmp / "o")], env={}, transport=self.api)
        self.assertEqual(rc, 1)

    def test_a_checkpoint_from_another_stage_is_refused(self):
        (self.tmp / "ck.json").write_text(json.dumps({"stage": "backfill", "done": ["Issue#38"]}))
        self.api.put("Issue", 38, Status="Ready")
        with self.assertRaisesRegex(pm.MigrationError, "checkpoint"):
            self.run_stage("migrate-existing", [epic()], apply=True)

    def test_dry_run_writes_no_checkpoint_and_does_not_wait(self):
        self.api.add_content("Issue", 181, state="CLOSED")
        waits = []
        pm.run("backfill", manifest(self.tmp, "backfill", [research()]), apply=False, transport=self.api,
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

    def test_graphql_wrapper_refuses_other_mutations(self):
        gql = pm.GraphQL(lambda q, v: {}, allow_mutations=True)
        with self.assertRaises(pm.MigrationError):
            gql.mutate("deleteProjectV2Item", "mutation{deleteProjectV2Item(input:{}){x}}", {})
        with self.assertRaises(pm.MigrationError):
            gql.query("mutation{x}", {})
        with self.assertRaises(pm.MigrationError):
            pm.GraphQL(lambda q, v: {}, allow_mutations=False).mutate("addProjectV2ItemById", pm.ADD_M, {})

    def test_committed_manifests_are_valid(self):
        for stage, path in pm.DEFAULT_MANIFEST.items():
            data = pm.load_manifest(path, stage)
            self.assertTrue(data["items"])
        existing = pm.load_manifest(pm.DEFAULT_MANIFEST["migrate-existing"], "migrate-existing")["items"]
        self.assertEqual(len(existing), 44)
        self.assertFalse(next(i for i in existing if i["number"] == 39)["approved"])


class CommittedManifests(unittest.TestCase):
    """Pins the counts and the maintainer's mapping decisions (2026-10-10)."""

    def setUp(self):
        self.existing = {i["number"]: i for i in pm.load_manifest(pm.DEFAULT_MANIFEST["migrate-existing"], "migrate-existing")["items"]}
        self.backfill = pm.load_manifest(pm.DEFAULT_MANIFEST["backfill"], "backfill")["items"]

    def test_counts(self):
        self.assertEqual(len(self.existing), 44)
        self.assertEqual(sum(i["baseline"]["Work Type"] == "Epic" for i in self.existing.values()), 35)
        planned = json.loads((pm._DOCS / "baseline-planned.json").read_text())
        planned_numbers = sorted(i["number"] if isinstance(i, dict) else i for i in planned["items"])
        self.assertEqual(planned_numbers, sorted(n for n, i in self.existing.items() if i["baseline"]["Status"] == "Planned"))
        self.assertEqual(len(planned_numbers), 23)
        self.assertEqual(len(self.backfill), 230)
        kinds = {}
        for i in self.backfill:
            kinds[(i["type"], i["state"])] = kinds.get((i["type"], i["state"]), 0) + 1
        self.assertEqual(kinds, {("Issue", "OPEN"): 215, ("PullRequest", "OPEN"): 12, ("Issue", "CLOSED"): 3})
        self.assertFalse({(i["type"], i["number"]) for i in self.backfill} & {("Issue", n) for n in self.existing})

    def test_maintainer_decisions(self):
        self.assertEqual(self.existing[35]["set"], {"Status": "Backlog"})
        self.assertEqual(self.existing[52]["set"], {"Status": "Backlog", "Roadmap Stage": "Research / Design"})
        self.assertEqual(self.existing[179]["set"], {"Roadmap Stage": "Planned"})
        self.assertEqual(self.existing[179]["expected_before"]["Status"], "In Progress")
        self.assertFalse(self.existing[39]["approved"])
        self.assertEqual(self.existing[39]["set"], {})
        closed_research = sorted(i["number"] for i in self.backfill if i["state"] == "CLOSED")
        self.assertEqual(closed_research, [181, 234, 246])
        self.assertTrue(all(i["set"] == {} for i in self.backfill if i["type"] == "PullRequest"))


class ProjectSyncLeavesBacklogAlone(unittest.TestCase):
    """Backlog is a lifecycle option project-sync does not know; it must never
    be treated as an empty Status (#441)."""

    def test_backlog_is_unknown_and_never_allowed_from(self):
        spec = importlib.util.spec_from_file_location("project_sync_for_backlog", _ROOT / "scripts" / "project_sync.py")
        assert spec and spec.loader
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
