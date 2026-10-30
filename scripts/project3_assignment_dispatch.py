#!/usr/bin/env python3
"""Owner-authenticated, comment-free Project #3 assignment workflow dispatcher.

An explicit workflow_dispatch on protected master executes the existing reviewed
Project V2 triage guards. A complete expected-before snapshot makes repeated or
stale commands fail closed. No issue-comment command/receipt is written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import project3_migrate as pm
import project3_triage as triage

REPOSITORY = "Fooftilly/PRKS"
OWNER_ID = 46610174
MAX_BYTES = 12000
MAX_ITEMS = 10
FIELDS = frozenset(("Agent", "Status", "Execution"))


class DispatchError(Exception):
    """Rejected dispatch; Project #3 has not been authorized by this command."""


def authenticate(event: Any, env: Mapping[str, str]) -> str:
    """Require the immutable maintainer ID and the trusted default-branch run."""
    if (env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("GITHUB_REF") != "refs/heads/master"
            or env.get("GITHUB_RUN_ATTEMPT") != "1"):
        raise DispatchError("not a first-attempt dispatch on protected master")
    if (not isinstance(event, dict)
            or (event.get("repository") or {}).get("full_name") != REPOSITORY
            or (event.get("sender") or {}).get("id") != OWNER_ID
            or (event.get("sender") or {}).get("login") != "Fooftilly"):
        raise DispatchError("dispatch sender or repository not authorized")
    if event.get("ref") not in (None, "refs/heads/master"):
        raise DispatchError("dispatch event ref is not master")
    inputs = event.get("inputs")
    command = inputs.get("command") if isinstance(inputs, dict) else None
    if not isinstance(command, str) or len(command.encode("utf-8")) > MAX_BYTES:
        raise DispatchError("missing or oversized command input")
    return command


def parse(command: str) -> tuple[str, list[dict], list[dict], list[bool]]:
    try:
        payload = json.loads(command)
    except (ValueError, TypeError):
        raise DispatchError("invalid command JSON") from None
    if (not isinstance(payload, dict)
            or set(payload) != {"version", "operation", "items"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["operation"] not in ("check", "apply")
            or not isinstance(payload["items"], list)
            or not 1 <= len(payload["items"]) <= MAX_ITEMS):
        raise DispatchError("invalid command schema or batch size")
    rows, expected, reassign = [], [], []
    for original in payload["items"]:
        if not isinstance(original, dict):
            raise DispatchError("invalid command row")
        if set(original) - {"number", "set", "reason", "roadmap_checked",
                            "overlap_checked", "expected_before", "allow_reassignment"}:
            raise DispatchError("unexpected command row fields")
        snapshot = original.get("expected_before")
        if (not isinstance(snapshot, dict) or set(snapshot) != FIELDS
                or any(value is not None and not isinstance(value, str)
                       for value in snapshot.values())):
            raise DispatchError("expected_before must include all three Project fields")
        allow = original.get("allow_reassignment", False)
        if type(allow) is not bool:
            raise DispatchError("allow_reassignment must be boolean")
        row = {k: v for k, v in original.items()
               if k not in ("expected_before", "allow_reassignment")}
        if row.get("number") == 982:
            raise DispatchError("legacy command inbox is not an assignment target")
        try:
            triage.validate_row(row, plan=False)
        except triage.TriageError as error:
            raise DispatchError(str(error)) from None
        rows.append(row)
        expected.append(snapshot)
        reassign.append(allow)
    if len({row["number"] for row in rows}) != len(rows):
        raise DispatchError("duplicate issue number")
    return payload["operation"], rows, expected, reassign


def prepare(rows: list[dict], expected: list[dict], reassign: list[bool],
            project: pm.Project, read: triage.Callable[[str], Any]) -> dict:
    for row, before, allowed in zip(rows, expected, reassign, strict=True):
        current = triage.project_live(project, row["number"])
        values = {field: current["values"].get(field) for field in FIELDS}
        if values != before:
            raise DispatchError(f"#{row['number']}: expected_before differs from live Project")
        old = values["Agent"]
        new = row["set"].get("Agent", old)
        if old not in (None, "Unassigned", new) and not allowed:
            raise DispatchError(f"#{row['number']}: changing Agent requires allow_reassignment=true")
    plan = triage.propose(rows, project, read)
    for row in plan["items"]:
        row["approved"] = True
    triage.preflight(plan, project, read)
    return plan


def execute(command: str, *, project: pm.Project,
            read: triage.Callable[[str], Any], mode: str) -> tuple[str, int, int]:
    operation, rows, expected, reassign = parse(command)
    plan = prepare(rows, expected, reassign, project, read)
    if operation == "check":
        return "CHECK ONLY — no Project fields changed", 0, len(rows)
    if mode != "apply":
        return "DRY RUN — dispatch write mode is not enabled", 0, len(rows)
    changed = triage.apply(plan, project, read)
    return "APPLIED — Project fields verified", changed, len(rows)


def report(outcome: str, changed: int, issues: int, *, failure: bool = False) -> None:
    # Workflow-run logs and step summary are the audit record, not issue comments.
    # Never echo the event command, user-supplied reason, or either token.
    line = f"{outcome}; issues={issues}; field_updates={changed}"
    print(line, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("## Project #3 assignment dispatch\n\n")
            output.write("**FAILED / POSSIBLY PARTIAL** — inspect live fields before a new "
                         "dispatch.\n" if failure else line + "\n")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path and not failure:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")


def main() -> int:
    try:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise DispatchError("missing workflow event")
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        command = authenticate(event, os.environ)
        operation, _, _, _ = parse(command)
        project_token = os.environ.get("PROJECT_SYNC_TOKEN")
        if not project_token:
            raise DispatchError("protected Project token missing")
        actions_token = os.environ.get("GITHUB_TOKEN")
        if not actions_token:
            raise DispatchError("scoped Actions token missing")
        allow = (operation == "apply"
                 and os.environ.get("PROJECT3_ASSIGN_DISPATCH_MODE") == "apply")
        project = pm.Project(pm.GraphQL(pm.http_transport(project_token),
                                        allow_mutations=allow))
        read = triage.rest_client(actions_token)
        outcome, changed, issues = execute(
            command, project=project, read=read,
            mode=os.environ.get("PROJECT3_ASSIGN_DISPATCH_MODE", ""))
        report(outcome, changed, issues)
        return 0
    except (DispatchError, triage.TriageError, pm.MigrationError, OSError,
            ValueError, TypeError, KeyError) as error:
        print(f"assignment dispatch refused or failed: {type(error).__name__}: {error}",
              file=sys.stderr)
        report("FAILED / POSSIBLY PARTIAL", 0, 0, failure=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
