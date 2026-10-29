#!/usr/bin/env python3
"""Authenticated issue-comment bridge for reviewed Project #3 assignments.

Only a newly created command comment on issue #982, authored by the Fooftilly
account through the verified ChatGPT Codex Connector GitHub App, can submit a
batch. GitHub's live REST comment record is authoritative; webhook text alone
cannot authorize a write. Default mode is a no-write probe.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any
import urllib.error
import urllib.request

import project3_migrate as pm
import project3_triage as triage

INBOX = 982
OWNER_ID = 46610174
OWNER_LOGIN = "Fooftilly"
CONNECTOR_APP_ID = 1144995
PREFIX = "PRKS_PROJECT3_ASSIGN_V1\n"
REPO = "Fooftilly/PRKS"
API = "https://api.github.com/repos/Fooftilly/PRKS"
MAX_BODY = 12000
MAX_BATCH = 10
MAX_AGE_SECONDS = 7200
CLAIM = "<!-- prks-project3-assignment-claim:{} -->"


class BridgeError(Exception):
    """Refused request; never override safeguards to make it succeed."""


class GitHubAPI:
    """Fixed-scope REST requests; no API URLs are accepted from the comment."""

    def __init__(self, token: str) -> None:
        if not token:
            raise BridgeError("GitHub Actions token is unavailable")
        self.token = token
        self.opener = pm._https_only_opener()

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        if (not path.startswith(API + "/") or "://" in path[len(API):]
                or method not in ("GET", "POST", "PATCH")):
            raise BridgeError("refused GitHub API path or method")
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "prks-project3-command-bridge",
        }
        req = urllib.request.Request(path, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as error:
            raise BridgeError(f"GitHub REST {method} failed: {type(error).__name__}") from None

    def comment(self, comment_id: int) -> dict:
        return self.request("GET", f"{API}/issues/comments/{comment_id}")

    def issue(self) -> dict:
        return self.request("GET", f"{API}/issues/{INBOX}")

    def has_claim(self, comment_id: int) -> bool:
        marker = CLAIM.format(comment_id)
        for page in range(1, 51):
            comments = self.request(
                "GET", f"{API}/issues/{INBOX}/comments?per_page=100&page={page}")
            if not isinstance(comments, list) or len(comments) > 100:
                raise BridgeError("command inbox comments could not be enumerated")
            for comment in comments:
                if (comment.get("user", {}).get("login") == "github-actions[bot]"
                        and isinstance(comment.get("body"), str)
                        and comment["body"].startswith(marker)):
                    return True
            if len(comments) < 100:
                return False
        raise BridgeError("command inbox history exceeds the supported replay bound")

    def claim(self, comment_id: int, run_url: str) -> int:
        record = self.request("POST", f"{API}/issues/{INBOX}/comments", {
            "body": CLAIM.format(comment_id) + "\nAssignment request claimed for processing. "
                    + "Run: " + run_url + "\nNo outcome confirmed yet."
        })
        if not isinstance(record, dict) or type(record.get("id")) is not int:
            raise BridgeError("claim response malformed; refusing Project mutations")
        return record["id"]

    def receipt(self, claim_id: int, comment_id: int, result: str) -> None:
        self.request("PATCH", f"{API}/issues/comments/{claim_id}", {
            "body": CLAIM.format(comment_id) + "\n" + result
        })


def _utc(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return result.astimezone(timezone.utc)
    except (AttributeError, ValueError):
        raise BridgeError("comment timestamp is invalid") from None


def authenticate(event: dict, comment: dict, inbox: dict, now: datetime) -> int:
    """All identity and freshness requirements are checked before any token write."""
    if (not isinstance(event, dict) or event.get("action") != "created"
            or event.get("repository", {}).get("full_name") != REPO
            or event.get("issue", {}).get("number") != INBOX
            or "pull_request" in event["issue"]):
        raise BridgeError("event is not a new comment on the assignment inbox")
    if inbox.get("number") != INBOX or inbox.get("state") != "open":
        raise BridgeError("assignment inbox is not open")
    original = event.get("comment")
    if not isinstance(original, dict) or type(original.get("id")) is not int:
        raise BridgeError("event comment identity missing")
    cid = original["id"]
    if (comment.get("id") != cid
            or comment.get("url") != f"{API}/issues/comments/{cid}"
            or comment.get("issue_url") != f"{API}/issues/{INBOX}"
            or comment.get("body") != original.get("body")):
        raise BridgeError("REST comment differs from the GitHub event")
    user = comment.get("user") or {}
    app = comment.get("performed_via_github_app") or {}
    if (user.get("id") != OWNER_ID or user.get("login") != OWNER_LOGIN
            or app.get("id") != CONNECTOR_APP_ID):
        raise BridgeError("command author / originating GitHub App not authorized")
    created = _utc(comment.get("created_at"))
    updated = _utc(comment.get("updated_at"))
    age = (now.astimezone(timezone.utc) - created).total_seconds()
    if created != updated or not 0 <= age <= MAX_AGE_SECONDS:
        raise BridgeError("comment edited, in the future, or too old to execute")
    return cid


def parse(body: Any) -> tuple[str, list[dict], list[bool]]:
    if not isinstance(body, str) or not body.startswith(PREFIX) or len(body) > MAX_BODY:
        raise BridgeError("missing or malformed command marker")
    try:
        command = json.loads(body[len(PREFIX):])
    except (ValueError, TypeError):
        raise BridgeError("invalid assignment command JSON") from None
    if (not isinstance(command, dict)
            or set(command) != {"version", "operation", "items"}
            or type(command["version"]) is not int or command["version"] != 1
            or command["operation"] not in ("check", "apply")
            or not isinstance(command["items"], list)
            or not 1 <= len(command["items"]) <= MAX_BATCH):
        raise BridgeError("invalid command version, operation or batch size")
    rows: list[dict] = []
    reassign: list[bool] = []
    for original in command["items"]:
        if not isinstance(original, dict):
            raise BridgeError("invalid issue command row")
        extras = set(original) - {"number", "set", "reason", "roadmap_checked",
                                   "overlap_checked", "allow_reassignment"}
        if extras or "allow_reassignment" in original and type(original["allow_reassignment"]) is not bool:
            raise BridgeError("unexpected command row key or reassignment flag")
        reassign.append(original.get("allow_reassignment", False))
        row = {key: value for key, value in original.items() if key != "allow_reassignment"}
        try:
            triage.validate_row(row, plan=False)
        except triage.TriageError as exc:
            raise BridgeError(str(exc)) from None
        rows.append(row)
    if len({r["number"] for r in rows}) != len(rows):
        raise BridgeError("duplicate issue number")
    return command["operation"], rows, reassign


def prepare(rows: list[dict], reassign: list[bool], project: pm.Project,
            read: triage.Callable[[str], Any]) -> dict:
    for row, allowed in zip(rows, reassign, strict=True):
        current = triage.project_live(project, row["number"])
        old = current["values"].get("Agent")
        new = row["set"].get("Agent", old)
        if old not in (None, "Unassigned", new) and not allowed:
            raise BridgeError(f"#{row['number']}: changing a different agent requires allow_reassignment=true")
    plan = triage.propose(rows, project, read)
    for row in plan["items"]:
        row["approved"] = True  # authenticated user explicitly authorized this exact command
    triage.preflight(plan, project, read)
    return plan


def execute(event: dict, *, github: GitHubAPI, project_token: str,
            mode: str, now: datetime, run_url: str) -> str:
    comment_id = event.get("comment", {}).get("id")
    if type(comment_id) is not int:
        raise BridgeError("event comment ID missing")
    comment = github.comment(comment_id)
    authenticate(event, comment, github.issue(), now)
    operation, rows, reassign = parse(comment["body"])
    if github.has_claim(comment_id):
        raise BridgeError("duplicate command: prior claim exists, refusing replay")
    if not project_token:
        raise BridgeError("protected project-automation token not configured")
    transport = pm.http_transport(project_token)
    read = triage.rest_client(project_token)
    project = pm.Project(pm.GraphQL(
        transport, allow_mutations=operation == "apply" and mode == "apply"))
    plan = prepare(rows, reassign, project, read)
    # Record the claim BEFORE making any Project mutation. If the run fails
    # after this point, a rerun cannot repeat a potentially partial batch.
    receipt_id = github.claim(comment_id, run_url)
    try:
        if operation == "check" or mode != "apply":
            result = ("CHECK ONLY — no Project fields changed. " if operation == "check"
                      else "DRY RUN — bridge is not enabled; no Project fields changed. ")
            result += f"Validated {len(rows)} assigned issue command(s)."
        else:
            count = triage.apply(plan, project, read)
            result = f"APPLIED — {count} Project field update(s) verified across {len(rows)} issue(s)."
        github.receipt(receipt_id, comment_id, result)
        return result
    except Exception as exc:
        # Never claim success when a mutation or verification failed. A new,
        # explicitly authorized comment is required after inspecting drift.
        try:
            github.receipt(receipt_id, comment_id,
                           "FAILED / POSSIBLY PARTIAL — inspect current Project values before a new request. "
                           + type(exc).__name__)
        except Exception:
            pass
        raise


def main() -> int:
    try:
        if (os.environ.get("GITHUB_REPOSITORY") != REPO
                or os.environ.get("GITHUB_REF") != "refs/heads/master"):
            raise BridgeError("not the trusted default-branch repository")
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise BridgeError("GitHub event path missing")
        event = json.loads(Path(event_path).read_text("utf-8"))
        now = datetime.now(timezone.utc)
        github = GitHubAPI(os.environ.get("GITHUB_TOKEN", ""))
        project_token = os.environ.get("PROJECT_SYNC_TOKEN", "")
        mode = os.environ.get("PROJECT3_ASSIGN_BRIDGE_MODE", "")
        run_url = ("https://github.com/Fooftilly/PRKS/actions/runs/"
                   + str(os.environ.get("GITHUB_RUN_ID", "unknown")))
        print(execute(event, github=github, project_token=project_token,
                      mode=mode, now=now, run_url=run_url))
        return 0
    except (BridgeError, triage.TriageError, pm.MigrationError, OSError, ValueError, KeyError,
            TypeError) as exc:
        print(f"assignment bridge refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
