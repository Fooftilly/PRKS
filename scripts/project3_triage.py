#!/usr/bin/env python3
"""Reviewed, drift-guarded Project #3 issue triage. Never modifies PRs.

propose: read live values and write a plan (all rows unapproved).
check:   read-only preflight and print the exact plan SHA-256.
apply:   update only approved rows, requiring that exact reviewed SHA-256.

See docs/agent-workflows/project-3.md. No writes without "apply".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

import project3_migrate as pm

FIELDS = ("Agent", "Status", "Execution")
SCOPE = {"owner": pm.OWNER, "repository": pm.REPOSITORY,
         "project_number": pm.PROJECT_NUMBER, "project_id": pm.PROJECT_ID}
SCHEMA = "prks-project3-triage/v1"
STATUS = {"Inbox", "Backlog", "Ready", "In Progress"}
AGENTS = {"Cursor Cloud", "Claude Code", "ChatGPT/Codex", "Unassigned", "Manual"}
EXECUTION = {"Human", "Agent", "Mixed"}
MAX_ITEMS = 25
TRIAGE_DIR = Path.home() / "prks-project3-triage"
AGENT_ALIASES = {
    "cursor": "Cursor Cloud", "cursor cloud": "Cursor Cloud",
    "claude": "Claude Code", "claude code": "Claude Code",
    "codex": "ChatGPT/Codex", "chatgpt": "ChatGPT/Codex",
    "chatgpt codex": "ChatGPT/Codex",
    "manual": "Manual", "unassigned": "Unassigned",
}
ALLOWED = {"Status": STATUS, "Agent": AGENTS, "Execution": EXECUTION}


class TriageError(Exception):
    pass


def normalize_agent(value: str) -> str:
    normalized = " ".join(value.lower().replace("/", " ").replace("-", " ").replace("_", " ").split())
    try:
        return AGENT_ALIASES[normalized]
    except KeyError:
        raise TriageError(f"unknown Agent option {value!r}") from None


def token_from_environment() -> str:
    token = (os.environ.get("PROJECT3_TRIAGE_TOKEN") or os.environ.get("GH_TOKEN")
             or os.environ.get("GITHUB_TOKEN"))
    if token:
        return token
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                                check=True, timeout=15)
        token = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        token = ""
    if not token:
        raise TriageError("GitHub token missing: authenticate with gh (classic project scope), or set GH_TOKEN")
    return token


def rest_client(token: str) -> Callable[[str], Any]:
    opener = pm._https_only_opener()
    prefix = "https://api.github.com/repos/Fooftilly/PRKS/issues/"

    def read(path: str) -> Any:
        if not path.startswith(prefix):
            raise TriageError("refused REST request outside PRKS issues")
        request = urllib.request.Request(path, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "User-Agent": "prks-project3-triage", "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with opener.open(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise TriageError(f"GitHub issue read failed: {type(error).__name__}") from None
        return result

    return read


def inspect_issue(number: int, read: Callable[[str], Any]) -> dict[str, Any]:
    base = f"https://api.github.com/repos/Fooftilly/PRKS/issues/{number}"
    issue = read(base)
    if (not isinstance(issue, dict) or issue.get("number") != number
            or issue.get("state") != "open" or "pull_request" in issue
            or not isinstance(issue.get("updated_at"), str)
            or not isinstance(issue.get("labels"), list)):
        raise TriageError(f"#{number}: not an open PRKS issue")
    labels = sorted(label["name"] for label in issue["labels"])
    blockers: list[int] = []
    # GitHub REST's native 'blocked by' relationship, not mentions in issue text.
    for page in range(1, 12):
        deps = read(f"{base}/dependencies/blocked_by?per_page=100&page={page}")
        if not isinstance(deps, list) or len(deps) > 100:
            raise TriageError(f"#{number}: malformed native dependencies")
        for dep in deps:
            if not isinstance(dep, dict) or not isinstance(dep.get("number"), int) or dep.get("state") not in ("open", "closed"):
                raise TriageError(f"#{number}: unknown dependency state")
            if dep["state"] == "open":
                blockers.append(dep["number"])
        if len(deps) < 100:
            break
    else:
        raise TriageError(f"#{number}: too many dependencies to verify")
    return {"state": "OPEN", "updated_at": issue["updated_at"],
            "labels": labels, "open_blockers": sorted(set(blockers))}


def _validate_edits(number: int, edits: Any) -> None:
    if not isinstance(edits, dict) or not edits or set(edits) - set(FIELDS):
        raise TriageError(f"#{number}: set may contain only Agent, Status and Execution")
    for field, value in edits.items():
        if not isinstance(value, str):
            raise TriageError(f"#{number}: {field} must be a string")
        if field == "Agent":
            value = normalize_agent(value)
            edits[field] = value
        if value not in ALLOWED[field]:
            raise TriageError(f"#{number}: unsupported {field} option {value!r}")


def _validate_plan_snapshot(number: int, row: dict) -> None:
    if type(row["approved"]) is not bool:
        raise TriageError(f"#{number}: approved must be a boolean")
    before = row["expected_before"]
    if (not isinstance(before, dict) or set(before) != set(FIELDS)
            or any(value is not None and not isinstance(value, str) for value in before.values())):
        raise TriageError(f"#{number}: all expected_before fields must be explicit")
    issue = row["expected_issue"]
    if not isinstance(issue, dict) or set(issue) != {"state", "updated_at", "labels", "open_blockers"}:
        raise TriageError(f"#{number}: expected_issue snapshot missing")
    if issue["state"] != "OPEN" or not isinstance(issue["updated_at"], str):
        raise TriageError(f"#{number}: invalid issue snapshot")
    if not isinstance(issue["labels"], list) or not isinstance(issue["open_blockers"], list):
        raise TriageError(f"#{number}: invalid issue snapshot")


def validate_row(row: Any, *, plan: bool) -> None:
    common = {"number", "set", "reason", "roadmap_checked", "overlap_checked"}
    keys = common | ({"approved", "expected_before", "expected_issue"} if plan else set())
    if not isinstance(row, dict) or set(row) != keys:
        raise TriageError("invalid triage row keys")
    number = row["number"]
    if type(number) is not int or number < 1:
        raise TriageError("issue number must be a positive integer")
    _validate_edits(number, row["set"])
    if not isinstance(row["reason"], str) or len(row["reason"].strip()) < 16:
        raise TriageError(f"#{number}: provide a substantive reason")
    if row["roadmap_checked"] is not True or row["overlap_checked"] is not True:
        raise TriageError(f"#{number}: roadmap and overlapping PR review must be attested")
    if plan:
        _validate_plan_snapshot(number, row)

def load_proposals(path: Path) -> list[dict]:
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict) or set(data) != {"items"} or not isinstance(data["items"], list):
        raise TriageError("proposal must be an object containing items")
    rows = data["items"]
    if not rows or len(rows) > MAX_ITEMS:
        raise TriageError("proposal needs 1–25 issues")
    for row in rows:
        validate_row(row, plan=False)
    if len({r["number"] for r in rows}) != len(rows):
        raise TriageError("duplicate issue number")
    return rows


def load_plan(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or set(data) != {"schema", "scope", "items"} or data["schema"] != SCHEMA or data["scope"] != SCOPE:
        raise TriageError("invalid Project #3 plan schema or scope")
    rows = data["items"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ITEMS:
        raise TriageError("plan needs 1–25 issue rows")
    for row in rows:
        validate_row(row, plan=True)
    if len({r["number"] for r in rows}) != len(rows):
        raise TriageError("duplicate issue number")
    return data


def guard(row: dict, current: dict, issue: dict) -> None:
    n = row["number"]
    if current is None or current["archived"] or current["state"] != "OPEN":
        raise TriageError(f"#{n}: missing, archived or closed Project item")
    before = {field: current["values"].get(field) for field in FIELDS}
    if before != row["expected_before"]:
        raise TriageError(f"#{n}: Project fields changed since review")
    if issue != row["expected_issue"]:
        raise TriageError(f"#{n}: issue labels, update time or blockers changed since review")
    if before["Status"] in ("Blocked", "Done"):
        raise TriageError(f"#{n}: refusing to override protected status {before['Status']}")
    active = row["set"].get("Status", before["Status"]) in ("Ready", "In Progress")
    labels = issue["labels"]
    if active and "candidate" in labels and "accepted" not in labels:
        raise TriageError(f"#{n}: candidate is not accepted")
    if active and issue["open_blockers"]:
        raise TriageError(f"#{n}: open native blockers: {issue['open_blockers']}")
    if active and row["set"].get("Execution", before["Execution"]) not in ("Agent", "Mixed"):
        raise TriageError(f"#{n}: actionable agent issue requires Execution Agent or Mixed")
    if active and row["set"].get("Agent", before["Agent"]) not in ("Cursor Cloud", "Claude Code", "ChatGPT/Codex"):
        raise TriageError(f"#{n}: actionable agent issue requires a named agent")


def project_live(project: pm.Project, number: int) -> dict:
    item = project.items.get(("Issue", number))
    if item is None:
        raise TriageError(f"#{number}: not on Project #3")
    return item


def propose(rows: list[dict], project: pm.Project, read: Callable[[str], Any]) -> dict:
    plans = []
    for r in rows:
        current = project_live(project, r["number"])
        issue = inspect_issue(r["number"], read)
        row = {**r, "approved": False,
               "expected_before": {f: current["values"].get(f) for f in FIELDS},
               "expected_issue": issue}
        guard(row, current, issue)
        plans.append(row)
    return {"schema": SCHEMA, "scope": SCOPE, "items": plans}


def preflight(plan: dict, project: pm.Project, read: Callable[[str], Any]) -> list[tuple[dict, str]]:
    rows = []
    for row in plan["items"]:
        item = project_live(project, row["number"])
        current = project.refresh(item["id"])
        issue = inspect_issue(row["number"], read)
        guard(row, current, issue)
        for field, value in row["set"].items():
            meta = project.fields.get(field)
            if (not meta or meta["type"] != "SINGLE_SELECT"
                    or value not in meta["options"]):
                raise TriageError(f"#{row['number']}: missing Project option {field}={value!r}")
        rows.append((row, item["id"]))
    return rows


def apply(plan: dict, project: pm.Project, read: Callable[[str], Any]) -> int:
    # Preflight EVERY row before sending the first mutation; held rows still
    # require a valid snapshot so drift cannot hide in an approved batch.
    rows = preflight(plan, project, read)
    changed = 0
    for row, item_id in rows:
        if not row["approved"]:
            print(f"#{row['number']}: held (not approved)")
            continue
        expected = dict(row["expected_before"])
        for field in FIELDS:
            if field not in row["set"] or row["set"][field] == expected[field]:
                continue
            # Race guard immediately before every write, including other
            # fields changed by this same reviewed batch.
            live = project.refresh(item_id)
            current_issue = inspect_issue(row["number"], read)
            if (live is None or live["archived"] or live["state"] != "OPEN"
                    or {f: live["values"].get(f) for f in FIELDS} != expected
                    or current_issue != row["expected_issue"]):
                raise TriageError(f"#{row['number']}: concurrent change; stopped after {changed} writes")
            value = row["set"][field]
            project.gql.mutate("updateProjectV2ItemFieldValue", pm.SET_M, {
                "project": pm.PROJECT_ID, "item": item_id,
                "field": project.fields[field]["id"], "option": project.fields[field]["options"][value],
            })
            expected[field] = value
            verified = project.refresh(item_id)
            if (verified is None or {f: verified["values"].get(f) for f in FIELDS} != expected
                    or verified["state"] != "OPEN" or verified["archived"]):
                raise TriageError(f"#{row['number']}: post-write verification failed; STOP and inspect live fields")
            # Dependencies and candidate approval can change between the
            # pre-write read and GitHub's separate Project field mutation.
            if inspect_issue(row["number"], read) != row["expected_issue"]:
                raise TriageError(f"#{row['number']}: issue changed during write; inspect live state")
            changed += 1
        if inspect_issue(row["number"], read) != row["expected_issue"]:
            raise TriageError(f"#{row['number']}: issue changed after field updates; inspect live state")
        print(f"#{row['number']}: verified")
    return changed


def external_plan_path(path: Path) -> Path:
    """Constrain all input/output to a dedicated maintainer-local JSON folder."""
    root = TRIAGE_DIR.expanduser().resolve()
    target = path.expanduser().resolve()
    if (not target.is_relative_to(root) or target == root
            or target.suffix != ".json"):
        raise TriageError(f"plan paths must be .json files under {root}")
    if target.is_relative_to(Path(__file__).resolve().parents[1]):
        raise TriageError("plans and proposals must be outside the PRKS repository")
    return target


def main(argv: list[str] | None = None, *, transport=None, read=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("propose", help="generate unapproved plan from proposed issue field edits")
    cmd.add_argument("--input", type=Path, required=True)
    cmd.add_argument("--out", type=Path, required=True)
    for name in ("check", "apply"):
        c = sub.add_parser(name, help="read-only validation" if name == "check" else "apply reviewed exact plan")
        c.add_argument("--plan", type=Path, required=True)
        if name == "apply":
            c.add_argument("--plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        token = None
        if transport is None or read is None:
            token = token_from_environment()
        transport = transport if transport is not None else pm.http_transport(token)
        read = read if read is not None else rest_client(token)
        gql = pm.GraphQL(transport, allow_mutations=args.command == "apply")
        project = pm.Project(gql)
        if args.command == "propose":
            plan = propose(load_proposals(external_plan_path(args.input)), project, read)
            # Use an out-of-repository local plan; do not commit approvals or
            # snapshots of Project fields. Do not overwrite an existing plan.
            args.out = external_plan_path(args.out)
            if args.out.exists():
                raise TriageError("output plan exists; refusing to overwrite")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as f:
                json.dump(plan, f, indent=2, ensure_ascii=False)
                f.write("\n")
            print(f"Plan written to {args.out}; all rows approved=false. Review it before check/apply.")
            return 0
        raw = external_plan_path(args.plan).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        plan = load_plan(raw)
        if args.command == "apply" and args.plan_sha256 != digest:
            raise TriageError("SHA-256 differs from reviewed plan; apply refused")
        preflight(plan, project, read)
        print(f"Project #3 triage: {len(plan['items'])} rows; {sum(r['approved'] for r in plan['items'])} approved")
        print(f"Plan SHA-256: {digest}")
        if args.command == "check":
            print("Read-only preflight passed; no GitHub fields changed.")
            return 0
        print(f"Applied and verified {apply(plan, project, read)} field edits.")
        return 0
    except (TriageError, pm.MigrationError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"triage refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
