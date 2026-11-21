#!/usr/bin/env python3
"""Turn scheduled test results into GitHub issues, so nobody has to read the Actions page.

Two reports, both stdlib-only and driven by the workflow's ``GITHUB_TOKEN``:

``failure``
    The last job of a scheduled workflow. When the run failed, it opens the
    issue "Scheduled check failing: <workflow>" (label ``scheduled-check``)
    with the run link and the failed jobs, or comments on that issue when it
    is already open. When the run passed and the issue is open, it comments
    that the check passes again and closes it. One issue per workflow, never
    one per run.

``mutation``
    Mutation testing never fails on survivors, so each target keeps one
    comment, edited in place, on the standing issue "Mutation testing report"
    (label ``mutation-report``). Editing does not notify anyone. A new comment,
    which does notify, is posted only when a target has more survivors than
    its last report. The matrix legs hold no issue token: each hands its
    result to one report job as the step output ``result-<target>``, and that
    job (never run for a pull request) updates the issue. On pull requests a
    dry run checks that every target handed over a result.

Usage::

    python scripts/ci/scheduled_report.py failure --workflow NAME --run-id ID --conclusion failure
    MUTATION_OUTPUTS='{...}' python scripts/ci/scheduled_report.py mutation --run-id ID [--dry-run]
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

API_VERSION = "2022-11-28"
FAILURE_LABEL = "scheduled-check"
MUTATION_LABEL = "mutation-report"
MUTATION_TITLE = "Mutation testing report"
# Only the workflow token's own comments are state; anyone else's marker is ignored.
REPORTER_LOGIN = "github-actions[bot]"
_LABEL_COLORS = {FAILURE_LABEL: "d73a4a", MUTATION_LABEL: "fbca04"}
_FOOTER = "\n\n---\n_Posted by `scripts/ci/scheduled_report.py`._"
_TARGET = re.compile(r"[a-z0-9-]{1,40}\Z")
_STATE = re.compile(r"<!-- mutation-report:([a-z0-9-]+) survived=(\d+) -->")


class ApiError(Exception):
    def __init__(self, status: int, method: str, path: str, detail: str = "") -> None:
        super().__init__(f"{method} {path} returned HTTP {status}{': ' + detail if detail else ''}")
        self.status = status


class Client(Protocol):
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any: ...


class GitHubClient:
    """Minimal REST client; ``path`` is relative to ``repos/<owner>/<repo>``."""

    def __init__(self, token: str, repo: str, api_url: str = "https://api.github.com") -> None:
        if not api_url.startswith("https://"):
            raise ApiError(0, "INIT", api_url, "the API URL must use https")
        self._token = token
        self._base = f"{api_url.rstrip('/')}/repos/{repo}"

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self._base}/{path.lstrip('/')}", data=data, method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "prks-scheduled-report",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            # The URL is always https (checked in __init__).
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, method, path) from None
        except (OSError, http.client.HTTPException) as error:
            raise ApiError(0, method, path, type(error).__name__) from None
        return json.loads(raw) if raw else None


def _find_issue(client: Client, label: str, title: str, state: str = "open") -> dict[str, Any] | None:
    """The issue (not pull request) with ``label`` and exactly ``title``: oldest open, or latest closed."""
    order = "sort=created&direction=asc" if state == "open" else "sort=updated&direction=desc"
    issues = client.request("GET", f"issues?state={state}&labels={label}&per_page=100&{order}")
    for issue in issues or []:
        if "pull_request" not in issue and issue.get("title") == title:
            return issue
    return None


def _open_issue(client: Client, label: str, title: str) -> dict[str, Any] | None:
    return _find_issue(client, label, title)


def _ensure_label(client: Client, label: str) -> None:
    try:
        client.request("POST", "labels", {"name": label, "color": _LABEL_COLORS[label]})
    except ApiError as error:
        if error.status != 422:  # 422: the label already exists
            raise


def _create_issue(client: Client, label: str, title: str, body: str) -> dict[str, Any]:
    _ensure_label(client, label)
    return client.request("POST", "issues", {"title": title, "body": body, "labels": [label]})


# --- failure issues ---------------------------------------------------------


def failure_title(workflow: str) -> str:
    return f"Scheduled check failing: {workflow}"


def failed_jobs(client: Client, run_id: int) -> list[str]:
    jobs = client.request("GET", f"actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    return sorted(
        str(job["name"]) for job in (jobs or {}).get("jobs", [])
        if job.get("conclusion") in ("failure", "timed_out")
    )


def failure_text(workflow: str, run_url: str, jobs: list[str]) -> str:
    lines = [f"The scheduled run of **{workflow}** failed: {run_url}", ""]
    if jobs:
        lines += ["Failed jobs:", *(f"- {name}" for name in jobs)]
    else:
        lines.append("No job reported a failure; open the run for the reason.")
    lines += ["", "This issue closes itself when the next scheduled run passes."]
    return "\n".join(lines) + _FOOTER


def report_failure(client: Client, workflow: str, run_id: int, run_url: str, conclusion: str) -> str:
    """Open, comment on or close the workflow's failure issue. Returns what it did."""
    title = failure_title(workflow)
    issue = _open_issue(client, FAILURE_LABEL, title)
    if conclusion == "success":
        if issue is None:
            return "nothing to do"
        client.request("POST", f"issues/{issue['number']}/comments",
                       {"body": f"The scheduled run passes again: {run_url}" + _FOOTER})
        client.request("PATCH", f"issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
        return f"closed #{issue['number']}"
    text = failure_text(workflow, run_url, failed_jobs(client, run_id))
    if issue is None:
        # A failure that comes back reopens its old issue, keeping the history in one place.
        closed = _find_issue(client, FAILURE_LABEL, title, state="closed")
        if closed is not None:
            client.request("PATCH", f"issues/{closed['number']}", {"state": "open"})
            client.request("POST", f"issues/{closed['number']}/comments", {"body": "Failing again.\n\n" + text})
            return f"reopened #{closed['number']}"
        created = _create_issue(client, FAILURE_LABEL, title, text)
        return f"opened #{created['number']}"
    client.request("POST", f"issues/{issue['number']}/comments", {"body": "Failed again.\n\n" + text})
    return f"commented on #{issue['number']}"


# --- mutation report --------------------------------------------------------


@dataclass(frozen=True)
class MutationResult:
    target: str
    stats: dict[str, int]
    survivors: tuple[tuple[str, int], ...]


def parse_result(text: str) -> MutationResult:
    """One ``result-<target>`` output of ``mutation_testing.py run --json``."""
    data = json.loads(text)
    target = data["target"]
    if not isinstance(target, str) or not _TARGET.fullmatch(target):
        raise ValueError("invalid mutation target name")
    stats = {str(key): int(value) for key, value in data["stats"].items()}
    survivors = tuple((str(name), int(count)) for name, count in data["survivors_by_function"])
    return MutationResult(target, stats, survivors)


def results_from_outputs(outputs: str, targets: list[str]) -> tuple[list[MutationResult], list[str]]:
    """The results in the mutation job's merged outputs (JSON), and the targets without one."""
    merged = json.loads(outputs or "{}")
    found: list[MutationResult] = []
    missing: list[str] = []
    for target in targets:
        text = merged.get(f"result-{target}") if isinstance(merged, dict) else None
        if not text:
            missing.append(target)
            continue
        result = parse_result(text)
        if result.target != target:
            raise ValueError(f"result-{target} holds the result of {result.target}")
        found.append(result)
    return found, missing


def previous_survivors(body: str, target: str) -> int | None:
    for name, count in _STATE.findall(body or ""):
        if name == target:
            return int(count)
    return None


def mutation_comment(result: MutationResult, run_url: str, previous: int | None) -> str:
    stats = result.stats
    change = "" if previous is None else f" (last report: {previous})"
    lines = [
        f"<!-- mutation-report:{result.target} survived={stats['survived']} -->",
        f"### `{result.target}`",
        "",
        f"{stats['total']} mutants: {stats['killed']} killed, **{stats['survived']} survived**{change}, "
        f"{stats['no_tests']} not reached by the tests, {stats['timeout']} timed out. Run: {run_url}",
    ]
    if result.survivors:
        lines += ["", "| Surviving mutants in | Count |", "| --- | ---: |"]
        lines += [f"| `{func}` | {count} |" for func, count in result.survivors]
    return "\n".join(lines) + _FOOTER


MUTATION_BODY = (
    "The weekly **Mutation testing (advisory)** workflow keeps one comment per target "
    "below, edited in place each run (an edit sends no notification).\n\n"
    "A new comment, which does notify, appears only when a target has more surviving "
    "mutants than its last report. A survivor is a change no test noticed: add an "
    "assertion, or note it as equivalent. Reproduce with "
    "`python scripts/ci/mutation_testing.py run TARGET --keep DIR`.\n\n"
    "An incomplete run opens a separate `scheduled-check` issue." + _FOOTER
)


def ensure_mutation_issue(client: Client) -> int:
    issue = _open_issue(client, MUTATION_LABEL, MUTATION_TITLE)
    if issue is None:
        issue = _create_issue(client, MUTATION_LABEL, MUTATION_TITLE, MUTATION_BODY)
    return int(issue["number"])


def _comments(client: Client, number: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = client.request("GET", f"issues/{number}/comments?per_page=100&page={page}") or []
        found += batch
        if len(batch) < 100:
            return found
        page += 1


def report_mutation(client: Client, number: int, result: MutationResult, run_url: str) -> str:
    """Edit the target's sticky comment; post a new one if survivors went up."""
    marker = f"<!-- mutation-report:{result.target} "
    sticky = next(
        (c for c in _comments(client, number)
         if str(c.get("body", "")).startswith(marker)
         and (c.get("user") or {}).get("login") == REPORTER_LOGIN),
        None,
    )
    previous = previous_survivors(sticky["body"], result.target) if sticky else None
    body = mutation_comment(result, run_url, previous)
    if sticky is None:
        client.request("POST", f"issues/{number}/comments", {"body": body})
    else:
        client.request("PATCH", f"issues/comments/{sticky['id']}", {"body": body})
    survived = result.stats["survived"]
    if previous is not None and survived > previous:
        client.request("POST", f"issues/{number}/comments", {
            "body": f"**`{result.target}`: survivors went from {previous} to {survived}.** "
                    f"The target's comment above lists them. Run: {run_url}" + _FOOTER,
        })
        return f"{result.target}: survivors up {previous} -> {survived}"
    return f"{result.target}: {survived} survivors"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    failure = sub.add_parser("failure")
    failure.add_argument("--workflow", required=True)
    failure.add_argument("--run-id", type=int, required=True)
    failure.add_argument("--conclusion", choices=("success", "failure"), required=True)
    mutation = sub.add_parser("mutation")
    mutation.add_argument("--run-id", type=int, required=True)
    mutation.add_argument("--dry-run", action="store_true",
                          help="only check that every target handed over a result (pull requests)")
    args = parser.parse_args(argv)

    results: list[MutationResult] = []
    if args.command == "mutation":
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)
        from scripts.ci.mutation_testing import TARGETS

        results, missing = results_from_outputs(os.environ.get("MUTATION_OUTPUTS", ""), sorted(TARGETS))
        for target in missing:
            print(f"{target}: no result (incomplete run; the failure issue reports it)")
        if args.dry_run:
            for result in results:
                print(f"{result.target}: would report {result.stats['survived']} survivors")
            return 1 if missing else 0
    repo = os.environ["GITHUB_REPOSITORY"]
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    client = GitHubClient(os.environ["GITHUB_TOKEN"], repo, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    run_url = f"{server}/{repo}/actions/runs/{args.run_id}"
    if args.command == "failure":
        print(report_failure(client, args.workflow, args.run_id, run_url, args.conclusion))
        return 0
    number = ensure_mutation_issue(client)
    for result in results:
        print(report_mutation(client, number, result, run_url))
    return 0

if __name__ == "__main__":
    sys.exit(main())
