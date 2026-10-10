#!/usr/bin/env python3
"""One-off Project #3 migration and backfill, dry-run by default (#441).

Stages (approved and run independently):

  migrate-existing  set Status / Roadmap Stage on the original 44 items
  backfill          add missing issues and PRs, then set the listed Status

Safety contract (enforced here, not only documented):

* Dry-run unless ``--apply`` is given. Dry-run sends queries only.
* The only mutations this file can send are ``addProjectV2ItemById`` and
  ``updateProjectV2ItemFieldValue`` (single-select). Anything else is refused
  by ``GraphQL.mutate``. No deletes, clears, archives, closes, merges.
* Scope: owner Fooftilly, project 3, node id PVT_kwHOAsc2_s4BkAo3, content
  from Fooftilly/PRKS only. The manifest must name the same scope.
* Field and option IDs are resolved by name at run time. ``--apply`` refuses
  to run unless the Roadmap Stage and Execution fields and the Backlog Status
  option exist, and every option the manifest uses exists.
* Drift: a field is written only when its current value equals the
  manifest's ``expected_before`` value (None = empty) and the issue/PR state
  matches. The item is re-read immediately before every write, not only in
  the initial snapshot, so a change made while the run is in progress is
  never overwritten. In the backfill stage Inbox also counts as an expected
  Status, because the native "Item added" workflow sets it on any item that
  auto-add or this tool adds. Otherwise the item is skipped and reported.
  Fields the manifest does not list are never touched. Items with
  ``approved: false`` are held.
* PR items are never set to Inbox (manifest validation).
* After the writes, every item this run added is re-read once the native
  workflows have had ``--settle-seconds`` to run. A late "Item added" Inbox
  that replaced the written Status is written over once; any other change
  is reported as ``verify-failed`` and left alone.
* A checkpoint file records added and completed items, so reruns are
  idempotent and a completed item is not rewritten after a person changes
  it. A rerun only repairs a late native Inbox on an item this tool added.
  A failure stops the run, writes the report and checkpoint, and exits 1.

Token: ``PROJECT3_MIGRATION_TOKEN`` or ``GH_TOKEN`` / ``GITHUB_TOKEN``. Never
printed. Needs the classic ``project`` scope for --apply (read for dry-run).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

OWNER = "Fooftilly"
REPOSITORY = "Fooftilly/PRKS"
PROJECT_NUMBER = 3
PROJECT_ID = "PVT_kwHOAsc2_s4BkAo3"
REQUIRED_FIELDS = ("Status", "Roadmap Stage", "Execution")
REQUIRED_STATUS_OPTIONS = ("Backlog",)
ALLOWED_MUTATIONS = ("addProjectV2ItemById", "updateProjectV2ItemFieldValue")
STAGES = ("migrate-existing", "backfill")
# The Status the native "Item added" workflow gives a newly added issue (§5).
NATIVE_ENTRY_STATUS = "Inbox"
DEFAULT_SETTLE_SECONDS = 30.0
_DOCS = Path(__file__).resolve().parents[1] / "docs" / "agent-workflows" / "project-3-migration"
DEFAULT_MANIFEST = {"migrate-existing": _DOCS / "existing-items.json", "backfill": _DOCS / "backfill.json"}


class MigrationError(Exception):
    pass


Transport = Callable[[str, dict], dict]


def http_transport(token: str) -> Transport:
    def send(query: str, variables: dict) -> dict:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310  # nosec B310 - fixed https URL
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

ADD_M = """mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}"""

SET_M = """mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:{singleSelectOptionId:$option}}){projectV2Item{id}}}"""


def load_manifest(path: Path, stage: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    scope = data.get("scope", {})
    expected = {"owner": OWNER, "repository": REPOSITORY, "project_number": PROJECT_NUMBER, "project_id": PROJECT_ID}
    for key, value in expected.items():
        if scope.get(key) != value:
            raise MigrationError(f"manifest scope {key}={scope.get(key)!r}, expected {value!r}")
    if data.get("stage") != stage:
        raise MigrationError(f"manifest is for stage {data.get('stage')!r}, not {stage!r}")
    seen = set()
    for item in data["items"]:
        key = (item["type"], item["number"])
        if key in seen:
            raise MigrationError(f"duplicate manifest entry {key}")
        seen.add(key)
        if item["type"] == "PullRequest" and item.get("set", {}).get("Status") == "Inbox":
            raise MigrationError(f"PR #{item['number']} may not be set to Inbox")
        if item["type"] not in ("Issue", "PullRequest"):
            raise MigrationError(f"unsupported item type {item['type']!r}")
        for field in item.get("set", {}):
            if field not in ("Status", "Roadmap Stage"):
                raise MigrationError(f"#{item['number']}: field {field!r} is not writable by this tool")
    return data


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
                self.fields[node["name"]] = {"id": node["id"], "options": {o["name"]: o["id"] for o in node.get("options") or []}}
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

    def missing_requirements(self, manifest: dict) -> list[str]:
        problems = [f"field {f!r} missing" for f in REQUIRED_FIELDS if f not in self.fields]
        status_opts = self.fields.get("Status", {}).get("options", {})
        problems += [f"Status option {o!r} missing" for o in REQUIRED_STATUS_OPTIONS if o not in status_opts]
        for item in manifest["items"]:
            for field, value in item.get("set", {}).items():
                if field in self.fields and value not in self.fields[field]["options"]:
                    problems.append(f"{field} option {value!r} missing")
        return sorted(set(problems))


class Checkpoint:
    """Items this tool added and items it completed, per stage."""

    def __init__(self, path: Path, stage: str) -> None:
        self.path = path
        self.stage = stage
        self.added: set[str] = set()
        self.done: set[str] = set()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("stage") != stage or data.get("project_id", PROJECT_ID) != PROJECT_ID:
                raise MigrationError(f"checkpoint {path} is for stage {data.get('stage')!r}, not {stage!r}")
            self.added = set(data.get("added", []))
            self.done = set(data["done"])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"stage": self.stage, "project_id": PROJECT_ID,
                                   "added": sorted(self.added), "done": sorted(self.done)}, indent=1))
        tmp.replace(self.path)


def _state_ok(item: dict, live_state: str) -> bool:
    want = item.get("github_state") or item.get("state")
    return want is None or want == live_state or (want == "CLOSED" and live_state == "MERGED")


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
        rec.update(outcome="held", detail="approved=false in manifest")
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
        rec.update(outcome="drift", detail=f"GitHub state is {live['state']}, manifest expects {item.get('github_state') or item.get('state')}")
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


def apply_item(project: Project, item: dict, rec: dict, stage: str, checkpoint: Checkpoint) -> None:
    """Write one item. Every field is re-checked against a fresh read first."""
    gql = project.gql
    key = rec["item"]
    live = project.items.get((item["type"], item["number"]))
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
        checkpoint.added.add(key)
        checkpoint.save()
    else:
        item_id = live["id"]
    rec["item_id"] = item_id
    written = []
    for field, target in item.get("set", {}).items():
        fresh = project.refresh(item_id)
        if fresh is None or fresh["archived"]:
            rec.update(outcome="drift", detail="item was removed or archived during the run", writes=written)
            return
        if not _state_ok(item, fresh["state"]):
            rec.update(outcome="drift", detail=f"GitHub state changed to {fresh['state']} during the run", writes=written)
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
    rec["writes"] = written
    rec["outcome"] = "changed" if written or rec.get("add") else "unchanged"
    if rec.get("repair"):
        rec["outcome"] = "repaired" if written else "unchanged"


def verify_added(project: Project, manifest_items: dict, records: list, checkpoint: Checkpoint) -> None:
    """Undo a late native "Item added" Inbox once; report anything else."""
    for rec in records:
        if rec["outcome"] not in ("changed", "repaired") or rec["item"] not in checkpoint.added:
            continue
        item = manifest_items[rec["item"]]
        fresh = project.refresh(rec["item_id"])
        values = fresh["values"] if fresh else {}
        if _late_native_inbox(rec["item"], item, values, checkpoint):
            _set(project, rec["item_id"], "Status", item["set"]["Status"])
            fresh = project.refresh(rec["item_id"])
            values = fresh["values"] if fresh else {}
            rec["detail"] = "late native Inbox written over"
        wrong = {f: values.get(f) for f, t in item.get("set", {}).items() if values.get(f) != t}
        if wrong:
            rec.update(outcome="verify-failed", detail=f"after settling: {wrong}; left as is")
            checkpoint.done.discard(rec["item"])


def run(stage: str, manifest_path: Path, *, apply: bool, transport: Transport, checkpoint: Path, report_dir: Path,
        out=sys.stdout, settle_seconds: float = DEFAULT_SETTLE_SECONDS, sleep: Callable[[float], None] = time.sleep) -> dict:
    manifest = load_manifest(manifest_path, stage)
    gql = GraphQL(transport, allow_mutations=apply)
    project = Project(gql)
    missing = project.missing_requirements(manifest)
    if apply and missing:
        raise MigrationError("refusing --apply: " + "; ".join(missing))
    ck = Checkpoint(checkpoint, stage)
    by_key = {f"{i['type']}#{i['number']}": i for i in manifest["items"]}
    records: list[dict] = []
    failed = False
    for item in manifest["items"]:
        rec = plan_item(project, item, stage, ck)
        records.append(rec)
        if failed:
            rec.update(outcome="not-run", detail="stopped after an earlier failure", writes=[])
            continue
        if apply and rec["outcome"] == "would-change":
            try:
                apply_item(project, item, rec, stage, ck)
            except (MigrationError, urllib.error.URLError, OSError, KeyError, TypeError) as exc:
                rec.update(outcome="failed", detail=f"{type(exc).__name__}: {exc}")
                failed = True
                continue
            if rec["outcome"] in ("changed", "repaired"):
                ck.done.add(rec["item"])
                ck.save()
    if apply and not failed and any(r["outcome"] in ("changed", "repaired") and r["item"] in ck.added for r in records):
        sleep(settle_seconds)
        try:
            verify_added(project, by_key, records, ck)
        except (MigrationError, urllib.error.URLError, OSError, KeyError, TypeError) as exc:
            failed = True
            print(f"verification failed: {type(exc).__name__}: {exc}", file=out)
        ck.save()
    counts: dict[str, int] = {}
    for r in records:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    report = {"stage": stage, "mode": "apply" if apply else "dry-run", "requirements_missing": missing, "counts": counts,
              "ok": not failed and "failed" not in counts and "verify-failed" not in counts, "items": records}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{stage}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=list))
    lines = [f"# Project #3 {stage} ({report['mode']})", "", "Missing requirements: " + (", ".join(missing) or "none"), "",
             "| Outcome | Count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "| Item | Outcome | Add | Before | After | Detail |", "|---|---|---|---|---|---|"]
    for r in records:
        lines.append(f"| {r['item']} | {r['outcome']} | {'yes' if r.get('add') else ''} | {r.get('before', '')} | {dict(r['writes']) or ''} | {r['detail']} |")
    (report_dir / f"{stage}.md").write_text("\n".join(lines) + "\n")
    print(f"{stage} ({report['mode']}): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), file=out)
    if missing:
        print("missing requirements: " + "; ".join(missing), file=out)
    return report


def main(argv: Optional[list[str]] = None, env: Optional[dict] = None, transport: Optional[Transport] = None) -> int:
    env = dict(os.environ) if env is None else env
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--apply", action="store_true", help="perform writes (default: dry-run)")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--report-dir", type=Path, default=Path("project3-migration-report"))
    parser.add_argument("--settle-seconds", type=float, default=DEFAULT_SETTLE_SECONDS,
                        help="wait before re-reading added items for a late native Inbox (default: %(default)s)")
    args = parser.parse_args(argv)
    manifest = args.manifest or DEFAULT_MANIFEST[args.stage]
    checkpoint = args.checkpoint or args.report_dir / f"{args.stage}.checkpoint.json"
    if transport is None:
        token = env.get("PROJECT3_MIGRATION_TOKEN") or env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
        if not token:
            print("error: set PROJECT3_MIGRATION_TOKEN (or GH_TOKEN)", file=sys.stderr)
            return 2
        transport = http_transport(token)
    try:
        report = run(args.stage, manifest, apply=args.apply, transport=transport, checkpoint=checkpoint,
                     report_dir=args.report_dir, settle_seconds=args.settle_seconds)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
