#!/usr/bin/env python3
"""Read-only Project #3 readiness snapshot for triage (#441).

Commands:

  snapshot        read Project #3 and the open PRKS issues and write one JSON
                  snapshot of each issue's Project Status, Execution and Agent
  check           validate a downloaded snapshot and enforce its freshness limit
  planning        write the planning snapshot: every PRKS issue item with its
                  Implementation Order and other planning fields
  check-planning  validate a downloaded planning snapshot and its freshness

Runs from ``.github/workflows/project3-readiness.yml``. The snapshot lets a
triage that cannot query Project V2 custom fields (the ChatGPT PRKS Daily
Triage reads GitHub through REST) tell Inbox, Backlog, Ready and In Progress
apart. See ``docs/agent-workflows/project-3.md`` section 14.

Safety contract (enforced here, not only documented):

* Read-only. Every GraphQL document goes through ``ReadOnlyGraphQL``, which
  refuses anything but a query, and this file defines no mutation. It does
  not import ``project_sync`` or ``project3_migrate``, which do write.
* Scope: owner Fooftilly, project 3, node id PVT_kwHOAsc2_s4BkAo3, issues
  from the public repository Fooftilly/PRKS. PRs, draft issues, redacted
  items and other repositories' content are skipped and only counted.
* Confidentiality: the snapshot is published as an Actions artifact of a
  public repository. Unless Project #3 itself is public (``public: true``),
  its field values are not public data, so the run refuses and writes
  nothing. There is no override.
* Fail closed: every page is read (``hasNextPage``/``endCursor``), counts
  must match ``totalCount`` on every page, and a duplicate item, an unknown
  shape, a missing field, an API error or a missing or rejected token stops
  the run without writing a file. A partial snapshot is never written.
* Unset stays unset: an empty Status, Execution or Agent is ``null``. An
  open issue that is not on Project #3 is listed with ``in_project: false``;
  an issue on Project #3 with empty fields has ``in_project: true`` and
  ``null`` values. Readiness is never inferred from an empty field.

Token: ``PROJECT3_READINESS_TOKEN`` (the workflow passes the existing
``PROJECT_SYNC_TOKEN``; classic ``project`` scope). Never printed.
Standard library only.
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

GRAPHQL_URL = "https://api.github.com/graphql"
OWNER = "Fooftilly"
REPOSITORY = "Fooftilly/PRKS"
PROJECT_NUMBER = 3
PROJECT_ID = "PVT_kwHOAsc2_s4BkAo3"
SCHEMA = "prks-project3-readiness/v1"
# Snapshot key -> Project #3 single-select field name.
FIELDS = {"status": "Status", "execution": "Execution", "agent": "Agent"}
# A snapshot older than this is stale. The daily run starts at 02:23 UTC and
# GitHub may delay scheduled runs by hours, so a limit of 30 hours keeps one
# delayed run usable but never a run that was missed entirely.
MAX_AGE_HOURS = 30
# Clock skew tolerated before a snapshot dated in the future is refused.
MAX_FUTURE_SKEW = timedelta(minutes=5)
PAGE_SIZE = 100
MAX_PAGES = 100
ASSIGNEES_PAGE = 20
PLANNING_SCHEMA = "prks-project3-planning/v1"
PLANNING_ARTIFACT_FILE = "project3-planning.json"
# Planning snapshot key -> (Project #3 field name, required data type).
PLANNING_FIELDS = {
    "status": ("Status", "SINGLE_SELECT"),
    "execution": ("Execution", "SINGLE_SELECT"),
    "agent": ("Agent", "SINGLE_SELECT"),
    "implementation_order": ("Implementation Order", "NUMBER"),
    "roadmap_order": ("Roadmap Order", "NUMBER"),
    "priority": ("Priority", "SINGLE_SELECT"),
    "effort": ("Effort", "SINGLE_SELECT"),
    "horizon": ("Horizon", "SINGLE_SELECT"),
    "work_type": ("Work Type", "SINGLE_SELECT"),
    "commitment": ("Commitment", "SINGLE_SELECT"),
}
LABELS_PAGE = 50
_REPO_ROOT = Path(__file__).resolve().parents[1]

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class ReadinessError(Exception):
    """The snapshot cannot be produced or trusted; nothing is written."""


class NotConfigured(ReadinessError):
    pass


class Confidential(ReadinessError):
    """Project #3 is not public, so its data is not published."""


class Malformed(ReadinessError):
    pass


# --- transport ----------------------------------------------------------------

_COMMENT_RE = re.compile(r"#[^\n]*")
_OPERATION_RE = re.compile(r"\b(mutation|subscription)\b", re.IGNORECASE)


def is_read_only(document: str) -> bool:
    """True only for a single anonymous or named query with no mutation or
    subscription keyword anywhere (string literals included, to stay simple
    and strict)."""
    text = _COMMENT_RE.sub("", document).strip()
    if _OPERATION_RE.search(text):
        return False
    return text.startswith("query") or text.startswith("{")


class ReadOnlyGraphQL:
    """The only way this module talks to GitHub: queries, never mutations."""

    def __init__(self, transport: Transport) -> None:
        self._send = transport

    def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        if not is_read_only(document):
            raise ReadinessError("refused: only GraphQL queries may be sent")
        data = self._send(document, variables)
        if not isinstance(data, dict):
            raise Malformed("GraphQL response has no data object")
        return data


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

    def send(document: str, variables: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            GRAPHQL_URL,
            data=json.dumps({"query": document, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "prks-project3-readiness",
            },
        )
        try:
            with opener.open(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise NotConfigured(
                    f"GraphQL HTTP {error.code}: the token was rejected (missing, expired, revoked, lacking a scope, or rate limited)"
                ) from None
            raise ReadinessError(f"GraphQL HTTP {error.code}") from None
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise ReadinessError(f"GraphQL request failed: {type(error).__name__}") from None
        if not isinstance(payload, dict):
            raise Malformed("GraphQL response is not an object")
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message", "")) for e in payload["errors"] if isinstance(e, dict))
            raise ReadinessError(f"GraphQL error: {messages or 'unknown'}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise Malformed("GraphQL response has no data")
        return data

    return send


# --- queries ------------------------------------------------------------------

PROJECT_QUERY = """
query($owner: String!, $number: Int!) {
  user(login: $owner) {
    projectV2(number: $number) {
      id number public closed
      owner { __typename ... on User { login } }
      fields(first: 50) {
        totalCount
        nodes { __typename ... on ProjectV2FieldCommon { id name dataType } }
      }
    }
  }
}
"""

_VALUE = "{ __typename ... on ProjectV2ItemFieldSingleSelectValue { name } }"
_ASSIGNEES = f"assignees(first: {ASSIGNEES_PAGE}) {{ totalCount nodes {{ login }} }}"

ITEMS_QUERY = (
    "query($owner: String!, $number: Int!, $after: String, $status: String!, $execution: String!, $agent: String!) {\n"
    "  user(login: $owner) {\n"
    "    projectV2(number: $number) {\n"
    "      id\n"
    f"      items(first: {PAGE_SIZE}, after: $after) {{\n"
    "        totalCount\n"
    "        pageInfo { hasNextPage endCursor }\n"
    "        nodes {\n"
    "          id isArchived\n"
    f"          status: fieldValueByName(name: $status) {_VALUE}\n"
    f"          execution: fieldValueByName(name: $execution) {_VALUE}\n"
    f"          agent: fieldValueByName(name: $agent) {_VALUE}\n"
    "          content {\n"
    "            __typename\n"
    f"            ... on Issue {{ number state repository {{ nameWithOwner isPrivate }} {_ASSIGNEES} }}\n"
    "            ... on PullRequest { number repository { nameWithOwner } }\n"
    "          }\n"
    "        }\n"
    "      }\n"
    "    }\n"
    "  }\n"
    "}\n"
)

# The queue publishes ownership, not issue assignees. Keep all other Project
# item fields and pagination identical while avoiding the unrelated assignee cap.
QUEUE_ITEMS_QUERY = ITEMS_QUERY.replace(_ASSIGNEES, "")

ISSUES_QUERY = (
    "query($owner: String!, $name: String!, $after: String) {\n"
    "  repository(owner: $owner, name: $name) {\n"
    "    nameWithOwner isPrivate\n"
    f"    issues(first: {PAGE_SIZE}, after: $after, states: [OPEN], orderBy: {{field: CREATED_AT, direction: ASC}}) {{\n"
    "      totalCount\n"
    "      pageInfo { hasNextPage endCursor }\n"
    f"      nodes {{ number state {_ASSIGNEES} }}\n"
    "    }\n"
    "  }\n"
    "}\n"
)

_PLANNING_VALUE = ("{ __typename ... on ProjectV2ItemFieldSingleSelectValue { name } "
                   "... on ProjectV2ItemFieldNumberValue { number } }")

PLANNING_ITEMS_QUERY = (
    "query($owner: String!, $number: Int!, $after: String, "
    + ", ".join(f"${key}: String!" for key in PLANNING_FIELDS) + ") {\n"
    "  user(login: $owner) {\n"
    "    projectV2(number: $number) {\n"
    "      id\n"
    f"      items(first: {PAGE_SIZE}, after: $after) {{\n"
    "        totalCount\n"
    "        pageInfo { hasNextPage endCursor }\n"
    "        nodes {\n"
    "          id isArchived\n"
    + "".join(f"          {key}: fieldValueByName(name: ${key}) {_PLANNING_VALUE}\n" for key in PLANNING_FIELDS)
    + "          content {\n"
    "            __typename\n"
    "            ... on Issue { number state repository { nameWithOwner isPrivate }\n"
    f"              labels(first: {LABELS_PAGE}) {{ totalCount nodes {{ name }} }}\n"
    "              parent { number repository { nameWithOwner } } }\n"
    "          }\n"
    "        }\n"
    "      }\n"
    "    }\n"
    "  }\n"
    "}\n"
)

QUERIES = (PROJECT_QUERY, ITEMS_QUERY, QUEUE_ITEMS_QUERY, ISSUES_QUERY, PLANNING_ITEMS_QUERY)


# --- parsing ------------------------------------------------------------------

def _obj(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Malformed(f"{what} is missing or not an object")
    return value


def _list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise Malformed(f"{what} is missing or not a list")
    return value


def _int(value: Any, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Malformed(f"{what} is not a non-negative integer")
    return value


def _str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise Malformed(f"{what} is not a non-empty string")
    return value


def _bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise Malformed(f"{what} is not a boolean")
    return value


def _assignees(node: dict[str, Any], what: str) -> list[str]:
    conn = _obj(node.get("assignees"), f"{what} assignees")
    logins = sorted(_str(_obj(a, f"{what} assignee").get("login"), f"{what} assignee login")
                    for a in _list(conn.get("nodes"), f"{what} assignees"))
    if _int(conn.get("totalCount"), f"{what} assignees totalCount") != len(logins):
        raise Malformed(f"{what} has more assignees than one page returned")
    return logins


def _field_value(value: Any, what: str) -> Optional[str]:
    if value is None:
        return None
    node = _obj(value, what)
    if node.get("__typename") != "ProjectV2ItemFieldSingleSelectValue":
        raise Malformed(f"{what} is not a single-select value")
    return _str(node.get("name"), f"{what} name")


def _number_value(value: Any, what: str) -> Optional[int | float]:
    if value is None:
        return None
    node = _obj(value, what)
    if node.get("__typename") != "ProjectV2ItemFieldNumberValue":
        raise Malformed(f"{what} is not a number value")
    number = node.get("number")
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
        raise Malformed(f"{what} is not a finite number")
    return int(number) if number == int(number) else number


def check_planning_fields(data: dict[str, Any]) -> None:
    project = _obj(_obj(data.get("user"), "user").get("projectV2"), "projectV2")
    nodes = _list(_obj(project.get("fields"), "project fields").get("nodes"), "project fields")
    types = {node["name"]: node.get("dataType") for node in nodes if isinstance(node, dict) and node.get("name")}
    for name, kind in PLANNING_FIELDS.values():
        if types.get(name) != kind:
            raise ReadinessError(f"Project #3 field {name!r} is missing or not {kind}")


def check_project(data: dict[str, Any]) -> dict[str, Any]:
    """Verify Project #3's identity, visibility and the three fields."""
    project = _obj(_obj(data.get("user"), "user").get("projectV2"), "projectV2")
    owner = _obj(project.get("owner"), "project owner")
    if (project.get("id") != PROJECT_ID or project.get("number") != PROJECT_NUMBER
            or owner.get("__typename") != "User" or owner.get("login") != OWNER):
        raise ReadinessError("project identity check failed (owner / number / node id)")
    if _bool(project.get("public"), "project public") is not True:
        raise Confidential(
            "Project #3 is private, so its field values are not public data and are not "
            "published from this public repository; no snapshot was written"
        )
    fields = _obj(project.get("fields"), "project fields")
    nodes = _list(fields.get("nodes"), "project fields")
    if _int(fields.get("totalCount"), "fields totalCount") != len(nodes):
        raise Malformed("project has more fields than one page returned")
    types = {node["name"]: node.get("dataType") for node in nodes if isinstance(node, dict) and node.get("name")}
    for name in FIELDS.values():
        if name not in types:
            raise ReadinessError(f"Project #3 has no {name!r} field")
        if types[name] != "SINGLE_SELECT":
            raise ReadinessError(f"Project #3 field {name!r} is {types[name]}, not single select")
    return {"owner": OWNER, "number": PROJECT_NUMBER, "id": PROJECT_ID, "public": True,
            "closed": _bool(project.get("closed"), "project closed")}


def _pages(gql: ReadOnlyGraphQL, document: str, variables: dict[str, Any],
           connection: Callable[[dict[str, Any]], dict[str, Any]], what: str) -> list[Any]:
    """Read every page of a connection; fail on any sign of a partial read."""
    nodes: list[Any] = []
    after: Optional[str] = None
    seen_cursors: set[str] = set()
    total: Optional[int] = None
    for _ in range(MAX_PAGES):
        conn = connection(gql.query(document, dict(variables, after=after)))
        page_total = _int(conn.get("totalCount"), f"{what} totalCount")
        if total is None:
            total = page_total
        elif page_total != total:
            raise ReadinessError(f"{what} changed while it was read ({total} -> {page_total}); rerun")
        nodes.extend(_list(conn.get("nodes"), f"{what} nodes"))
        info = _obj(conn.get("pageInfo"), f"{what} pageInfo")
        if not _bool(info.get("hasNextPage"), f"{what} hasNextPage"):
            if len(nodes) != total:
                raise Malformed(f"{what}: read {len(nodes)} of totalCount {total}")
            return nodes
        cursor = _str(info.get("endCursor"), f"{what} endCursor")
        if cursor in seen_cursors:
            raise Malformed(f"{what} pagination repeated a cursor")
        seen_cursors.add(cursor)
        after = cursor
    raise Malformed(f"{what} has more than {MAX_PAGES} pages")


def read_items(gql: ReadOnlyGraphQL, *, include_assignees: bool = True) -> tuple[dict[int, dict[str, Any]], Counter[str]]:
    variables = {"owner": OWNER, "number": PROJECT_NUMBER, **FIELDS}

    def items(data: dict[str, Any]) -> dict[str, Any]:
        project = _obj(_obj(data.get("user"), "user").get("projectV2"), "projectV2")
        if project.get("id") != PROJECT_ID:
            raise ReadinessError("project identity changed between pages")
        return _obj(project.get("items"), "project items")

    issues: dict[int, dict[str, Any]] = {}
    item_ids: set[str] = set()
    skipped: Counter[str] = Counter()
    document = ITEMS_QUERY if include_assignees else QUEUE_ITEMS_QUERY
    for raw in _pages(gql, document, variables, items, "project items"):
        node = _obj(raw, "project item")
        item_id = _str(node.get("id"), "project item id")
        if item_id in item_ids:
            raise Malformed(f"project item {item_id} was returned twice")
        item_ids.add(item_id)
        content = node.get("content")
        kind = content.get("__typename") if isinstance(content, dict) else None
        if kind == "PullRequest":
            skipped["pull_requests"] += 1
            continue
        if kind == "DraftIssue":
            skipped["draft_issues"] += 1
            continue
        if kind != "Issue":
            # null content: deleted, or from a repository the token cannot see.
            skipped["redacted_or_unknown"] += 1
            continue
        issue = _obj(content, "issue content")
        repo = _obj(issue.get("repository"), "issue repository")
        if repo.get("nameWithOwner") != REPOSITORY:
            skipped["other_repositories"] += 1
            continue
        if _bool(repo.get("isPrivate"), "repository isPrivate"):
            raise Confidential(f"{REPOSITORY} is private; no snapshot was written")
        number = _int(issue.get("number"), "issue number")
        if number in issues:
            raise Malformed(f"issue #{number} has more than one Project #3 item")
        issues[number] = {
            "number": number,
            "state": _str(issue.get("state"), f"issue #{number} state"),
            "in_project": True,
            "project_item_id": item_id,
            "archived": _bool(node.get("isArchived"), f"issue #{number} isArchived"),
            **{key: _field_value(node.get(key), f"issue #{number} {name}") for key, name in FIELDS.items()},
            "assignees": (_assignees(issue, f"issue #{number}")
                          if include_assignees else []),
        }
    return issues, skipped


def read_open_issues(gql: ReadOnlyGraphQL) -> list[dict[str, Any]]:
    owner, name = REPOSITORY.split("/")

    def issues(data: dict[str, Any]) -> dict[str, Any]:
        repo = _obj(data.get("repository"), "repository")
        if repo.get("nameWithOwner") != REPOSITORY:
            raise ReadinessError("repository identity check failed")
        if _bool(repo.get("isPrivate"), "repository isPrivate"):
            raise Confidential(f"{REPOSITORY} is private; no snapshot was written")
        return _obj(repo.get("issues"), "repository issues")

    found = []
    for raw in _pages(gql, ISSUES_QUERY, {"owner": owner, "name": name}, issues, "open issues"):
        node = _obj(raw, "issue")
        number = _int(node.get("number"), "issue number")
        found.append({"number": number, "state": _str(node.get("state"), f"issue #{number} state"),
                      "assignees": _assignees(node, f"issue #{number}")})
    return found


def _labels(issue: dict[str, Any], what: str) -> list[str]:
    conn = _obj(issue.get("labels"), f"{what} labels")
    names = sorted(_str(_obj(label, f"{what} label").get("name"), f"{what} label name")
                   for label in _list(conn.get("nodes"), f"{what} labels"))
    if _int(conn.get("totalCount"), f"{what} labels totalCount") != len(names):
        raise Malformed(f"{what} has more labels than one page returned")
    return names


def _parent(issue: dict[str, Any], what: str) -> Optional[str]:
    parent = issue.get("parent")
    if parent is None:
        return None
    node = _obj(parent, f"{what} parent")
    repo = _str(_obj(node.get("repository"), f"{what} parent repository").get("nameWithOwner"),
                f"{what} parent repository")
    return f"{repo}#{_int(node.get('number'), f'{what} parent number')}"


def read_planning_items(gql: ReadOnlyGraphQL) -> tuple[list[dict[str, Any]], Counter[str]]:
    variables = {"owner": OWNER, "number": PROJECT_NUMBER,
                 **{key: name for key, (name, _) in PLANNING_FIELDS.items()}}

    def items(data: dict[str, Any]) -> dict[str, Any]:
        project = _obj(_obj(data.get("user"), "user").get("projectV2"), "projectV2")
        if project.get("id") != PROJECT_ID:
            raise ReadinessError("project identity changed between pages")
        return _obj(project.get("items"), "project items")

    rows: dict[int, dict[str, Any]] = {}
    item_ids: set[str] = set()
    skipped: Counter[str] = Counter()
    for raw in _pages(gql, PLANNING_ITEMS_QUERY, variables, items, "project items"):
        node = _obj(raw, "project item")
        item_id = _str(node.get("id"), "project item id")
        if item_id in item_ids:
            raise Malformed(f"project item {item_id} was returned twice")
        item_ids.add(item_id)
        content = node.get("content")
        kind = content.get("__typename") if isinstance(content, dict) else None
        if kind != "Issue":
            skipped[{"PullRequest": "pull_requests", "DraftIssue": "draft_issues"}.get(
                kind or "", "redacted_or_unknown")] += 1
            continue
        issue = _obj(content, "issue content")
        repo = _obj(issue.get("repository"), "issue repository")
        if repo.get("nameWithOwner") != REPOSITORY:
            skipped["other_repositories"] += 1
            continue
        if _bool(repo.get("isPrivate"), "repository isPrivate"):
            raise Confidential(f"{REPOSITORY} is private; no snapshot was written")
        number = _int(issue.get("number"), "issue number")
        what = f"issue #{number}"
        if number in rows:
            raise Malformed(f"issue #{number} has more than one Project #3 item")
        values: dict[str, Any] = {}
        for key, (name, data_type) in PLANNING_FIELDS.items():
            reader = _number_value if data_type == "NUMBER" else _field_value
            values[key] = reader(node.get(key), f"{what} {name}")
        rows[number] = {
            "number": number,
            "state": _str(issue.get("state"), f"{what} state"),
            "project_item_id": item_id,
            "archived": _bool(node.get("isArchived"), f"{what} isArchived"),
            **values,
            "labels": _labels(issue, what),
            "parent": _parent(issue, what),
        }
    return [rows[n] for n in sorted(rows)], skipped


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_snapshot(transport: Transport, *, generated_at: datetime,
                   source: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    gql = ReadOnlyGraphQL(transport)
    project = check_project(gql.query(PROJECT_QUERY, {"owner": OWNER, "number": PROJECT_NUMBER}))
    on_board, skipped = read_items(gql)
    issues = dict(on_board)
    seen_open: set[int] = set()
    for issue in read_open_issues(gql):
        if issue["number"] in seen_open:
            raise Malformed(f"open issue #{issue['number']} was returned twice")
        seen_open.add(issue["number"])
        if issue["number"] not in issues:
            issues[issue["number"]] = {
                "number": issue["number"], "state": issue["state"], "in_project": False,
                "project_item_id": None, "archived": None,
                **{key: None for key in FIELDS}, "assignees": issue["assignees"],
            }
    # Re-check identity and visibility after every read: a project made
    # private while the items were being read must not be published.
    if check_project(gql.query(PROJECT_QUERY, {"owner": OWNER, "number": PROJECT_NUMBER})) != project:
        raise ReadinessError("Project #3 changed while it was read; rerun")
    rows = [issues[n] for n in sorted(issues)]
    return {
        "schema": SCHEMA,
        "complete": True,
        "generated_at": _iso(generated_at),
        "stale_after": _iso(generated_at + timedelta(hours=MAX_AGE_HOURS)),
        "max_age_hours": MAX_AGE_HOURS,
        "project": project,
        "repository": REPOSITORY,
        "coverage": "every open issue of the repository, plus every closed issue that has a Project #3 item",
        "fields": dict(FIELDS),
        "source": source,
        "counts": {
            "issues": len(rows),
            "in_project": sum(1 for r in rows if r["in_project"]),
            "open_not_in_project": sum(1 for r in rows if not r["in_project"]),
            "skipped_items": dict(sorted(skipped.items())),
        },
        "issues": rows,
    }


def build_planning(transport: Transport, *, generated_at: datetime,
                   source: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    gql = ReadOnlyGraphQL(transport)
    identity = {"owner": OWNER, "number": PROJECT_NUMBER}
    first = gql.query(PROJECT_QUERY, identity)
    project = check_project(first)
    check_planning_fields(first)
    rows, skipped = read_planning_items(gql)
    again = gql.query(PROJECT_QUERY, identity)
    if check_project(again) != project:
        raise ReadinessError("Project #3 changed while it was read; rerun")
    check_planning_fields(again)
    return {
        "schema": PLANNING_SCHEMA,
        "complete": True,
        "generated_at": _iso(generated_at),
        "stale_after": _iso(generated_at + timedelta(hours=MAX_AGE_HOURS)),
        "max_age_hours": MAX_AGE_HOURS,
        "project": project,
        "repository": REPOSITORY,
        "coverage": "every Fooftilly/PRKS issue item on Project #3, open or closed",
        "dependencies": "not included: read GET /repos/Fooftilly/PRKS/issues/{n}/dependencies/blocked_by live; a failed read is unknown, not none",
        "fields": {key: name for key, (name, _) in PLANNING_FIELDS.items()},
        "source": source,
        "counts": {"issues": len(rows), "skipped_items": dict(sorted(skipped.items()))},
        "issues": rows,
    }


# --- consumer-side validation -------------------------------------------------

def _parse_time(value: Any, what: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise Malformed(f"{what} is not a UTC timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def check_snapshot(snapshot: Any, *, now: datetime, max_age_hours: float = MAX_AGE_HOURS) -> datetime:
    """Raise unless the snapshot is complete, for Project #3 and fresh.
    Returns its generation time."""
    data = _obj(snapshot, "snapshot")
    if data.get("schema") != SCHEMA:
        raise Malformed(f"unknown snapshot schema {data.get('schema')!r}")
    if data.get("complete") is not True:
        raise ReadinessError("snapshot is not marked complete")
    project = _obj(data.get("project"), "snapshot project")
    if (project.get("id"), project.get("number"), project.get("owner")) != (PROJECT_ID, PROJECT_NUMBER, OWNER) \
            or data.get("repository") != REPOSITORY:
        raise ReadinessError("snapshot is not for Project #3 / Fooftilly/PRKS")
    generated = _parse_time(data.get("generated_at"), "generated_at")
    if generated - now > MAX_FUTURE_SKEW:
        raise ReadinessError("snapshot is dated in the future")
    age = now - generated
    if age > timedelta(hours=max_age_hours):
        raise ReadinessError(f"snapshot is stale: {age.total_seconds() / 3600:.1f} h old, limit {max_age_hours:g} h")
    if data.get("fields") != FIELDS:
        raise Malformed("snapshot fields do not match Status / Execution / Agent")
    rows = [_check_row(r) for r in _list(data.get("issues"), "snapshot issues")]
    numbers = [r["number"] for r in rows]
    if len(set(numbers)) != len(numbers):
        raise Malformed("snapshot lists an issue twice")
    if numbers != sorted(numbers):
        raise Malformed("snapshot issues are not sorted by number")
    counts = _obj(data.get("counts"), "snapshot counts")
    in_project = sum(1 for r in rows if r["in_project"])
    expected = {"issues": len(rows), "in_project": in_project, "open_not_in_project": len(rows) - in_project}
    if any(counts.get(key) != value for key, value in expected.items()):
        raise Malformed("snapshot counts do not match its issues")
    return generated


_PLANNING_ROW_KEYS = frozenset({"number", "state", "project_item_id", "archived",
                                "labels", "parent", *PLANNING_FIELDS})


def _check_planning_row(raw: Any) -> dict[str, Any]:
    row = _obj(raw, "planning issue")
    if set(row) != _PLANNING_ROW_KEYS:
        raise Malformed(f"planning issue has keys {sorted(row)}, expected {sorted(_PLANNING_ROW_KEYS)}")
    number = _int(row["number"], "planning issue number")
    what = f"planning issue #{number}"
    if number < 1 or row["state"] not in ("OPEN", "CLOSED"):
        raise Malformed(f"{what} has an invalid number or state")
    _str(row["project_item_id"], f"{what} project_item_id")
    _bool(row["archived"], f"{what} archived")
    for key, (_, data_type) in PLANNING_FIELDS.items():
        value = row[key]
        if value is None:
            continue
        if data_type == "NUMBER":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise Malformed(f"{what} {key} is not a finite number")
        else:
            _str(value, f"{what} {key}")
    labels = _list(row["labels"], f"{what} labels")
    if any(not isinstance(label, str) or not label for label in labels) or labels != sorted(set(labels)):
        raise Malformed(f"{what} labels are not sorted unique names")
    if row["parent"] is not None and not re.fullmatch(r"[^\s#]+/[^\s#]+#[0-9]+", _str(row["parent"], f"{what} parent")):
        raise Malformed(f"{what} parent is not owner/repo#number")
    return row


def check_planning(snapshot: Any, *, now: datetime, max_age_hours: float = MAX_AGE_HOURS) -> datetime:
    """Raise unless the planning snapshot is complete, for Project #3 and fresh."""
    data = _obj(snapshot, "planning snapshot")
    if data.get("schema") != PLANNING_SCHEMA:
        raise Malformed(f"unknown planning snapshot schema {data.get('schema')!r}")
    if data.get("complete") is not True:
        raise ReadinessError("planning snapshot is not marked complete")
    project = _obj(data.get("project"), "planning snapshot project")
    if (project.get("id"), project.get("number"), project.get("owner")) != (PROJECT_ID, PROJECT_NUMBER, OWNER) \
            or data.get("repository") != REPOSITORY:
        raise ReadinessError("planning snapshot is not for Project #3 / Fooftilly/PRKS")
    generated = _parse_time(data.get("generated_at"), "generated_at")
    if generated - now > MAX_FUTURE_SKEW:
        raise ReadinessError("planning snapshot is dated in the future")
    age = now - generated
    if age > timedelta(hours=max_age_hours):
        raise ReadinessError(f"planning snapshot is stale: {age.total_seconds() / 3600:.1f} h old, limit {max_age_hours:g} h")
    if data.get("fields") != {key: name for key, (name, _) in PLANNING_FIELDS.items()}:
        raise Malformed("planning snapshot fields do not match")
    rows = [_check_planning_row(r) for r in _list(data.get("issues"), "planning issues")]
    numbers = [r["number"] for r in rows]
    if numbers != sorted(set(numbers)):
        raise Malformed("planning issues are not sorted and unique")
    if _obj(data.get("counts"), "planning counts").get("issues") != len(rows):
        raise Malformed("planning counts do not match its issues")
    return generated


ARTIFACT_FILE = "project3-readiness.json"
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def decode_download(raw: bytes, name: str = ARTIFACT_FILE) -> Any:
    """Decode a downloaded artifact by its content, not its name: the raw
    JSON file (``archive: false``) or a ZIP holding exactly that one file.
    Anything else is refused."""
    if raw.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                members = archive.infolist()
                if [m.filename for m in members] != [name]:
                    raise Malformed(f"artifact ZIP must hold only {name}")
                if members[0].file_size > MAX_SNAPSHOT_BYTES:
                    raise Malformed("artifact file is too large")
                raw = archive.read(members[0])
        except zipfile.BadZipFile:
            raise Malformed("artifact is a damaged ZIP") from None
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise Malformed("artifact file is too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Malformed("artifact is neither the snapshot JSON nor a ZIP holding it") from None


_ROW_KEYS = frozenset({"number", "state", "in_project", "project_item_id", "archived", "assignees", *FIELDS})


def _check_row(raw: Any) -> dict[str, Any]:
    """One issue row must be exactly as ``build_snapshot`` writes it, so a
    truncated or hand-edited row is never read as authoritative."""
    row = _obj(raw, "snapshot issue")
    if set(row) != _ROW_KEYS:
        raise Malformed(f"snapshot issue has keys {sorted(row)}, expected {sorted(_ROW_KEYS)}")
    number = _int(row["number"], "snapshot issue number")
    what = f"snapshot issue #{number}"
    if number < 1:
        raise Malformed(f"{what} has an invalid number")
    if row["state"] not in ("OPEN", "CLOSED"):
        raise Malformed(f"{what} has an invalid state")
    for key in FIELDS:
        if row[key] is not None:
            _str(row[key], f"{what} {key}")
    assignees = _list(row["assignees"], f"{what} assignees")
    for login in assignees:
        _str(login, f"{what} assignee")
    if assignees != sorted(set(assignees)):
        raise Malformed(f"{what} assignees are not sorted and unique")
    if _bool(row["in_project"], f"{what} in_project"):
        _str(row["project_item_id"], f"{what} project_item_id")
        _bool(row["archived"], f"{what} archived")
    else:
        if row["state"] != "OPEN":
            raise Malformed(f"{what} is closed and not on Project #3, so it cannot be listed")
        if any(row[key] is not None for key in ("project_item_id", "archived", *FIELDS)):
            raise Malformed(f"{what} is not on Project #3 but has Project values")
    return row


# --- output -------------------------------------------------------------------

def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".project3-readiness-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _allowed_roots() -> tuple[Path, ...]:
    return tuple({Path(tempfile.gettempdir()).resolve(), Path.home().resolve()})


def _confined(value: str, what: str, suffix: Optional[str] = None) -> Path:
    """Keep every file this tool reads or writes in the home or temp
    directory (the runner's temp and step-summary files are under its home)
    and out of this repository, so an argument cannot point it at an
    arbitrary file and a snapshot is never written into the checkout."""
    path = Path(value).expanduser().resolve()
    if path == _REPO_ROOT or path.is_relative_to(_REPO_ROOT):
        raise ReadinessError(f"{what} must be outside the repository (snapshots are never committed)")
    roots = _allowed_roots()
    if not any(path == root or path.is_relative_to(root) for root in roots):
        raise ReadinessError(f"{what} must be under {' or '.join(str(r) for r in roots)}")
    if suffix and path.suffix != suffix:
        raise ReadinessError(f"{what} must be a {suffix} file")
    return path


def summary_markdown(snapshot: dict[str, Any]) -> str:
    counts = snapshot["counts"]
    statuses = Counter(r["status"] or "(unset)" for r in snapshot["issues"] if r["in_project"])
    lines = [
        "## Project #3 readiness snapshot",
        "",
        f"Generated {snapshot['generated_at']}, stale after {snapshot['stale_after']}. "
        "The JSON artifact is the output; this summary is for people only.",
        "",
        f"- Issues: {counts['issues']} ({counts['in_project']} on Project #3, "
        f"{counts['open_not_in_project']} open and not on it)",
        f"- Skipped items: {json.dumps(counts['skipped_items'], sort_keys=True)}",
        "",
        "| Status | Issues |",
        "|---|---|",
        *(f"| {name} | {count} |" for name, count in sorted(statuses.items())),
        "",
    ]
    return "\n".join(lines)


def planning_summary(snapshot: dict[str, Any]) -> str:
    ranked = sum(1 for r in snapshot["issues"] if r["implementation_order"] is not None)
    return "\n".join([
        "## Project #3 planning snapshot",
        "",
        f"Generated {snapshot['generated_at']}, stale after {snapshot['stale_after']}.",
        "",
        f"- Issue items: {snapshot['counts']['issues']} ({ranked} with Implementation Order)",
        f"- Skipped items: {json.dumps(snapshot['counts']['skipped_items'], sort_keys=True)}",
        "",
    ])


def _source(env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    if env.get("GITHUB_REPOSITORY") != REPOSITORY or not env.get("GITHUB_RUN_ID", "").isdigit():
        return None
    return {"workflow_run_id": int(env["GITHUB_RUN_ID"]), "run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
            "sha": env.get("GITHUB_SHA"), "event": env.get("GITHUB_EVENT_NAME")}


def main(argv: Optional[Sequence[str]] = None, env: Optional[Mapping[str, str]] = None,
         transport_factory: Callable[[str], Transport] = http_transport,
         now: Optional[Callable[[], datetime]] = None) -> int:
    env = os.environ if env is None else env
    clock = now or (lambda: datetime.now(timezone.utc))
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="write a snapshot (read-only)")
    snap.add_argument("--out", required=True, help="output .json path, outside the repository")
    snap.add_argument("--summary", help="append a Markdown summary to this file (job summary)")
    chk = sub.add_parser("check", help="validate a snapshot and its freshness")
    chk.add_argument("--snapshot", required=True, help="file under home or temp, or - for standard input")
    chk.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    plan = sub.add_parser("planning", help="write a planning snapshot (read-only)")
    plan.add_argument("--out", required=True, help="output .json path, outside the repository")
    plan.add_argument("--summary", help="append a Markdown summary to this file (job summary)")
    plan_chk = sub.add_parser("check-planning", help="validate a planning snapshot and its freshness")
    plan_chk.add_argument("--snapshot", required=True, help="file under home or temp, or - for standard input")
    plan_chk.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    args = parser.parse_args(argv)
    try:
        if args.command in ("check", "check-planning"):
            if args.snapshot == "-":
                # Standard input reads a downloaded file from anywhere (for
                # example /mnt/data) without widening the path confinement.
                raw = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
            else:
                raw = _confined(args.snapshot, "--snapshot").read_bytes()
            if args.command == "check":
                checker, data = check_snapshot, decode_download(raw)
            else:
                checker, data = check_planning, decode_download(raw, PLANNING_ARTIFACT_FILE)
            generated = checker(data, now=clock(), max_age_hours=args.max_age_hours)
            print(f"ok: snapshot generated {_iso(generated)} is complete and fresh")
            return 0
        out = _confined(args.out, "--out", ".json")
        summary = _confined(args.summary, "--summary") if args.summary else None
        if summary == out:
            raise ReadinessError("--summary must be a different file from --out")
        token = env.get("PROJECT3_READINESS_TOKEN", "")
        if not token:
            raise NotConfigured("PROJECT3_READINESS_TOKEN is not set; nothing was read or written")
        build = build_snapshot if args.command == "snapshot" else build_planning
        snapshot = build(transport_factory(token), generated_at=clock(), source=_source(env))
        _write_atomic(out, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(summary_markdown(snapshot) if args.command == "snapshot"
                             else planning_summary(snapshot))
        print(f"ok: wrote {snapshot['counts']['issues']} issues to {out}")
        return 0
    except Confidential as error:
        print(f"refused: {error}", file=sys.stderr)
        return 3
    except NotConfigured as error:
        print(f"not-configured: {error}", file=sys.stderr)
        return 2
    except (ReadinessError, OSError, ValueError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
