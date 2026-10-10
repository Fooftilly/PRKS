#!/usr/bin/env python3
"""One-off Project #3 migration and backfill, dry-run by default (#441).

Commands:

  plan-backfill     read live PRKS issues/PRs and Project #3 membership and
                    write a backfill plan for review (read-only)
  migrate-existing  run a reviewed plan that sets Status / Roadmap Stage on
                    items already on Project #3
  backfill          run a reviewed backfill plan: add the listed items, then
                    set the listed Status

Plans are local files the maintainer reviews; they are never checked in.
The migrate-existing plan carries the human decisions (for example the
pre-rename Planned epics, which the live Ready option cannot tell apart).
``--apply`` runs only the exact file that was reviewed: it requires
``--plan-sha256`` to match the file, and never regenerates a plan.

Safety contract (enforced here, not only documented):

* Dry-run unless ``--apply`` is given. Dry-run sends queries only.
* The only mutations this file can send are ``addProjectV2ItemById`` and
  ``updateProjectV2ItemFieldValue`` (single-select). Anything else is refused
  by ``GraphQL.mutate``. No deletes, clears, archives, closes, merges.
* Scope: owner Fooftilly, project 3, node id PVT_kwHOAsc2_s4BkAo3, content
  from Fooftilly/PRKS only. The plan must name the same scope.
* Field and option IDs are resolved by name at run time. ``--apply`` refuses
  to run unless the Roadmap Stage and Execution fields and the Backlog Status
  option exist, and every option the plan uses exists.
* Drift: a field is written only when its current value equals the
  plan's ``expected_before`` value (None = empty) and the issue/PR state
  matches. The item is re-read immediately before every write, not only in
  the initial snapshot, so a change made while the run is in progress is
  never overwritten. In the backfill stage Inbox also counts as an expected
  Status, because the native "Item added" workflow sets it on any item that
  auto-add or this tool adds. Otherwise the item is skipped and reported.
  Fields the plan does not list are never touched. Items with
  ``approved: false`` are held.
* PR items are never set to Inbox (plan validation).
* After the writes, every item this run added is re-read once the native
  workflows have had ``--settle-seconds`` to run. A late "Item added" Inbox
  that replaced the written Status is written over once; any other change
  is reported as ``verify-failed`` and left alone.
* A checkpoint file records added and completed items, so reruns are
  idempotent and a completed item is not rewritten after a person changes
  it. A rerun only repairs a late native Inbox on an item this tool added.
  A failure stops the run, writes the report and checkpoint, and exits 1.

* Paths are confined to the home and temp directories and refused inside
  this repository, so plans, reports and checkpoints stay out of version
  control and an argument cannot point the tool at an arbitrary file.

Token: ``PROJECT3_MIGRATION_TOKEN`` or ``GH_TOKEN`` / ``GITHUB_TOKEN``. Never
printed. Needs the classic ``project`` scope for --apply (read for dry-run).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

OWNER = "Fooftilly"
REPOSITORY = "Fooftilly/PRKS"
PROJECT_NUMBER = 3
PROJECT_ID = "PVT_kwHOAsc2_s4BkAo3"
# Single-select fields that must exist, with the options each must have,
# before --apply (§2 and §3 of docs/agent-workflows/project-3.md).
REQUIRED_OPTIONS = {
    "Status": ("Backlog",),
    "Roadmap Stage": ("Idea", "Research / Design", "Planned", "Parked"),
    "Execution": ("Human", "Agent", "Mixed"),
}
ALLOWED_MUTATIONS = ("addProjectV2ItemById", "updateProjectV2ItemFieldValue")
STAGES = ("migrate-existing", "backfill")
COMMANDS = ("plan-backfill",) + STAGES
RESEARCH_LABEL = "research"
# The Status the native "Item added" workflow gives a newly added issue (§5).
NATIVE_ENTRY_STATUS = "Inbox"
DEFAULT_SETTLE_SECONDS = 30.0
_REPO = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "prks-project3-migration"


class MigrationError(Exception):
    pass


Transport = Callable[[str, dict], dict]


def _https_only_opener() -> urllib.request.OpenerDirector:
    """An opener that can speak only HTTPS (through the environment's proxy,
    if any): unlike urlopen, it has no file:// or ftp:// handler."""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(),
                    urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(),
                    urllib.request.UnknownHandler()):
        opener.add_handler(handler)
    return opener


def http_transport(token: str) -> Transport:
    opener = _https_only_opener()

    def send(query: str, variables: dict) -> dict:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
        )
        with opener.open(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
        if body.get("errors"):
            raise MigrationError("GraphQL error: " + "; ".join(e.get("message", "?") for e in body["errors"]))
        return body["data"]

    return send


class GraphQL:
    """Wraps a transport; only allow-listed mutations, and none in dry-run."""

    def __init__(self, transport: Transport, *, allow_mutations: bool) -> None:
        self._send = transport
        self.allow_mutations = allow_mutations

    def query(self, text: str, variables: dict) -> dict:
        if text.lstrip().startswith("mutation"):
            raise MigrationError("query() refuses mutations")
        return self._send(text, variables)

    def mutate(self, name: str, text: str, variables: dict) -> dict:
        if name not in ALLOWED_MUTATIONS:
            raise MigrationError(f"mutation {name!r} is not allowed")
        head = text.split("{", 2)
        if not text.lstrip().startswith("mutation") or len(head) < 3 or name not in head[1]:
            raise MigrationError("mutation text does not match its declared name")
        for other in ("delete", "clear", "archive", "close", "merge", "reopen", "approve"):
            if other in text.lower().replace("projectv2item", ""):
                raise MigrationError(f"mutation text contains forbidden operation {other!r}")
        if not self.allow_mutations:
            raise MigrationError("mutations are disabled (dry-run)")
        return self._send(text, variables)


PROJECT_Q = """query($owner:String!,$number:Int!){user(login:$owner){projectV2(number:$number){id number owner{... on User{login}}
fields(first:50){nodes{... on ProjectV2FieldCommon{id name dataType} ... on ProjectV2SingleSelectField{options{id name}}}}}}}"""

ITEM_FIELDS = """id isArchived
fieldValues(first:40){nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}}}
content{__typename ... on Issue{number state repository{nameWithOwner}} ... on PullRequest{number state repository{nameWithOwner}}}"""

ITEMS_Q = ("query($owner:String!,$number:Int!,$after:String){user(login:$owner){projectV2(number:$number){"
           "items(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{" + ITEM_FIELDS + "}}}}}")

ITEM_Q = "query($id:ID!){node(id:$id){... on ProjectV2Item{project{id} " + ITEM_FIELDS + "}}}"

CONTENT_Q = """query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issueOrPullRequest(number:$number){
__typename ... on Issue{id number state} ... on PullRequest{id number state}}}}"""

_REPO_PAGE = "pageInfo{hasNextPage endCursor} nodes{number title state labels(first:30){nodes{name}}}"

REPO_ISSUES_Q = ("query($owner:String!,$name:String!,$states:[IssueState!],$labels:[String!],$after:String){"
                 "repository(owner:$owner,name:$name){issues(first:100,after:$after,states:$states,labels:$labels){" + _REPO_PAGE + "}}}")

REPO_PRS_Q = ("query($owner:String!,$name:String!,$after:String){"
              "repository(owner:$owner,name:$name){pullRequests(first:100,after:$after,states:[OPEN]){" + _REPO_PAGE + "}}}")

ADD_M = """mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}"""

SET_M = """mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:{singleSelectOptionId:$option}}){projectV2Item{id}}}"""


def load_plan(path: Path, stage: str, raw: Optional[bytes] = None) -> dict:
    """Parse and validate a plan. ``raw`` is the file's bytes when the caller
    already read them to hash, so the plan that runs is the plan hashed."""
    data = json.loads((path.read_bytes() if raw is None else raw).decode("utf-8"))
    scope = data.get("scope", {})
    expected = {"owner": OWNER, "repository": REPOSITORY, "project_number": PROJECT_NUMBER, "project_id": PROJECT_ID}
    for key, value in expected.items():
        if scope.get(key) != value:
            raise MigrationError(f"plan scope {key}={scope.get(key)!r}, expected {value!r}")
    if data.get("stage") != stage:
        raise MigrationError(f"plan is for stage {data.get('stage')!r}, not {stage!r}")
    seen = set()
    for item in data["items"]:
        key = (item["type"], item["number"])
        if key in seen:
            raise MigrationError(f"duplicate plan entry {key}")
        seen.add(key)
        if item["type"] == "PullRequest" and item.get("set", {}).get("Status") == "Inbox":
            raise MigrationError(f"PR #{item['number']} may not be set to Inbox")
        if item["type"] not in ("Issue", "PullRequest"):
            raise MigrationError(f"unsupported item type {item['type']!r}")
        for field in item.get("set", {}):
            if field not in ("Status", "Roadmap Stage"):
                raise MigrationError(f"#{item['number']}: field {field!r} is not writable by this tool")
        if not isinstance(item.get("approved", True), bool):
            raise MigrationError(f"#{item['number']}: approved must be JSON true or false, got {item['approved']!r}")
        _check_guards(item, stage)
    return data


GITHUB_STATES = ("OPEN", "CLOSED", "MERGED")


def _check_guards(item: dict, stage: str) -> None:
    """A row that can write must carry the values its drift checks compare
    against: the GitHub state, and an explicit expected-before entry (JSON
    null allowed) for every field it sets. Every backfill row can add its
    item, so it always needs a state. Held rows, and migrate-existing rows
    that set nothing, are exempt."""
    target = item.get("set") or {}
    if not item.get("approved", True) or not (target or stage == "backfill"):
        return
    key = f"{item['type']}#{item['number']}"
    state = item.get("github_state") or item.get("state")
    if state not in GITHUB_STATES:
        raise MigrationError(f"{key}: plan row needs github_state (or state) set to one of {', '.join(GITHUB_STATES)}, got {state!r}")
    before = item.get("expected_before")
    missing = [f for f in target if not isinstance(before, dict) or f not in before]
    if missing:
        raise MigrationError(f"{key}: plan row needs an explicit expected_before entry for {', '.join(missing)} (null is allowed)")


def _parse_item(node: dict) -> Optional[tuple[tuple[str, int], dict]]:
    content = node.get("content") or {}
    if content.get("__typename") not in ("Issue", "PullRequest"):
        return None
    if content["repository"]["nameWithOwner"] != REPOSITORY:
        return None
    values = {v["field"]["name"]: v["name"] for v in node["fieldValues"]["nodes"] if v and v.get("field")}
    return (content["__typename"], content["number"]), {
        "id": node["id"], "state": content["state"], "archived": node["isArchived"], "values": values}


class Project:
    def __init__(self, gql: GraphQL) -> None:
        self.gql = gql
        data = gql.query(PROJECT_Q, {"owner": OWNER, "number": PROJECT_NUMBER})["user"]["projectV2"]
        if not data or data["id"] != PROJECT_ID or data["number"] != PROJECT_NUMBER or data["owner"]["login"] != OWNER:
            raise MigrationError("project scope check failed (owner / number / node id)")
        self.fields: dict[str, dict] = {}
        for node in data["fields"]["nodes"]:
            if node.get("name"):
                self.fields[node["name"]] = {"id": node["id"], "type": node.get("dataType"),
                                             "options": {o["name"]: o["id"] for o in node.get("options") or []}}
        self.items: dict[tuple[str, int], dict] = {}
        after: Optional[str] = None
        while True:
            page = gql.query(ITEMS_Q, {"owner": OWNER, "number": PROJECT_NUMBER, "after": after})["user"]["projectV2"]["items"]
            for node in page["nodes"]:
                parsed = _parse_item(node)
                if parsed:
                    self.items[parsed[0]] = parsed[1]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]

    def refresh(self, item_id: str) -> Optional[dict]:
        """Re-read one item right before a write or a verification."""
        node = self.gql.query(ITEM_Q, {"id": item_id}).get("node")
        if not node or (node.get("project") or {}).get("id") != PROJECT_ID:
            return None
        parsed = _parse_item(node)
        return parsed[1] if parsed else None

    def missing_requirements(self, plan: dict) -> list[str]:
        problems = []
        for name, options in REQUIRED_OPTIONS.items():
            field = self.fields.get(name)
            if field is None:
                problems.append(f"field {name!r} missing")
                continue
            if field["type"] != "SINGLE_SELECT":
                problems.append(f"field {name!r} is {field['type']}, not single select")
            problems += [f"{name} option {o!r} missing" for o in options if o not in field["options"]]
        for item in plan["items"]:
            for field, value in item.get("set", {}).items():
                if field in self.fields and value not in self.fields[field]["options"]:
                    problems.append(f"{field} option {value!r} missing")
        return sorted(set(problems))


class Checkpoint:
    """Items this tool added and items it completed, for one stage and one
    exact plan file.

    A checkpoint is bound to the sha256 of the plan that produced it, so an
    item completed under one reviewed plan is never reported as done under a
    different one. A checkpoint from another plan, or one that does not name
    its plan, is refused; delete it (or pass a new --checkpoint) to start over.
    """

    def __init__(self, path: Path, stage: str, plan_digest: str) -> None:
        self.path = path
        self.stage = stage
        self.plan_digest = plan_digest
        self.added: set[str] = set()
        self.done: set[str] = set()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("stage") != stage or data.get("project_id", PROJECT_ID) != PROJECT_ID:
                raise MigrationError(f"checkpoint {path} is for stage {data.get('stage')!r}, not {stage!r}")
            if data.get("plan_sha256") != plan_digest:
                raise MigrationError(
                    f"checkpoint {path} was written for plan sha256 {data.get('plan_sha256')!r}, not this plan "
                    f"({plan_digest}); remove it or pass a new --checkpoint to start this plan from the live state")
            self.added = set(data.get("added", []))
            self.done = set(data["done"])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"stage": self.stage, "project_id": PROJECT_ID, "plan_sha256": self.plan_digest,
                                   "added": sorted(self.added), "done": sorted(self.done)}, indent=1))
        tmp.replace(self.path)


def _state_ok(item: dict, live_state: str) -> bool:
    want = item.get("github_state") or item.get("state")
    # Exact: MERGED is its own plan state, so a PR reviewed as CLOSED that was
    # reopened and merged since is drift, not a match.
    return want is None or want == live_state


def _expected(item: dict, field: str, stage: str) -> set:
    allowed = {item.get("expected_before", {}).get(field)}
    if stage == "backfill" and field == "Status":
        allowed.add(NATIVE_ENTRY_STATUS)
    return allowed


def _late_native_inbox(key: str, item: dict, values: dict, checkpoint: Checkpoint) -> bool:
    target = item.get("set", {}).get("Status")
    return (key in checkpoint.added and target is not None and target != NATIVE_ENTRY_STATUS
            and values.get("Status") == NATIVE_ENTRY_STATUS)


def _plan_checkpointed(rec: dict, item: dict, live: Optional[dict], checkpoint: Checkpoint) -> dict:
    if live is not None and _late_native_inbox(rec["item"], item, live["values"], checkpoint):
        rec.update(outcome="would-change", add=False, before={"Status": NATIVE_ENTRY_STATUS},
                   writes=[("Status", item["set"]["Status"])], repair=True,
                   detail="late native Inbox replaced the Status this tool set")
        return rec
    rec.update(outcome="checkpointed", detail="already completed in an earlier run")
    return rec


def _planned_writes(item: dict, before: dict, stage: str) -> tuple[list, list]:
    writes, drift = [], []
    for field, target in item.get("set", {}).items():
        current = before.get(field)
        if current == target:
            continue
        if current not in _expected(item, field, stage):
            drift.append(f"{field}: current {current!r}, expected {item.get('expected_before', {}).get(field)!r}")
            continue
        writes.append((field, target))
    return writes, drift


def plan_item(project: Project, item: dict, stage: str, checkpoint: Checkpoint) -> dict:
    key = f"{item['type']}#{item['number']}"
    rec: dict[str, Any] = {"item": key, "title": item.get("title", ""), "set": item.get("set", {}), "writes": [], "outcome": None, "detail": ""}
    if not item.get("approved", True):
        rec.update(outcome="held", detail="approved=false in plan")
        return rec
    live = project.items.get((item["type"], item["number"]))
    if key in checkpoint.done:
        return _plan_checkpointed(rec, item, live, checkpoint)
    if live is None and stage == "migrate-existing":
        rec.update(outcome="drift", detail="item is no longer on the project")
        return rec
    if live is not None and live["archived"]:
        rec.update(outcome="drift", detail="item is archived; not touched")
        return rec
    rec["add"] = live is None
    before = {} if live is None else live["values"]
    rec["before"] = {f: before.get(f) for f in item.get("set", {})}
    if live is not None and not _state_ok(item, live["state"]):
        rec.update(outcome="drift", detail=f"GitHub state is {live['state']}, plan expects {item.get('github_state') or item.get('state')}")
        return rec
    rec["writes"], drift = _planned_writes(item, before, stage)
    if drift:
        rec.update(outcome="drift", detail="; ".join(drift), writes=[])
        return rec
    if not rec["writes"] and not rec["add"]:
        rec["outcome"] = "unchanged"
        return rec
    missing = [f for f, _ in rec["writes"] if f not in project.fields]
    rec["outcome"] = "would-change"
    if missing:
        rec["detail"] = "field not created yet: " + ", ".join(missing)
    return rec


def _set(project: Project, item_id: str, field: str, target: str) -> None:
    f = project.fields[field]
    project.gql.mutate("updateProjectV2ItemFieldValue", SET_M,
                       {"project": PROJECT_ID, "item": item_id, "field": f["id"], "option": f["options"][target]})


def _record_added(key: str, checkpoint: Checkpoint) -> None:
    """Record a backfill item as added before its add is sent: if the add
    commits but its response is lost, a rerun still treats the item as added,
    so it is verified after settling and a late native Inbox on it stays
    repairable. An item native auto-add put there first is covered the same way."""
    if key not in checkpoint.added:
        checkpoint.added.add(key)
        checkpoint.save()


def apply_item(project: Project, item: dict, rec: dict, stage: str, checkpoint: Checkpoint) -> None:
    """Write one item. Every field is re-checked against a fresh read first."""
    gql = project.gql
    key = rec["item"]
    live = project.items.get((item["type"], item["number"]))
    if stage == "backfill":
        _record_added(key, checkpoint)
    if rec.get("add"):
        owner, name = REPOSITORY.split("/")
        content = gql.query(CONTENT_Q, {"owner": owner, "name": name, "number": item["number"]})["repository"]["issueOrPullRequest"]
        if not content or content["__typename"] != item["type"]:
            raise MigrationError(f"{key}: content type mismatch")
        if not _state_ok(item, content["state"]):
            rec.update(outcome="drift", detail=f"GitHub state is {content['state']}", writes=[])
            return
        # Returns the existing item if native auto-add got there first.
        item_id = gql.mutate("addProjectV2ItemById", ADD_M, {"project": PROJECT_ID, "content": content["id"]})["addProjectV2ItemById"]["item"]["id"]
    else:
        item_id = live["id"]
    rec["item_id"] = item_id
    written = []
    for field, target in item.get("set", {}).items():
        fresh = project.refresh(item_id)
        reason = _unsafe_to_touch(item, fresh)
        if reason or fresh is None:
            rec.update(outcome="drift", detail=f"{reason} during the run; not touched", writes=written)
            return
        current = fresh["values"].get(field)
        if current == target:
            continue
        allowed = _expected(item, field, stage)
        if rec.get("repair"):
            allowed = {NATIVE_ENTRY_STATUS}
        if current not in allowed:
            rec.update(outcome="drift", detail=f"{field} changed to {current!r} during the run; not overwritten", writes=written)
            return
        _set(project, item_id, field, target)
        written.append((field, target))
        # The write is not atomic with the check above: an issue closed (and
        # moved to Done by "Item closed") in between would now carry this
        # target. Re-read and report it so the apply fails for a person to fix.
        after = _unsafe_to_touch(item, project.refresh(item_id))
        if after:
            rec.update(outcome="drift", writes=written,
                       detail=f"{after} while {field} was written; check this item by hand")
            return
    rec["writes"] = written
    rec["outcome"] = "changed" if written or rec.get("add") else "unchanged"
    if rec.get("repair"):
        rec["outcome"] = "repaired" if written else "unchanged"


def _unsafe_to_touch(item: dict, fresh: Optional[dict]) -> Optional[str]:
    """Why an item may not be written now, or None: the same guard as apply_item."""
    if fresh is None:
        return "item was removed from the project"
    if fresh["archived"]:
        return "item was archived"
    if not _state_ok(item, fresh["state"]):
        return f"GitHub state changed to {fresh['state']}"
    return None


def _awaits_verify(rec: dict, checkpoint: Checkpoint) -> bool:
    """An item this tool added that has not verified yet: written now, or
    left unchanged by a rerun after an earlier run stopped before verifying."""
    return (rec["outcome"] in ("changed", "repaired", "unchanged")
            and rec["item"] in checkpoint.added and rec["item"] not in checkpoint.done)


def verify_added(project: Project, plan_items: dict, records: list, checkpoint: Checkpoint) -> None:
    """Undo a late native "Item added" Inbox once; report anything else.

    The repair is a write like any other, so the item's presence, archive
    flag and GitHub state are re-checked right before it; if any changed
    during the settle wait, nothing is written and the item is reported.
    An added item is checkpointed as done only here, once it verifies, so a
    run that stops before or during verification re-plans it from live data.
    """
    for rec in records:
        if not _awaits_verify(rec, checkpoint):
            continue
        item = plan_items[rec["item"]]
        if "item_id" not in rec:   # left unchanged by this run, so never written
            rec["item_id"] = project.items[(item["type"], item["number"])]["id"]
        fresh = project.refresh(rec["item_id"])
        reason = _unsafe_to_touch(item, fresh)
        if reason:
            rec.update(outcome="verify-failed", detail=f"after settling: {reason}; not touched")
            continue
        values = fresh["values"] if fresh else {}
        if _late_native_inbox(rec["item"], item, values, checkpoint):
            _set(project, rec["item_id"], "Status", item["set"]["Status"])
            fresh = project.refresh(rec["item_id"])
            after = _unsafe_to_touch(item, fresh)
            if after:   # changed between the check and the repair write
                rec.update(outcome="verify-failed", detail=f"after settling: {after} while the repair was written; check this item by hand")
                continue
            values = fresh["values"] if fresh else {}
            rec["detail"] = "late native Inbox written over"
        wrong = {f: values.get(f) for f, t in item.get("set", {}).items() if values.get(f) != t}
        if wrong:
            rec.update(outcome="verify-failed", detail=f"after settling: {wrong}; left as is")
            continue
        checkpoint.done.add(rec["item"])


def plan_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_RUN_ERRORS = (MigrationError, urllib.error.URLError, OSError, KeyError, TypeError)


def _run_items(project: Project, plan: dict, stage: str, ck: Checkpoint, apply: bool) -> tuple[list[dict], bool]:
    """Plan every item and, with --apply, write it; stop writing at the first failure."""
    records: list[dict] = []
    failed = False
    for item in plan["items"]:
        rec = plan_item(project, item, stage, ck)
        records.append(rec)
        if failed:
            rec.update(outcome="not-run", detail="stopped after an earlier failure", writes=[])
            continue
        if not (apply and rec["outcome"] == "would-change"):
            continue
        try:
            apply_item(project, item, rec, stage, ck)
        except _RUN_ERRORS as exc:
            rec.update(outcome="failed", detail=f"{type(exc).__name__}: {exc}")
            failed = True
            continue
        try:
            _checkpoint_written(rec, ck)
        except OSError as exc:   # the write landed; only the bookkeeping failed
            rec.update(outcome="failed", detail=f"written, but the checkpoint could not be saved: {type(exc).__name__}: {exc}")
            failed = True
    return records, failed


def _checkpoint_written(rec: dict, ck: Checkpoint) -> None:
    if rec["outcome"] not in ("changed", "repaired"):
        return
    # An added item (a first add, or a rerun's repair of one already done)
    # is done only once verify_added confirms it.
    if rec["item"] in ck.added:
        ck.done.discard(rec["item"])
    else:
        ck.done.add(rec["item"])
    ck.save()


def _not_in_plan(project: Project, plan: dict) -> list[dict]:
    planned = {(i["type"], i["number"]) for i in plan["items"]}
    return [{"item": f"{kind}#{number}", "title": "", "set": {}, "writes": [], "outcome": "not-in-plan",
             "detail": "on Project #3 but not in the reviewed plan; not touched"}
            for (kind, number) in sorted(set(project.items) - planned)]


def _write_report(report: dict, report_dir: Path, out) -> None:
    stage, digest, counts, missing = report["stage"], report["plan_sha256"], report["counts"], report["requirements_missing"]
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{stage}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=list))
    lines = [f"# Project #3 {stage} ({report['mode']})", "", f"Plan sha256: `{digest}`", "",
             "Missing requirements: " + (", ".join(missing) or "none"), "",
             "| Outcome | Count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "| Item | Outcome | Add | Before | After | Detail |", "|---|---|---|---|---|---|"]
    for r in report["items"]:
        lines.append(f"| {r['item']} | {r['outcome']} | {'yes' if r.get('add') else ''} | {r.get('before', '')} | {dict(r['writes']) or ''} | {r['detail']} |")
    (report_dir / f"{stage}.md").write_text("\n".join(lines) + "\n")
    print(f"{stage} ({report['mode']}): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), file=out)
    print(f"plan sha256: {digest}", file=out)
    if missing:
        print("missing requirements: " + "; ".join(missing), file=out)


def _run_ok(apply: bool, failed: bool, counts: dict[str, int]) -> bool:
    """A run is ok only if nothing failed and, with --apply, the reviewed plan
    was applied in full: an item skipped as drift leaves the plan incomplete.
    held, unchanged, checkpointed and not-in-plan are intended and stay ok."""
    if failed or counts.get("failed") or counts.get("verify-failed"):
        return False
    return not (apply and counts.get("drift"))


def run(stage: str, plan_path: Path, *, apply: bool, transport: Transport, checkpoint: Path, report_dir: Path,
        out=sys.stdout, settle_seconds: float = DEFAULT_SETTLE_SECONDS, sleep: Callable[[float], None] = time.sleep,
        expected_sha256: Optional[str] = None) -> dict:
    raw = plan_path.read_bytes()   # read once: these exact bytes are hashed and run
    digest = hashlib.sha256(raw).hexdigest()
    if apply and expected_sha256 != digest:
        raise MigrationError(f"refusing --apply: --plan-sha256 does not match the plan file ({digest})")
    plan = load_plan(plan_path, stage, raw)
    gql = GraphQL(transport, allow_mutations=apply)
    project = Project(gql)
    missing = project.missing_requirements(plan)
    if apply and missing:
        raise MigrationError("refusing --apply: " + "; ".join(missing))
    ck = Checkpoint(checkpoint, stage, digest)
    records, failed = _run_items(project, plan, stage, ck, apply)
    if stage == "migrate-existing":
        records += _not_in_plan(project, plan)
    if apply and not failed and any(_awaits_verify(r, ck) for r in records):
        sleep(settle_seconds)
        try:
            verify_added(project, {f"{i['type']}#{i['number']}": i for i in plan["items"]}, records, ck)
            ck.save()
        except _RUN_ERRORS as exc:
            failed = True
            print(f"verification or checkpoint save failed: {type(exc).__name__}: {exc}", file=out)
    counts: dict[str, int] = {}
    for r in records:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    report = {"stage": stage, "mode": "apply" if apply else "dry-run", "plan_sha256": digest, "requirements_missing": missing, "counts": counts,
              "ok": _run_ok(apply, failed, counts), "items": records}
    _write_report(report, report_dir, out)
    return report


def _repo_pages(gql: GraphQL, query: str, variables: dict, connection: str) -> list[dict]:
    nodes: list[dict] = []
    after: Optional[str] = None
    while True:
        page = gql.query(query, dict(variables, after=after))["repository"][connection]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        after = page["pageInfo"]["endCursor"]


def plan_backfill(transport: Transport, *, generated_at: str) -> dict:
    """Read-only: propose adding what Project #3 is missing, from live data.

    Open issues get Inbox, open PRs get no Status (project-sync owns PR
    Status), and closed issues labelled ``research`` get Done, because
    "Item closed" does not fire when a closed item is added.
    """
    gql = GraphQL(transport, allow_mutations=False)
    project = Project(gql)
    owner, name = REPOSITORY.split("/")
    base = {"owner": owner, "name": name}
    on_project = set(project.items)
    candidates = [
        ("Issue", node, "open-issue", {"Status": "Inbox"}, "Open issue missing from Project #3.")
        for node in _repo_pages(gql, REPO_ISSUES_Q, dict(base, states=["OPEN"], labels=None), "issues")
    ] + [
        ("PullRequest", node, "open-pr", {}, "Open PR missing from Project #3; project-sync sets its Status.")
        for node in _repo_pages(gql, REPO_PRS_Q, base, "pullRequests")
    ] + [
        ("Issue", node, "closed-research", {"Status": "Done"}, "Closed research issue for Research History; Done is set explicitly.")
        for node in _repo_pages(gql, REPO_ISSUES_Q, dict(base, states=["CLOSED"], labels=[RESEARCH_LABEL]), "issues")
    ]
    items = []
    for kind, node, category, target, reason in candidates:
        if (kind, node["number"]) in on_project:
            continue
        items.append({"number": node["number"], "type": kind, "title": node["title"], "state": node["state"],
                      "labels": sorted(label["name"] for label in node["labels"]["nodes"]),
                      "expected_before": {field: None for field in target}, "set": target,
                      "category": category, "reason": reason})
    items.sort(key=lambda i: (i["category"], i["number"]))
    counts: dict[str, int] = {}
    for item in items:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {"schema": 1, "scope": {"owner": OWNER, "repository": REPOSITORY, "project_number": PROJECT_NUMBER, "project_id": PROJECT_ID},
            "stage": "backfill", "generated_at": generated_at, "source": "live read-only snapshot", "counts": counts, "items": items}


def _allowed_roots() -> tuple:
    return tuple({Path(tempfile.gettempdir()).resolve(), Path.home().resolve()})


def _confined(path: Path, what: str, suffix: Optional[str] = None) -> Path:
    """Keep plans, reports and checkpoints in the home or temp directory and
    out of this repository."""
    resolved = path.expanduser().resolve()
    roots = _allowed_roots()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise MigrationError(f"{what} must be under {' or '.join(str(r) for r in roots)}: {resolved}")
    if resolved == _REPO or resolved.is_relative_to(_REPO):
        raise MigrationError(f"{what} must be outside this repository (plans and reports are never checked in): {resolved}")
    if suffix and resolved.suffix != suffix:
        raise MigrationError(f"{what} must be a {suffix} file: {resolved}")
    return resolved


def _token_transport(env: dict) -> Optional[Transport]:
    token = env.get("PROJECT3_MIGRATION_TOKEN") or env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    return http_transport(token) if token else None


def _checked_paths(args: argparse.Namespace) -> dict[str, Path]:
    """Validate the command's options and confine every path it will touch."""
    if args.command == "plan-backfill":
        if args.apply or args.plan:
            raise MigrationError("plan-backfill only reads; it takes --out, not --plan or --apply")
        if not args.out:
            raise MigrationError("plan-backfill needs --out")
        out_path = _confined(args.out, "--out", ".json")
        if out_path.exists():
            raise MigrationError(f"{out_path} exists; a reviewed plan is never overwritten")
        return {"out": out_path}
    if not args.plan:
        raise MigrationError(f"{args.command} needs --plan (the reviewed plan file)")
    if args.apply and not args.plan_sha256:
        raise MigrationError("--apply needs --plan-sha256 from the reviewed dry-run")
    report_dir = _confined(args.report_dir, "--report-dir")
    return {"plan": _confined(args.plan, "--plan", ".json"), "report_dir": report_dir,
            "checkpoint": _confined(args.checkpoint or report_dir / f"{args.command}.checkpoint.json", "--checkpoint", ".json")}


def _write_backfill_plan(transport: Transport, out_path: Path) -> None:
    plan = plan_backfill(transport, generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(plan, indent=1, ensure_ascii=False) + "\n")
    print(f"plan-backfill: {plan['counts']} -> {out_path}")
    print(f"plan sha256: {plan_sha256(out_path)}")


def main(argv: Optional[list[str]] = None, env: Optional[dict] = None, transport: Optional[Transport] = None) -> int:
    env = dict(os.environ) if env is None else env
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--plan", type=Path, help="the reviewed plan to run (migrate-existing, backfill)")
    parser.add_argument("--out", type=Path, help="where plan-backfill writes the new plan; never overwritten")
    parser.add_argument("--apply", action="store_true", help="perform writes (default: dry-run)")
    parser.add_argument("--plan-sha256", help="required with --apply: the sha256 of the reviewed plan file")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR,
                        help="outside the repository, under the home or temp directory (default: %(default)s)")
    parser.add_argument("--settle-seconds", type=float, default=DEFAULT_SETTLE_SECONDS,
                        help="wait before re-reading added items for a late native Inbox (default: %(default)s)")
    args = parser.parse_args(argv)
    try:
        paths = _checked_paths(args)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    transport = transport or _token_transport(env)
    if transport is None:
        print("error: set PROJECT3_MIGRATION_TOKEN (or GH_TOKEN)", file=sys.stderr)
        return 2
    try:
        if args.command == "plan-backfill":
            _write_backfill_plan(transport, paths["out"])
            return 0
        report = run(args.command, paths["plan"], apply=args.apply, transport=transport, checkpoint=paths["checkpoint"],
                     report_dir=paths["report_dir"], settle_seconds=args.settle_seconds, expected_sha256=args.plan_sha256)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
