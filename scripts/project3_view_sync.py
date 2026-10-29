#!/usr/bin/env python3
"""Versioned GitHub Project #3 Agent Queue (#9) filter policy.

Agent ownership is not execution readiness. The Agent Queue shows every open
issue owned by a named coding agent, including pending, blocked and in-progress
work. The existing view's Status grouping distinguishes their lifecycle states.
An agent may start work only when Status=Ready, Execution=Agent/Mixed, the issue
is accepted and no native Blocked-by dependency is open.

On a protected master push, reconcile only the view #9 filter; preserve its
name, layout, visible columns, grouping, sort and all other project views.
A daily --fail-on-drift job checks the policy without writing GitHub.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable

import project3_migrate as pm

PROJECT_ID = pm.PROJECT_ID
OWNER = pm.OWNER
PROJECT_NUMBER = pm.PROJECT_NUMBER
QUEUE_NUMBER = 9
QUEUE_NAME = "Agent Queue"
QUEUE_LAYOUT = "TABLE_LAYOUT"
MAX_PAGES = 10

OWNED_FILTER = (
    'is:open is:issue '
    'agent:"Cursor Cloud","Claude Code","ChatGPT/Codex"'
)

VIEWS_QUERY = """query($owner:String!,$number:Int!,$after:String){
  user(login:$owner) {
    projectV2(number:$number) {
      id number owner { __typename ... on User { login } }
      views(first:100,after:$after) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { id number name filter layout }
      }
    }
  }
}"""
UPDATE_VIEW = """mutation($input:UpdateProjectV2ViewInput!){
  updateProjectV2View(input:$input){
    projectV2View { id number name filter layout }
  }
}"""

Transport = Callable[[str, dict], dict]


class ViewError(Exception):
    pass


def _connection(result: Any) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("user"), dict):
        raise ViewError("Project owner could not be queried")
    project = result["user"].get("projectV2")
    if (not isinstance(project, dict) or project.get("id") != PROJECT_ID
            or project.get("number") != PROJECT_NUMBER):
        raise ViewError("Project #3 identity does not match")
    owner = project.get("owner")
    if (not isinstance(owner, dict) or owner.get("__typename") != "User"
            or owner.get("login") != OWNER):
        raise ViewError("Project #3 ownership does not match")
    conn = project.get("views")
    if not isinstance(conn, dict) or type(conn.get("totalCount")) is not int:
        raise ViewError("Project view connection is incomplete")
    if not isinstance(conn.get("nodes"), list) or not isinstance(conn.get("pageInfo"), dict):
        raise ViewError("Project view page is malformed")
    return conn


def _view(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ViewError("Project view is not an object")
    if (not isinstance(raw.get("id"), str)
            or type(raw.get("number")) is not int
            or not isinstance(raw.get("name"), str)
            or not isinstance(raw.get("filter"), (str, type(None)))
            or not isinstance(raw.get("layout"), str)):
        raise ViewError("Project view has unknown field values")
    return raw


def _next_cursor(info: dict, seen: set[str]) -> str:
    cursor = info.get("endCursor")
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        raise ViewError("Malformed or repeating Project view cursor")
    seen.add(cursor)
    return cursor


def views(transport: Transport) -> list[dict[str, Any]]:
    """Read every view page and fail on drift/partial pagination."""
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    seen_cursors: set[str] = set()
    expected_total: int | None = None
    after: str | None = None
    for _ in range(MAX_PAGES):
        conn = _connection(transport(VIEWS_QUERY, {
            "owner": OWNER, "number": PROJECT_NUMBER, "after": after
        }))
        total = conn["totalCount"]
        if expected_total is not None and total != expected_total:
            raise ViewError("Project views changed during pagination")
        expected_total = total
        for raw in conn["nodes"]:
            item = _view(raw)
            if item["id"] in seen_ids or item["number"] in seen_numbers:
                raise ViewError("Duplicate Project view in pagination")
            seen_ids.add(item["id"])
            seen_numbers.add(item["number"])
            items.append(item)
        info = conn["pageInfo"]
        if info.get("hasNextPage") is False:
            if len(items) != total:
                raise ViewError(f"Incomplete Project views: read {len(items)} of {total}")
            return items
        after = _next_cursor(info, seen_cursors)
    raise ViewError("Too many Project views to verify")


def queue(items: list[dict]) -> dict:
    candidates = [v for v in items if v["number"] == QUEUE_NUMBER]
    if (len(candidates) != 1 or candidates[0]["name"] != QUEUE_NAME
            or candidates[0]["layout"] != QUEUE_LAYOUT):
        raise ViewError("View #9 is no longer the expected Agent Queue table; refusing to modify")
    return candidates[0]


def reconcile(transport: Transport, *, apply: bool) -> list[str]:
    current = queue(views(transport))
    if current["filter"] == OWNED_FILTER:
        return []
    if not apply:
        return ["View #9 Agent Queue: ownership filter differs from policy"]
    # Guard against an independent UI edit while the view list was fetched.
    if queue(views(transport)) != current:
        raise ViewError("Agent Queue changed concurrently; no mutation made")
    result = transport(UPDATE_VIEW, {
        "input": {"viewId": current["id"], "filter": OWNED_FILTER}
    })
    payload = result.get("updateProjectV2View") if isinstance(result, dict) else None
    updated = payload.get("projectV2View") if isinstance(payload, dict) else None
    if not isinstance(updated, dict) or updated.get("id") != current["id"]:
        raise ViewError("Invalid update response; inspect view #9 before retrying")
    after = queue(views(transport))
    if (after["filter"] != OWNED_FILTER or after["id"] != current["id"]
            or after["name"] != current["name"] or after["layout"] != current["layout"]):
        raise ViewError("Agent Queue post-write verification failed")
    return ["View #9 Agent Queue: ownership filter reconciled"]


def main(argv: list[str] | None = None, *, transport: Transport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="reconcile view #9 (protected GitHub Actions master only)")
    parser.add_argument("--fail-on-drift", action="store_true",
                        help="read-only verification exits nonzero on configuration drift")
    args = parser.parse_args(argv)
    try:
        if args.apply and args.fail_on_drift:
            raise ViewError("--apply and --fail-on-drift cannot be combined")
        if args.apply and (os.environ.get("GITHUB_REPOSITORY") != pm.REPOSITORY
                           or os.environ.get("GITHUB_REF") != "refs/heads/master"):
            raise ViewError("--apply requires the protected PRKS master-branch workflow")
        if transport is None:
            token = os.environ.get("PROJECT_SYNC_TOKEN")
            if not token:
                raise ViewError("PROJECT_SYNC_TOKEN not configured")
            transport = pm.http_transport(token)
        changes = reconcile(transport, apply=args.apply)
        for change in changes:
            print(("Applied: " if args.apply else "Drift: ") + change)
        if not args.apply:
            print("Read-only check; no Project views changed.")
            if changes and args.fail_on_drift:
                raise ViewError("Project view configuration drift detected")
        if not changes:
            print("Agent Queue already matches the versioned filter policy.")
        return 0
    except (ViewError, pm.MigrationError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Project #3 view sync refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
