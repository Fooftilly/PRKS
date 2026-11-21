#!/usr/bin/env python3
"""Run master's full CI on a schedule, only when something was merged since the last run.

Pull requests already pass the full gates before they merge, so master no
longer reruns them on every push. Once a night, ``master-ci.yml`` runs one job
per workflow in ``WORKFLOWS``; each calls this script, which finds the last run
of that workflow it started (a ``workflow_dispatch`` run on master by
``github-actions[bot]``) and decides:

- it tested an older commit, or there is none: start one;
- it tested this commit and passed: nothing to do;
- it tested this commit and is still running: wait for it;
- it tested this commit and failed, timed out or was cancelled: start one
  again, so a flaky failure gets its retry and a real one keeps reporting.

The job then waits for that run and fails when it fails, so the scheduled
"Master CI after merges" run fails too and ``scheduled-report.yml`` opens
(or updates, or closes) the issue "Scheduled check failing: Master CI after
merges", naming the failed job, which names the workflow.

Usage::

    python scripts/ci/master_ci.py --workflow e2e-gate.yml [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from urllib.parse import quote
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.scheduled_report import Client, GitHubClient  # noqa: E402

BRANCH = "master"
DISPATCHER = "github-actions[bot]"
# The full gates a merge could break, plus the scanners whose master results
# pull requests are compared against. master-ci.yml runs one job for each.
WORKFLOWS = (
    "e2e-gate.yml",
    "test-gate.yml",
    "static-analysis.yml",
    "codeql.yml",
    "zizmor.yml",
)
POLL_SECONDS = 60
# How long a dispatched run may take to appear in the run list.
APPEAR_SECONDS = 300
# Before any run has passed, diff_base() reads the whole history for the
# first run. GitHub lists at most 1000 runs per filter: nearly three years
# of nightly failures, after which it refuses rather than guess.
HISTORY_PER_PAGE = 100
HISTORY_PAGES = 10
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _ours(run: dict[str, Any]) -> bool:
    # A person's manual dispatch (a benchmark, say) is not one of ours. The
    # actor is whoever started the run; a person re-running ours changes only
    # the triggering actor, so the run stays ours.
    return (run.get("actor") or {}).get("login") == DISPATCHER


def dispatched_runs(client: Client, workflow: str) -> list[dict[str, Any]]:
    """This script's master runs of ``workflow``, newest first."""
    page = client.request("GET", _runs_query(workflow, "&per_page=30"))
    return [run for run in (page or {}).get("workflow_runs", []) if _ours(run)]


def decide(run: dict[str, Any] | None, head: str) -> tuple[str, str]:
    """``start``, ``wait`` or ``done``, and why."""
    if run is None:
        return "start", "no earlier run"
    if run.get("head_sha") != head:
        return "start", f"new merges since {str(run.get('head_sha'))[:8]}"
    if run.get("status") != "completed":
        return "wait", "a run of this commit is in progress"
    if run.get("conclusion") == "success":
        return "done", "this commit already passed"
    return "start", f"retrying this commit after {run.get('conclusion')}"


def _runs_query(workflow: str, extra: str = "") -> str:
    # Filtered by actor on GitHub's side: the run list returns at most 1000
    # results per filter, and a person's dispatches would otherwise use them up.
    return (f"actions/workflows/{workflow}/runs?branch={BRANCH}&event=workflow_dispatch"
            f"&actor={quote(DISPATCHER)}{extra}")


def _sha(run: dict[str, Any]) -> str | None:
    sha = run.get("head_sha")
    return sha if isinstance(sha, str) and _SHA.match(sha) else None


def diff_base(client: Client, workflow: str) -> str | None:
    """The commit static analysis last passed on, else the first one master CI checked.

    Never the commit of a failed run: retrying a failed commit against
    itself would show the diff-aware guards an empty diff and pass. Until
    one run passes, every run keeps the first checked commit as its base.
    The last pass is asked for directly (``status=success``), so no run of
    failures can push it out of view.
    """
    passed = (client.request("GET", _runs_query(workflow, "&status=success&per_page=10")) or {})
    for run in passed.get("workflow_runs", []):
        if _ours(run) and run.get("conclusion") == "success" and _sha(run):
            return _sha(run)
    # Nothing has passed yet: the oldest run is the first commit checked.
    oldest = None
    for page in range(1, HISTORY_PAGES + 1):
        runs = (client.request("GET", _runs_query(workflow, f"&per_page={HISTORY_PER_PAGE}&page={page}"))
                or {}).get("workflow_runs", [])
        for run in runs:
            if _ours(run) and _sha(run):
                oldest = _sha(run)
        if len(runs) < HISTORY_PER_PAGE:
            return oldest
    raise RuntimeError(f"{workflow}: more than {HISTORY_PAGES * HISTORY_PER_PAGE} runs and none passed")


def dispatch_body(client: Client, workflow: str) -> dict[str, Any]:
    body: dict[str, Any] = {"ref": BRANCH}
    base = diff_base(client, workflow) if workflow == "static-analysis.yml" else None
    if base:
        # Its diff-aware guards then see everything merged since that check.
        body["inputs"] = {"diff_base": base}
    return body


def master_head(client: Client) -> str:
    sha = (client.request("GET", f"branches/{BRANCH}") or {}).get("commit", {}).get("sha", "")
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise RuntimeError(f"unexpected {BRANCH} head: {sha!r}")
    return sha


def start(client: Client, workflow: str, known: set[int], body: dict[str, Any], *,
          sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Dispatch ``workflow`` on master and return the new run."""
    client.request("POST", f"actions/workflows/{workflow}/dispatches", body)
    waited = 0.0
    while True:
        for run in dispatched_runs(client, workflow):
            if run.get("id") not in known:
                return run
        if waited >= APPEAR_SECONDS:
            raise RuntimeError(f"{workflow}: the dispatched run did not appear")
        sleep(10)
        waited += 10


def wait(client: Client, run: dict[str, Any], *,
         sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    while run.get("status") != "completed":
        sleep(POLL_SECONDS)
        run = client.request("GET", f"actions/runs/{run['id']}")
    return run


def run_workflow(client: Client, workflow: str, *, dry_run: bool = False,
                 sleep: Callable[[float], None] = time.sleep) -> tuple[bool, str]:
    """Bring ``workflow`` up to date with master's head. Returns (passed, message).

    The head is read live, not from the scheduled run's own commit, because
    a dispatch always runs on the branch's current head: deciding and
    dispatching on the same commit keeps every job of one night on it.
    """
    head = master_head(client)
    runs = dispatched_runs(client, workflow)
    previous = runs[0] if runs else None
    action, reason = decide(previous, head)
    if dry_run or action == "done":
        return True, f"{workflow}: {'would ' + action if dry_run else 'skipped'} ({reason})"
    run = previous if action == "wait" and previous else start(
        client, workflow, {r["id"] for r in runs}, dispatch_body(client, workflow), sleep=sleep)
    print(f"{workflow}: {'waiting for' if action == 'wait' else 'started'} {run.get('html_url')} ({reason})",
          flush=True)
    run = wait(client, run, sleep=sleep)
    conclusion = run.get("conclusion")
    tested = str(run.get("head_sha"))
    note = "" if tested == head else f" (master moved from {head[:8]} during the dispatch)"
    return conclusion == "success", f"{workflow}: {conclusion} at {tested[:8]} {run.get('html_url')}{note}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workflow", required=True, choices=WORKFLOWS)
    parser.add_argument("--dry-run", action="store_true", help="print the decision, start nothing")
    args = parser.parse_args(argv)
    client = GitHubClient(
        os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    passed, message = run_workflow(client, args.workflow, dry_run=args.dry_run)
    print(message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"- {message}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
