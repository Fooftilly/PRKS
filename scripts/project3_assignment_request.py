#!/usr/bin/env python3
"""Trusted-master relay for Project #3 assignment requests written by ChatGPT.

A minimal unprivileged push workflow is a signal, never an authorization.
Only a verified owner-initiated, single-file commit at the CURRENT head of the
dedicated request branch can supply a command. It is read as pinned data from
GitHub REST; no branch code or artifacts are executed or checked out.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

import project3_assignment_dispatch as dispatch
import project3_migrate as pm
import project3_triage as triage

REPO = "Fooftilly/PRKS"
API = "https://api.github.com/repos/" + REPO
BRANCH = "project3-assignment-requests"
FILE = "project3-assignment-request.json"
SIGNAL = ".github/workflows/project3-assignment-request-signal.yml"
MAX_BYTES = 12000
SHA = re.compile(r"^[0-9a-f]{40}$")


class RequestError(Exception):
    """Refused untrusted command or invalid provenance."""


def _refuse_if_untrusted_event(event: Any, env: Mapping[str, str]) -> dict:
    if (env.get("GITHUB_EVENT_NAME") != "workflow_run"
            or env.get("GITHUB_REPOSITORY") != REPO
            or env.get("GITHUB_REF") != "refs/heads/master"
            or env.get("GITHUB_RUN_ATTEMPT") != "1"):
        raise RequestError("not a first-attempt protected master workflow_run")
    if not isinstance(event, dict) or event.get("action") != "completed":
        raise RequestError("not a completed workflow-run event")
    if (event.get("repository") or {}).get("full_name") != REPO:
        raise RequestError("repository mismatch")
    run = event.get("workflow_run")
    if not isinstance(run, dict) or type(run.get("id")) is not int:
        raise RequestError("missing upstream workflow identity")
    if (run.get("event") != "push" or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("head_branch") != BRANCH
            or (run.get("head_repository") or {}).get("full_name") != REPO
            or (run.get("actor") or {}).get("id") != dispatch.OWNER_ID
            or (run.get("actor") or {}).get("login") != "Fooftilly"
            or run.get("run_attempt") != 1):
        raise RequestError("unauthorized or unsuccessful request signal")
    path = run.get("path")
    if not isinstance(path, str) or path.split("@", 1)[0] != SIGNAL:
        raise RequestError("unexpected signal workflow path")
    sha = run.get("head_sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise RequestError("invalid upstream commit")
    return run



def provenance_client(token: str) -> Callable[[str], Any]:
    """Read only the four fixed GitHub REST resource families used for proof."""
    opener = pm._https_only_opener()
    prefixes = (
        API + "/actions/runs/",
        API + "/branches/" + BRANCH,
        API + "/commits/",
        API + "/contents/" + FILE + "?ref=",
    )

    def read(url: str) -> Any:
        if not any(url.startswith(prefix) for prefix in prefixes):
            raise RequestError("refused request outside fixed provenance resources")
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "prks-project3-assignment-request",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with opener.open(request, timeout=30) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise RequestError("GitHub request provenance read failed: "
                               + type(error).__name__) from None
        if not isinstance(value, dict):
            raise RequestError("malformed GitHub provenance response")
        return value

    return read


def load_command(event: Any, env: Mapping[str, str],
                 read: Callable[[str], Any]) -> tuple[str, str]:
    run = _refuse_if_untrusted_event(event, env)
    upstream = read(API + "/actions/runs/" + str(run["id"]))
    for field in ("id", "event", "status", "conclusion", "head_branch",
                  "head_sha", "path", "run_attempt"):
        if upstream.get(field) != run[field]:
            raise RequestError("upstream workflow REST record differs from event")
    if ((upstream.get("actor") or {}).get("id") != dispatch.OWNER_ID
            or (upstream.get("head_repository") or {}).get("full_name") != REPO):
        raise RequestError("upstream workflow owner/repository mismatch")

    sha = run["head_sha"]
    # Only process the live branch tip, not an old run after a newer request.
    current = read(API + "/branches/" + BRANCH)
    if (current.get("commit") or {}).get("sha") != sha:
        raise RequestError("request is no longer the current branch tip")
    commit = read(API + "/commits/" + sha)
    changed = commit.get("files")
    if (commit.get("sha") != sha or not isinstance(commit.get("parents"), list)
            or len(commit["parents"]) != 1 or not isinstance(changed, list)
            or len(changed) != 1 or changed[0].get("filename") != FILE
            or changed[0].get("status") != "modified"):
        raise RequestError("request commit must modify only the existing request file")
    source = read(API + "/contents/" + FILE + "?ref=" + sha)
    if (source.get("type") != "file" or type(source.get("size")) is not int
            or not 1 <= source["size"] <= MAX_BYTES
            or source.get("encoding") != "base64"
            or not isinstance(source.get("content"), str)):
        raise RequestError("missing or oversized assignment request file")
    try:
        raw = base64.b64decode("".join(source["content"].split()), validate=True)
        command = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise RequestError("invalid request file encoding") from None
    if len(raw) != source["size"] or len(raw) > MAX_BYTES:
        raise RequestError("request file size mismatch")
    # Full content must parse under the same bounded, reviewed Project writer.
    dispatch.parse(command)
    return command, sha


def main() -> int:
    try:
        path = os.environ.get("GITHUB_EVENT_PATH")
        if not path:
            raise RequestError("missing workflow event")
        # Refuse untrusted event before making a token-backed REST request.
        event = json.loads(Path(path).read_text("utf-8"))
        _refuse_if_untrusted_event(event, os.environ)
        actions_token = os.environ.get("GITHUB_TOKEN")
        project_token = os.environ.get("PROJECT_SYNC_TOKEN")
        if not actions_token or not project_token:
            raise RequestError("scoped Actions/Project token missing")
        command, sha = load_command(event, os.environ,
                                    provenance_client(actions_token))
        issue_read = triage.rest_client(actions_token)
        operation, _, _, _ = dispatch.parse(command)
        mode = os.environ.get("PROJECT3_ASSIGN_DISPATCH_MODE", "")
        gql = pm.GraphQL(pm.http_transport(project_token),
                         allow_mutations=(operation == "apply" and mode == "apply"))
        outcome, changed, issues = dispatch.execute(
            command, project=pm.Project(gql), read=issue_read, mode=mode)
        print("Validated assignment request commit " + sha[:12], flush=True)
        dispatch.report(outcome, changed, issues)
        return 0
    except (RequestError, dispatch.DispatchError, triage.TriageError,
            pm.MigrationError, OSError, ValueError, KeyError, TypeError) as error:
        # A failed triage apply might have written some Project fields.
        # The workflow refreshes the read-only queue even on this failure.
        print("assignment request refused or failed: "
              + type(error).__name__ + ": " + str(error), file=sys.stderr)
        dispatch.report("FAILED / POSSIBLY PARTIAL", 0, 0, failure=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
