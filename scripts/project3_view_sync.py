#!/usr/bin/env python3
"""Version-controlled GitHub Project #3 agent views, reconciled on master.

Agent ownership and execution readiness are different:
- Agent Queue (#9): every open issue assigned to a named coding agent,
  including pending/blocked/in-progress work. This is an OWNERSHIP view.
- Agent Ready: only Ready issues with Agent/Mixed execution and named owner.
  Open native dependencies must STILL be checked before starting.

Run without --apply to inspect view drift. On a protected master push the
GitHub Actions job uses --apply, an environment-scoped Project token and a
fixed project identity. No issue status, execution, owner, or labels are edited.
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

OWNED_FILTER = (
    'is:open is:issue -label:dependencies '
    'agent:"Cursor Cloud","Claude Code","ChatGPT/Codex"'
)
READY_FILTER = (
    OWNED_FILTER + ' execution:Agent,Mixed status:Ready'
)
QUEUE_NUMBER = 9
QUEUE_NAME = "Agent Queue"
READY_NAME = "Agent Ready"
MAX_PAGES = 10

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
CREATE_VIEW = """mutation($input:CreateProjectV2ViewInput!){
  createProjectV2View(input:$input){
    projectV2View { id number name filter layout }
  }
}"""

Transport = Callable[[str, dict], dict]


class ViewError(Exception):
    pass


def _connection(result: Any) -> dict:
    """Require the exact user-owned Project #3, never a similarly named board."""
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


def identify(items: list[dict]) -> tuple[dict, dict | None]:
    queue = [v for v in items if v["number"] == QUEUE_NUMBER]
    ready = [v for v in items if v["name"] == READY_NAME]
    if len(queue) != 1 or queue[0]["name"] != QUEUE_NAME or queue[0]["layout"] != "TABLE_LAYOUT":
        raise ViewError("View #9 is no longer the expected Agent Queue table; refusing to modify")
    if len(ready) > 1:
        raise ViewError("Multiple Agent Ready views; refusing to guess")
    if ready and ready[0]["layout"] != "TABLE_LAYOUT":
        raise ViewError("Existing Agent Ready view is not a table")
    return queue[0], ready[0] if ready else None


def mutate(transport: Transport, document: str, name: str, variables: dict) -> dict:
    result = transport(document, variables)
    payload = result.get(name) if isinstance(result, dict) else None
    view = payload.get("projectV2View") if isinstance(payload, dict) else None
    if not isinstance(view, dict) or not isinstance(view.get("id"), str):
        raise ViewError(f"{name} response is invalid; inspect live Project view before retrying")
    return view


def update_filter(transport: Transport, view: dict, new_filter: str) -> None:
    latest = next((v for v in views(transport) if v["id"] == view["id"]), None)
    if latest != view:
        raise ViewError(f"{view['name']}: view changed concurrently; no write made")
    mutate(transport, UPDATE_VIEW, "updateProjectV2View", {
        "input": {"viewId": view["id"], "filter": new_filter}
    })
    updated = next((v for v in views(transport) if v["id"] == view["id"]), None)
    if (updated is None or updated["filter"] != new_filter
            or updated["name"] != view["name"] or updated["layout"] != view["layout"]):
        raise ViewError(f"{view['name']}: post-write verification failed")


def reconcile(transport: Transport, *, apply: bool) -> list[str]:
    state = views(transport)
    queue, ready = identify(state)
    changes: list[str] = []
    if queue["filter"] != OWNED_FILTER:
        changes.append(f"View #9 {QUEUE_NAME}: update ownership filter")
        if apply:
            update_filter(transport, queue, OWNED_FILTER)
    if ready is None:
        changes.append(f"Create {READY_NAME} table (Ready-only filter)")
        if apply:
            current = views(transport)
            _, found = identify(current)
            if found is not None:
                raise ViewError("Agent Ready was concurrently created; rerun")
            created = mutate(transport, CREATE_VIEW, "createProjectV2View", {
                "input": {"projectId": PROJECT_ID, "name": READY_NAME, "layout": "TABLE_LAYOUT"}
            })
            # Creation precedes filter assignment because CreateProjectV2ViewInput
            # does not accept a filter. Verify and set it immediately.
            fresh = views(transport)
            ready = next((v for v in fresh if v["id"] == created["id"]), None)
            if ready is None or ready["name"] != READY_NAME or ready["layout"] != "TABLE_LAYOUT":
                raise ViewError("Created Agent Ready view needs inspection")
            update_filter(transport, ready, READY_FILTER)
    elif ready["filter"] != READY_FILTER:
        changes.append(f"{READY_NAME}: update actionable-only filter")
        if apply:
            update_filter(transport, ready, READY_FILTER)
    if apply:
        final_queue, final_ready = identify(views(transport))
        if final_queue["filter"] != OWNED_FILTER or final_ready is None or final_ready["filter"] != READY_FILTER:
            raise ViewError("Post-reconciliation policy mismatch")
    return changes


def main(argv: list[str] | None = None, *, transport: Transport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="reconcile both views (protected GitHub Actions master only)")
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
        if not changes:
            print("Project #3 agent views already match policy.")
        else:
            for change in changes:
                print(("Applied: " if args.apply else "Drift: ") + change)
        if not args.apply:
            print("Read-only check; no Project views changed.")
            if changes and args.fail_on_drift:
                raise ViewError("Project view configuration drift detected")
        return 0
    except (ViewError, pm.MigrationError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Project #3 view sync refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
