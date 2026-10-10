#!/usr/bin/env python3
"""Record one maintainer-approved native GitHub issue dependency (#441).

The only mutation this script can make is
``POST /repos/{repo}/issues/{blocked}/dependencies/blocked_by`` for exactly one
pair, and only in ``--apply`` mode after every preflight check passes against
live GitHub state. It never removes a relationship, never writes comments or
labels, and never touches pull requests or Project #3 fields.

Preflight (re-run immediately before the write in ``--apply`` mode):

- the requesting actor is on the allowlist;
- both numbers are distinct, live, open issues (not pull requests);
- the relationship is not already present (present means a no-op);
- adding it would not create a cycle: the blocking issue must not already be
  (transitively) blocked by the blocked issue. A graph too large to check is
  refused rather than assumed acyclic.

Every run prints one JSON audit record (actor, mode, pair, justification,
edges before and after, outcome) and appends a Markdown summary to
``GITHUB_STEP_SUMMARY`` when that is set.

Exit status: 0 for a dry run that would write, a no-op, or a verified write;
1 for a refused request; 2 for a GitHub API failure or an unverified write.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

API_VERSION = "2022-11-28"
# Upper bound on issues visited by the cycle check. Exceeding it refuses the
# request: an unchecked graph is never assumed to be acyclic.
MAX_GRAPH_NODES = 500
MAX_JUSTIFICATION = 1000
MIN_JUSTIFICATION = 10

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_NUMBER_RE = re.compile(r"^[1-9][0-9]{0,9}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")


class Refused(Exception):
    """The request is invalid or unsafe; nothing was written."""


class ApiError(Exception):
    def __init__(self, status: int, method: str, path: str, detail: str = "") -> None:
        super().__init__(f"{method} {path} returned HTTP {status}{': ' + detail if detail else ''}")
        self.status = status


class Client(Protocol):
    def get(self, path: str) -> Any: ...

    def post(self, path: str, body: dict[str, Any]) -> Any: ...


def _error_message(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read() or b"{}")
    except (ValueError, OSError, http.client.HTTPException):
        # A truncated or timed-out error body still reports the status.
        return ""
    return str(payload.get("message", ""))[:200] if isinstance(payload, dict) else ""


class GitHubClient:
    """Minimal REST client. ``path`` is relative to the API root."""

    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        if not api_url.startswith("https://"):
            raise ApiError(0, "INIT", api_url, "the API URL must use https")
        self._token = token
        self._api_url = api_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self._api_url}/{path.lstrip('/')}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "prks-issue-dependency-writer",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            # The URL is always https (checked in __init__).
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, method, path, _error_message(error)) from None
        except urllib.error.URLError as error:
            raise ApiError(0, method, path, str(error.reason)[:200]) from None
        except (OSError, http.client.HTTPException) as error:
            # Read timeouts, connection resets and truncated reads.
            raise ApiError(0, method, path, f"{type(error).__name__}: {str(error)[:200]}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(0, method, path, "the response is not valid JSON") from None

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("POST", path, body)


@dataclass(frozen=True)
class IssueRef:
    repo: str
    number: int
    id: int

    def label(self, home_repo: str) -> str:
        return f"#{self.number}" if self.repo == home_repo else f"{self.repo}#{self.number}"


@dataclass
class Request:
    repo: str
    blocked: int
    blocking: int
    justification: str
    actor: str
    triggering_actor: str
    allowed_actors: frozenset[str]
    apply: bool


@dataclass
class Audit:
    request: Request
    outcome: str = "pending"
    reason: str = ""
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        r = self.request
        return {
            "tool": "issue-dependency-writer",
            "repository": r.repo,
            "actor": r.actor,
            "triggering_actor": r.triggering_actor,
            "mode": "apply" if r.apply else "dry-run",
            "blocked": r.blocked,
            "blocking": r.blocking,
            "justification": r.justification,
            "blocked_by_before": self.before,
            "blocked_by_after": self.after,
            "outcome": self.outcome,
            "reason": self.reason,
        }

    def summary_markdown(self) -> str:
        r = self.request
        lines = [
            "## Issue dependency writer",
            "",
            f"- Request: #{r.blocked} blocked by #{r.blocking}",
            f"- Mode: {'apply' if r.apply else 'dry-run'}",
            f"- Actor: `{r.actor}` (triggered by `{r.triggering_actor}`)",
            f"- Outcome: **{self.outcome}**" + (f": {self.reason}" if self.reason else ""),
            f"- #{r.blocked} blocked by, before: {', '.join(self.before) or 'none'}",
            f"- #{r.blocked} blocked by, after: {', '.join(self.after) or 'none'}",
            "",
            "Justification:",
            "",
            "```text",
            r.justification.replace("```", "'''"),
            "```",
            "",
        ]
        return "\n".join(lines)


def parse_number(value: str, name: str) -> int:
    text = value.strip().lstrip("#")
    if not _NUMBER_RE.match(text):
        raise Refused(f"{name} must be an issue number, got {value!r}")
    return int(text)


def parse_actors(value: str, default: str) -> frozenset[str]:
    names = [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    if not names:
        names = [default]
    for name in names:
        if not _ACTOR_RE.match(name):
            raise Refused(f"invalid allowed actor {name!r}")
    return frozenset(name.lower() for name in names)


def validate(request: Request) -> None:
    if not _REPO_RE.match(request.repo):
        raise Refused(f"invalid repository {request.repo!r}")
    for who in (request.actor, request.triggering_actor):
        if who.lower() not in request.allowed_actors:
            raise Refused(f"actor {who!r} is not allowed to write issue dependencies")
    if request.blocked == request.blocking:
        raise Refused("an issue cannot be blocked by itself")
    length = len(request.justification)
    if length < MIN_JUSTIFICATION or length > MAX_JUSTIFICATION:
        raise Refused(
            f"justification must be {MIN_JUSTIFICATION}-{MAX_JUSTIFICATION} characters, got {length}"
        )


def _repo_from_url(url: str) -> str:
    match = re.search(r"/repos/([^/]+/[^/]+)$", url or "")
    if not match:
        raise ApiError(0, "GET", url, "unexpected repository_url in dependency listing")
    return match.group(1)


def _ref(issue: dict[str, Any], fallback_repo: str) -> IssueRef:
    repo_url = issue.get("repository_url")
    repo = _repo_from_url(repo_url) if repo_url else fallback_repo
    return IssueRef(repo=repo, number=int(issue["number"]), id=int(issue["id"]))


def fetch_open_issue(client: Client, repo: str, number: int, role: str) -> IssueRef:
    try:
        issue = client.get(f"repos/{repo}/issues/{number}")
    except ApiError as error:
        if error.status in (404, 410):
            raise Refused(f"{role} #{number} does not exist in {repo}") from None
        raise
    if issue.get("pull_request") is not None:
        raise Refused(f"{role} #{number} is a pull request, not an issue")
    if issue.get("state") != "open":
        raise Refused(f"{role} #{number} is {issue.get('state')}; only open issues are linked")
    ref = _ref(issue, repo)
    # A transferred issue answers with a redirect to its new home. Writing
    # against that number in this repository would hit an unrelated issue.
    if ref.repo.lower() != repo.lower() or ref.number != number:
        raise Refused(f"{role} #{number} was transferred to {ref.repo}#{ref.number}")
    return ref


def _edges(client: Client, ref: IssueRef, direction: str) -> list[IssueRef]:
    edges: list[IssueRef] = []
    page = 1
    while True:
        batch = client.get(
            f"repos/{ref.repo}/issues/{ref.number}/dependencies/{direction}?per_page=100&page={page}"
        )
        if not isinstance(batch, list):
            raise ApiError(0, "GET", f"{ref.repo}#{ref.number} {direction}", "expected a list")
        edges.extend(_ref(item, ref.repo) for item in batch)
        if len(batch) < 100:
            return edges
        page += 1


def blocked_by(client: Client, ref: IssueRef) -> list[IssueRef]:
    return _edges(client, ref, "blocked_by")


def blocking(client: Client, ref: IssueRef) -> list[IssueRef]:
    return _edges(client, ref, "blocking")


def find_cycle(client: Client, blocked: IssueRef, blocker: IssueRef) -> list[IssueRef] | None:
    """Return the path blocker -> ... -> blocked along blocked-by edges, if any.

    Adding "blocked is blocked by blocker" closes a cycle exactly when blocker
    is already (transitively) blocked by blocked.
    """
    parents: dict[int, IssueRef | None] = {blocker.id: None}
    queue: deque[IssueRef] = deque([blocker])
    while queue:
        current = queue.popleft()
        for nxt in blocked_by(client, current):
            if nxt.id in parents:
                continue
            parents[nxt.id] = current
            if nxt.id == blocked.id:
                path = [nxt]
                step: IssueRef | None = current
                while step is not None:
                    path.append(step)
                    step = parents[step.id]
                return list(reversed(path))
            if len(parents) > MAX_GRAPH_NODES:
                raise Refused(
                    f"dependency graph above #{blocker.number} exceeds {MAX_GRAPH_NODES} issues; "
                    "refusing rather than assuming it is acyclic"
                )
            queue.append(nxt)
    return None


@dataclass
class Plan:
    blocked: IssueRef
    blocker: IssueRef
    existing: list[IssueRef]
    already_present: bool


def preflight(client: Client, request: Request, audit: Audit | None = None) -> Plan:
    blocked_ref = fetch_open_issue(client, request.repo, request.blocked, "blocked issue")
    blocker_ref = fetch_open_issue(client, request.repo, request.blocking, "blocking issue")
    existing = blocked_by(client, blocked_ref)
    if audit is not None:
        # Recorded before the cycle check, so a refusal still shows them.
        audit.before = audit.after = [ref.label(request.repo) for ref in existing]
    if any(edge.id == blocker_ref.id for edge in existing):
        return Plan(blocked_ref, blocker_ref, existing, already_present=True)
    cycle = find_cycle(client, blocked_ref, blocker_ref)
    if cycle is not None:
        chain = " blocked by ".join(ref.label(request.repo) for ref in cycle)
        raise Refused(f"adding it would create a cycle: {chain}")
    return Plan(blocked_ref, blocker_ref, existing, already_present=False)


def _finish(audit: Audit, outcome: str, reason: str, after: list[str], status: int) -> int:
    audit.outcome, audit.reason, audit.after = outcome, reason, after
    return status


def _write(client: Client, request: Request, audit: Audit) -> int:
    # Re-check live state immediately before the write. The workflow also
    # serializes runs, so no other writer of this tool races the check.
    plan = preflight(client, request)
    if plan.already_present:
        labels = [ref.label(request.repo) for ref in plan.existing]
        return _finish(audit, "no-op", "relationship appeared before the write", labels, 0)
    write_error: ApiError | None = None
    try:
        client.post(
            f"repos/{plan.blocked.repo}/issues/{plan.blocked.number}/dependencies/blocked_by",
            {"issue_id": plan.blocker.id},
        )
    except ApiError as error:
        # A concurrent identical write may have won; the re-read decides.
        write_error = error
    after = blocked_by(client, plan.blocked)
    labels = [ref.label(request.repo) for ref in after]
    present = any(edge.id == plan.blocker.id for edge in after)
    mirrored = any(edge.id == plan.blocked.id for edge in blocking(client, plan.blocker))
    if not (present and mirrored):
        reason = str(write_error) if write_error else "write accepted but the edge is not visible on both issues"
        return _finish(audit, "failed", reason, labels, 2)
    if write_error is None:
        return _finish(audit, "written", "", labels, 0)
    return _finish(audit, "converged", f"write returned an error but the edge is present ({write_error})", labels, 0)


def run(client: Client, request: Request, audit: Audit) -> int:
    try:
        validate(request)
        plan = preflight(client, request, audit)
        if plan.already_present:
            return _finish(audit, "no-op", "relationship already present", audit.before, 0)
        if not request.apply:
            proposed = audit.before + [plan.blocker.label(request.repo)]
            return _finish(audit, "dry-run", "would add the relationship; nothing was written", proposed, 0)
        return _write(client, request, audit)
    except Refused as refusal:
        return _finish(audit, "refused", str(refusal), audit.after, 1)
    except ApiError as error:
        return _finish(audit, "failed", str(error), audit.after, 2)


def build_request(args: argparse.Namespace, env: dict[str, str]) -> Request:
    repo = args.repo or env.get("GITHUB_REPOSITORY", "")
    owner = repo.split("/", 1)[0]
    actor = args.actor or env.get("GITHUB_ACTOR", "")
    return Request(
        repo=repo,
        blocked=parse_number(args.blocked, "blocked"),
        blocking=parse_number(args.blocking, "blocking"),
        justification=(args.justification or "").strip(),
        actor=actor,
        # A re-run keeps the original actor; the person who re-ran it must be
        # allowed too.
        triggering_actor=args.triggering_actor or env.get("GITHUB_TRIGGERING_ACTOR", "") or actor,
        allowed_actors=parse_actors(args.allowed_actors or "", owner),
        apply=args.apply,
    )


def main(argv: list[str] | None = None, env: dict[str, str] | None = None,
         client_factory: Callable[[str, str], Client] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="")
    parser.add_argument("--blocked", required=True, help="issue number that is blocked")
    parser.add_argument("--blocking", required=True, help="issue number that blocks it")
    parser.add_argument("--justification", required=True)
    parser.add_argument("--actor", default="")
    parser.add_argument("--triggering-actor", default="")
    parser.add_argument("--allowed-actors", default="", help="comma-separated; default: repository owner")
    parser.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    args = parser.parse_args(argv)

    try:
        request = build_request(args, env)
    except Refused as refusal:
        # The request could not be parsed, so there is no full audit record;
        # still report the refusal in the log and the job summary.
        print(json.dumps({"tool": "issue-dependency-writer", "outcome": "refused", "reason": str(refusal)}))
        summary_path = env.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write(
                    "## Issue dependency writer\n\n"
                    f"- Outcome: **refused**: {str(refusal).replace('`', "'")}\n"
                    "- Nothing was read or written.\n"
                )
        print(f"::error::refused: {refusal}", file=sys.stderr)
        return 1
    audit = Audit(request)
    token = env.get("GITHUB_TOKEN", "")
    if not token:
        audit.outcome, audit.reason, status = "failed", "GITHUB_TOKEN is not set", 2
    else:
        factory = client_factory or (lambda tok, url: GitHubClient(tok, url))
        try:
            client = factory(token, env.get("GITHUB_API_URL", "https://api.github.com"))
        except ApiError as error:
            audit.outcome, audit.reason, status = "failed", str(error), 2
        else:
            status = run(client, request, audit)

    print(json.dumps(audit.record(), sort_keys=True))
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(audit.summary_markdown())
    if status:
        print(f"::error::{audit.outcome}: {audit.reason}", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
