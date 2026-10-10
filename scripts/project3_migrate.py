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
  matches. Otherwise the item is skipped and reported. Fields the manifest
  does not list are never touched. Items with ``approved: false`` are held.
* PR items are never set to Inbox (manifest validation).
* A checkpoint file records completed items so reruns are idempotent.

Token: ``PROJECT3_MIGRATION_TOKEN`` or ``GH_TOKEN`` / ``GITHUB_TOKEN``. Never
printed. Needs the classic ``project`` scope for --apply (read for dry-run).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https URL
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

ITEMS_Q = """query($owner:String!,$number:Int!,$after:String){user(login:$owner){projectV2(number:$number){items(first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{id isArchived
fieldValues(first:40){nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}}}
content{__typename ... on Issue{number state repository{nameWithOwner}} ... on PullRequest{number state repository{nameWithOwner}}}}}}}}"""

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
                content = node.get("content") or {}
                if content.get("__typename") not in ("Issue", "PullRequest"):
                    continue
                if content["repository"]["nameWithOwner"] != REPOSITORY:
                    continue
                values = {v["field"]["name"]: v["name"] for v in node["fieldValues"]["nodes"] if v and v.get("field")}
                self.items[(content["__typename"], content["number"])] = {
                    "id": node["id"], "state": content["state"], "archived": node["isArchived"], "values": values}
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]

    def missing_requirements(self, manifest: dict) -> list[str]:
        problems = [f"field {f!r} missing" for f in REQUIRED_FIELDS if f not in self.fields]
        status_opts = self.fields.get("Status", {}).get("options", {})
        problems += [f"Status option {o!r} missing" for o in REQUIRED_STATUS_OPTIONS if o not in status_opts]
        for item in manifest["items"]:
            for field, value in item.get("set", {}).items():
                if field in self.fields and value not in self.fields[field]["options"]:
                    problems.append(f"{field} option {value!r} missing")
        return sorted(set(problems))


def _state_ok(item: dict, live_state: str) -> bool:
    want = item.get("github_state") or item.get("state")
    return want is None or want == live_state or (want == "CLOSED" and live_state == "MERGED")


def plan_item(project: Project, item: dict, stage: str, done: set) -> dict:
    key = f"{item['type']}#{item['number']}"
    rec: dict[str, Any] = {"item": key, "title": item.get("title", ""), "set": item.get("set", {}), "writes": [], "outcome": None, "detail": ""}
    if key in done:
        rec.update(outcome="checkpointed", detail="already completed in an earlier run")
        return rec
    if not item.get("approved", True):
        rec.update(outcome="held", detail="approved=false in manifest")
        return rec
    live = project.items.get((item["type"], item["number"]))
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
    expected = item.get("expected_before", {})
    drift = []
    for field, target in item.get("set", {}).items():
        current = before.get(field)
        if current == target:
            continue
        if current != expected.get(field):
            drift.append(f"{field}: current {current!r}, expected {expected.get(field)!r}")
            continue
        rec["writes"].append((field, target))
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


def apply_item(project: Project, item: dict, rec: dict) -> None:
    gql = project.gql
    item_id = None
    live = project.items.get((item["type"], item["number"]))
    if rec.get("add"):
        owner, name = REPOSITORY.split("/")
        content = gql.query(CONTENT_Q, {"owner": owner, "name": name, "number": item["number"]})["repository"]["issueOrPullRequest"]
        if not content or content["__typename"] != item["type"]:
            raise MigrationError(f"{rec['item']}: content type mismatch")
        if not _state_ok(item, content["state"]):
            rec.update(outcome="drift", detail=f"GitHub state is {content['state']}", writes=[])
            return
        item_id = gql.mutate("addProjectV2ItemById", ADD_M, {"project": PROJECT_ID, "content": content["id"]})["addProjectV2ItemById"]["item"]["id"]
    else:
        item_id = live["id"]
    for field, target in rec["writes"]:
        f = project.fields[field]
        gql.mutate("updateProjectV2ItemFieldValue", SET_M, {"project": PROJECT_ID, "item": item_id, "field": f["id"], "option": f["options"][target]})
    rec["outcome"] = "changed"


def run(stage: str, manifest_path: Path, *, apply: bool, transport: Transport, checkpoint: Path, report_dir: Path, out=sys.stdout) -> dict:
    manifest = load_manifest(manifest_path, stage)
    gql = GraphQL(transport, allow_mutations=apply)
    project = Project(gql)
    missing = project.missing_requirements(manifest)
    if apply and missing:
        raise MigrationError("refusing --apply: " + "; ".join(missing))
    done = set(json.loads(checkpoint.read_text())["done"]) if checkpoint.exists() else set()
    records = []
    for item in manifest["items"]:
        rec = plan_item(project, item, stage, done)
        if apply and rec["outcome"] == "would-change":
            apply_item(project, item, rec)
            if rec["outcome"] == "changed":
                done.add(rec["item"])
                checkpoint.write_text(json.dumps({"stage": stage, "done": sorted(done)}, indent=1))
        records.append(rec)
    counts: dict[str, int] = {}
    for r in records:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    report = {"stage": stage, "mode": "apply" if apply else "dry-run", "requirements_missing": missing, "counts": counts, "items": records}
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
        run(args.stage, manifest, apply=args.apply, transport=transport, checkpoint=checkpoint, report_dir=args.report_dir)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
