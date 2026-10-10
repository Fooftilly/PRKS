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

    def add_content(self, kind, number, state="OPEN", repo="Fooftilly/PRKS"):
        self.content[number] = {"__typename": kind, "id": f"N{number}", "number": number, "state": state, "repo": repo}

    def put(self, kind, number, state="OPEN", archived=False, **values):
        self.add_content(kind, number, state)
        self.items[f"I{number}"] = {"number": number, "archived": archived, "values": dict(values)}

    def _fid(self, name):
        return "F_" + name.replace(" ", "_")

    def __call__(self, query, variables):
        self.calls.append(query)
        if query.lstrip().startswith("mutation"):
            name = re.search(r"\{(\w+)\(", query).group(1)
            self.mutations.append((name, variables))
            if name == "addProjectV2ItemById":
                n = int(variables["content"][1:])
                self.items[f"I{n}"] = {"number": n, "archived": False, "values": {}}
                return {name: {"item": {"id": f"I{n}"}}}
            item = self.items[variables["item"]]
            field = next(f for f in self.fields if self._fid(f) == variables["field"])
            item["values"][field] = variables["option"].split(":", 1)[1]
            return {name: {"projectV2Item": {"id": variables["item"]}}}
        if "issueOrPullRequest" in query:
            c = self.content[variables["number"]]
            return {"repository": {"issueOrPullRequest": {k: c[k] for k in ("__typename", "id", "number", "state")}}}
        if "items(first" in query:
            nodes = []
            for iid, it in self.items.items():
                c = self.content[it["number"]]
                nodes.append({"id": iid, "isArchived": it["archived"],
                              "fieldValues": {"nodes": [{"name": v, "field": {"name": f}} for f, v in it["values"].items()] + [{}]},
                              "content": {"__typename": c["__typename"], "number": c["number"], "state": c["state"], "repository": {"nameWithOwner": c["repo"]}}})
            return {"user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}
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
                      checkpoint=self.tmp / "ck.json", report_dir=self.tmp / "out", out=io.StringIO())


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


class NeverMutations(unittest.TestCase):
    def test_only_allowlisted_mutations_exist(self):
        text = _SCRIPT.read_text()
        names = set(re.findall(r"mutation\([^)]*\)\{(\w+)", text))
        self.assertEqual(names, set(pm.ALLOWED_MUTATIONS))
        for bad in ("deleteProjectV2", "archiveProjectV2Item", "clearProjectV2ItemFieldValue", "closeIssue", "mergePullRequest",
                    "reopenIssue", "deleteProjectV2Field", "updateProjectV2Field(", "addPullRequestReview", "enablePullRequestAutoMerge"):
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
