#!/usr/bin/env python3
"""Publish and consume a bounded, public, read-only Project #3 agent ownership queue.

The protected publisher reads the complete Project #3 item connection directly,
while the legacy `publish` command still accepts validated readiness snapshots.
Consumers never need the project-scoped PAT. This is an ownership inventory,
NOT authorization to start work; verify live issue state, blockers and overlap.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import project3_readiness as readiness

SCHEMA = "prks-project3-agent-queue/v1"
MAX_AGE_HOURS = 12
MAX_FILE_BYTES = 16 * 1024 * 1024
AGENTS = {"cursor cloud": "Cursor Cloud", "cursor": "Cursor Cloud",
          "claude code": "Claude Code", "claude": "Claude Code",
          "chatgpt codex": "ChatGPT/Codex", "chatgpt": "ChatGPT/Codex",
          "codex": "ChatGPT/Codex"}
ROW_KEYS = {"number", "agent", "status", "execution"}

# A public Project can include zero readable PRKS items even when PRKS itself
# has become private. Check repository visibility independently of item content
# without fetching or paginating unrelated repository issues.
REPOSITORY_SCOPE_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner isPrivate
  }
}
"""


def _check_public_repository(gql: readiness.ReadOnlyGraphQL) -> None:
    owner, name = readiness.REPOSITORY.split("/")
    data = gql.query(REPOSITORY_SCOPE_QUERY, {"owner": owner, "name": name})
    repo = readiness._obj(data.get("repository"), "repository")
    if repo.get("nameWithOwner") != readiness.REPOSITORY:
        raise readiness.ReadinessError("PRKS repository identity check failed")
    if readiness._bool(repo.get("isPrivate"), "repository isPrivate"):
        raise readiness.Confidential("PRKS repository is private; refusing queue publication")



def canonical_agent(name: str) -> str:
    if not isinstance(name, str):
        raise readiness.Malformed("agent must be text")
    key = re.sub(r"[\s_/-]+", " ", name.casefold()).strip()
    if key not in AGENTS:
        raise readiness.Malformed(f"unknown Project Agent option: {name!r}")
    return AGENTS[key]


def _queue_from_rows(rows: Sequence[dict[str, Any]], *, project: dict[str, Any],
                     generated_at: str, source_run_id: int | None,
                     now: datetime) -> dict[str, Any]:
    if project.get("public") is not True:
        raise readiness.Confidential("Project #3 is not public; refusing publication")
    entries = []
    for issue in rows:
        if not issue["in_project"] or issue["archived"] or issue["state"] != "OPEN":
            continue
        if issue["agent"] in (None, "Manual", "Unassigned"):
            continue
        entries.append({
            "number": issue["number"],
            "agent": canonical_agent(issue["agent"]),
            "status": issue["status"],
            "execution": issue["execution"],
        })
    entries.sort(key=lambda row: row["number"])
    generated = readiness._parse_time(generated_at, "generated_at")
    result = {
        "schema": SCHEMA,
        "complete": True,
        "repository": readiness.REPOSITORY,
        "project": {"id": readiness.PROJECT_ID, "number": 3,
                    "owner": readiness.OWNER, "public": True},
        "generated_at": generated_at,
        "stale_after": readiness._iso(generated + timedelta(hours=MAX_AGE_HOURS)),
        "source_run_id": source_run_id,
        "count": len(entries),
        "issues": entries,
    }
    check_queue(result, now=now)
    return result


def build_queue(snapshot: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    # The existing full-readiness-snapshot API remains unchanged.
    readiness.check_snapshot(snapshot, now=now)
    return _queue_from_rows(
        snapshot["issues"], project=snapshot["project"],
        generated_at=snapshot["generated_at"],
        source_run_id=(snapshot.get("source") or {}).get("workflow_run_id"),
        now=now,
    )


def build_queue_live(transport: readiness.Transport, *, now: datetime,
                     source_run_id: int | None = None) -> dict[str, Any]:
    """Read every Project item without scanning unrelated repository issues.

    Reuse the readiness module's read-only GraphQL guard, complete pagination,
    Project/field identity checks and per-item repository/privacy validation.
    Independently verify the PRKS repository remains public before and after
    reading all Project items, including when no issue content is readable.
    """
    gql = readiness.ReadOnlyGraphQL(transport)
    identity = {"owner": readiness.OWNER, "number": readiness.PROJECT_NUMBER}
    project = readiness.check_project(gql.query(readiness.PROJECT_QUERY, identity))
    _check_public_repository(gql)
    items, _skipped = readiness.read_items(gql, include_assignees=False)
    if readiness.check_project(gql.query(readiness.PROJECT_QUERY, identity)) != project:
        raise readiness.ReadinessError("Project #3 changed while it was read; rerun")
    _check_public_repository(gql)
    return _queue_from_rows(
        list(items.values()), project=project,
        generated_at=readiness._iso(now), source_run_id=source_run_id, now=now,
    )

def check_queue(queue: Any, *, now: datetime) -> datetime:
    if not isinstance(queue, dict) or set(queue) != {
        "schema", "complete", "repository", "project", "generated_at",
        "stale_after", "source_run_id", "count", "issues",
    }:
        raise readiness.Malformed("queue has missing or unexpected keys")
    if queue["schema"] != SCHEMA or queue["complete"] is not True:
        raise readiness.Malformed("unknown or incomplete agent queue")
    if queue["repository"] != readiness.REPOSITORY or queue["project"] != {
        "id": readiness.PROJECT_ID, "number": 3, "owner": readiness.OWNER, "public": True,
    }:
        raise readiness.Malformed("agent queue identity or public visibility mismatch")
    generated = readiness._parse_time(queue["generated_at"], "generated_at")
    stale_after = readiness._parse_time(queue["stale_after"], "stale_after")
    if stale_after != generated + timedelta(hours=MAX_AGE_HOURS):
        raise readiness.Malformed("agent queue freshness deadline mismatch")
    if generated - now > readiness.MAX_FUTURE_SKEW:
        raise readiness.ReadinessError("agent queue is dated in the future")
    if now - generated > timedelta(hours=MAX_AGE_HOURS):
        raise readiness.ReadinessError("agent queue is stale; direct Project access required")
    source = queue["source_run_id"]
    if source is not None and (type(source) is not int or source < 1):
        raise readiness.Malformed("source run ID is invalid")
    rows = queue["issues"]
    if not isinstance(rows, list) or type(queue["count"]) is not int or len(rows) != queue["count"]:
        raise readiness.Malformed("agent queue count mismatch")
    prev = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise readiness.Malformed("agent queue row has missing or unexpected keys")
        number = row["number"]
        if type(number) is not int or number <= prev:
            raise readiness.Malformed("agent queue issue IDs are invalid, duplicated or unsorted")
        prev = number
        if canonical_agent(row["agent"]) != row["agent"]:
            raise readiness.Malformed("agent queue option is not canonical")
        for key in ("status", "execution"):
            value = row[key]
            if value is not None and (not isinstance(value, str) or not value):
                raise readiness.Malformed(f"agent queue {key} must be text or null")
    return generated


def read_json(path: str) -> Any:
    # Consumer-supplied data may be a 404 page or a truncated/untrusted file.
    p = readiness._confined(path, "--snapshot")
    raw = p.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise readiness.Malformed("agent queue exceeds size limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise readiness.Malformed("agent queue is not UTF-8 JSON") from None


def render(queue: dict[str, Any], agent: str, *, now: datetime) -> str:
    check_queue(queue, now=now)
    role = canonical_agent(agent)
    lines = ["Issue\tStatus\tExecution\tTitle\tURL"]
    for row in queue["issues"]:
        if row["agent"] != role:
            continue
        number = row["number"]
        lines.append(f"#{number}\t{row['status'] or '(unset)'}\t"
                     f"{row['execution'] or '(unset)'}\t\t"
                     f"https://github.com/Fooftilly/PRKS/issues/{number}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None, *, now: Optional[datetime] = None,
         env: Optional[Mapping[str, str]] = None,
         transport_factory: Callable[[str], readiness.Transport] = readiness.http_transport) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    publish = sub.add_parser("publish", help="build a public queue from a validated readiness snapshot")
    publish.add_argument("--snapshot", required=True)
    publish.add_argument("--out", required=True)
    live = sub.add_parser("publish-live", help="publish directly from complete Project item pages")
    live.add_argument("--out", required=True)
    show = sub.add_parser("render", help="verify a published queue and list one agent's issues")
    show.add_argument("--snapshot", required=True)
    show.add_argument("--agent", required=True)
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    env = os.environ if env is None else env
    try:
        if args.command in ("publish", "publish-live"):
            out = readiness._confined(args.out, "--out", ".json")
            if args.command == "publish":
                source = readiness._confined(args.snapshot, "--snapshot")
                if source == out:
                    raise readiness.ReadinessError("input and output paths must differ")
                result = build_queue(read_json(args.snapshot), now=now)
            else:
                token = env.get("PROJECT3_READINESS_TOKEN", "")
                if not token:
                    raise readiness.NotConfigured("PROJECT3_READINESS_TOKEN is not set")
                source = readiness._source(env)
                result = build_queue_live(
                    transport_factory(token), now=now,
                    source_run_id=(source or {}).get("workflow_run_id"),
                )
            readiness._write_atomic(out, json.dumps(result, indent=2, sort_keys=True) + "\n")
            print(f"ok: published {result['count']} open agent-owned Project #3 issues")
        else:
            queue = read_json(args.snapshot)
            output = render(queue, args.agent, now=now)
            sys.stdout.write(output)
            print(f"Published queue generated {queue['generated_at']} UTC; "
                  "ownership only, verify live blockers before coding.", file=sys.stderr)
    except (readiness.ReadinessError, OSError, TypeError, KeyError, ValueError) as error:
        print(f"agent queue refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
