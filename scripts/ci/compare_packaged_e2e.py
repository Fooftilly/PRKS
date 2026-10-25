"""Compare completed, same-commit manual packaged full and smoke E2E runs.

Metrics are summed GitHub job spans (not billed minutes). This does not assess
defect-coverage equivalence or change automatic packaged E2E selection.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

WORKFLOW = ".github/workflows/e2e-classic-distribution-gate.yml"
FULL_JOB = re.compile(r"Packaged classic bundles \(([1-6])/6\)\Z")
SMOKE_JOB = "Packaged classic smoke (manual pilot)"
SHARED_JOBS = (
    "Docker classic bundle and rollback build smoke",
    "Classic bundle SW upgrade and rollback (real Chromium)",
)


class InvalidBenchmark(ValueError):
    """A run is incomplete, unpaired, or unsuitable for comparison."""


def _seconds(start: str, end: str) -> float:
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(end.replace("Z", "+00:00"))
        span = (last - first).total_seconds()
    except (AttributeError, ValueError, TypeError) as exc:
        raise InvalidBenchmark("Missing or invalid job timestamps") from exc
    if span <= 0:
        raise InvalidBenchmark("Non-positive job duration")
    return span


def summarize(run: dict, jobs: list[dict], expected_mode: str) -> dict:
    if expected_mode not in ("full", "smoke"):
        raise InvalidBenchmark("Unknown requested comparison mode")
    if run.get("event") != "workflow_dispatch" or run.get("conclusion") != "success":
        raise InvalidBenchmark("Expected a successful manual workflow_dispatch run")
    if run.get("path") != WORKFLOW or not run.get("head_sha"):
        raise InvalidBenchmark("Not a verified packaged E2E workflow run")
    if not jobs:
        raise InvalidBenchmark("No workflow jobs returned")

    active = []
    for job in jobs:
        conclusion = job.get("conclusion")
        if conclusion == "skipped":
            continue
        if conclusion != "success":
            raise InvalidBenchmark(f"Non-success job: {job.get('name')!r}")
        active.append(job)

    names = [job.get("name") for job in active]
    for name in SHARED_JOBS:
        if names.count(name) != 1:
            raise InvalidBenchmark(f"Missing or repeated shared guard: {name}")

    shards = [job for job in active if FULL_JOB.fullmatch(job.get("name") or "")]
    smoke = [job for job in active if job.get("name") == SMOKE_JOB]
    if expected_mode == "full":
        indices = sorted(int(FULL_JOB.fullmatch(job["name"]).group(1)) for job in shards)
        if indices != list(range(1, 7)) or smoke:
            raise InvalidBenchmark("Full run must have six distinct passing shards and no smoke job")
        browser_jobs = shards
    else:
        if shards or len(smoke) != 1:
            raise InvalidBenchmark("Smoke run must have one passing pilot and no full shard")
        browser_jobs = smoke

    # Confirm the omitted jobs really were skipped, rather than missing from
    # a different workflow definition or a partial API response.
    skipped = [job.get("name") or "" for job in jobs if job.get("conclusion") == "skipped"]
    if expected_mode == "full":
        if SMOKE_JOB not in skipped:
            raise InvalidBenchmark("Full run is missing its skipped smoke job")
    elif not any(name.startswith("Packaged classic bundles (") for name in skipped):
        raise InvalidBenchmark("Smoke run is missing its skipped full matrix")

    job_minutes = sum(_seconds(job["started_at"], job["completed_at"]) for job in active) / 60
    browser_minutes = sum(_seconds(job["started_at"], job["completed_at"]) for job in browser_jobs) / 60
    shared_minutes = sum(
        _seconds(job["started_at"], job["completed_at"])
        for job in active if job.get("name") in SHARED_JOBS
    ) / 60
    return {
        "sha": run["head_sha"],
        "run_id": run["id"],
        "mode": expected_mode,
        "browser_count": len(browser_jobs),
        "browser_minutes": browser_minutes,
        "shared_minutes": shared_minutes,
        "all_job_minutes": job_minutes,
        "longest_browser_minutes": max(
            _seconds(job["started_at"], job["completed_at"]) for job in browser_jobs
        ) / 60,
    }


def compare(full: dict, smoke: dict) -> str:
    if full["sha"] != smoke["sha"] or full["run_id"] == smoke["run_id"]:
        raise InvalidBenchmark("Full and smoke runs must be distinct and use the same exact SHA")

    def reduction(a: float, b: float) -> str:
        if a <= 0:
            raise InvalidBenchmark("Invalid zero full baseline")
        return f"{(1 - b / a) * 100:.1f}%"

    return "\n".join((
        f"Same-commit packaged E2E comparison: \`{full['sha']}\`",
        "",
        "| Measured job spans | Full | Smoke | Reduction |",
        "| --- | ---: | ---: | ---: |",
        f"| Passing browser jobs | {full['browser_count']} | {smoke['browser_count']} | — |",
        f"| Browser runner-min | {full['browser_minutes']:.2f} | {smoke['browser_minutes']:.2f} | {reduction(full['browser_minutes'], smoke['browser_minutes'])} |",
        f"| Shared Docker + SW runner-min | {full['shared_minutes']:.2f} | {smoke['shared_minutes']:.2f} | — |",
        f"| All successful job runner-min | {full['all_job_minutes']:.2f} | {smoke['all_job_minutes']:.2f} | {reduction(full['all_job_minutes'], smoke['all_job_minutes'])} |",
        f"| Longest browser job (min) | {full['longest_browser_minutes']:.2f} | {smoke['longest_browser_minutes']:.2f} | — |",
        "",
        f"GitHub runs: full #{full['run_id']}; smoke #{smoke['run_id']}.",
        "These are summed job start-to-complete spans, excluding queued time and billing adjustments.",
        "Passing jobs and matching SHA do not establish equivalent regression coverage.",
        "Keep default full packaged E2E until defect-class coverage and maintainer approval.",
    ))


def _get_json(url: str, token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "PRKS-E2E-benchmark"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:  # nosec B310 - fixed HTTPS API origin
        return json.load(response)


def fetch_run(repo: str, run_id: int, token: str | None) -> tuple[dict, list[dict]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise InvalidBenchmark("Invalid GitHub repository")
    if run_id <= 0:
        raise InvalidBenchmark("Run ID must be positive")
    base = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
    run = _get_json(base, token)
    jobs: list[dict] = []
    page = 1
    while True:
        data = _get_json(f"{base}/jobs?per_page=100&page={page}", token)
        batch = data.get("jobs")
        if not isinstance(batch, list):
            raise InvalidBenchmark("Missing jobs from GitHub API response")
        jobs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        if page > 20:
            raise InvalidBenchmark("Unexpectedly many workflow jobs")
    return run, jobs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="Fooftilly/PRKS")
    parser.add_argument("--full-run", type=int, required=True)
    parser.add_argument("--smoke-run", type=int, required=True)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        full_run, full_jobs = fetch_run(args.repo, args.full_run, token)
        smoke_run, smoke_jobs = fetch_run(args.repo, args.smoke_run, token)
        print(compare(summarize(full_run, full_jobs, "full"), summarize(smoke_run, smoke_jobs, "smoke")))
    except (InvalidBenchmark, HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"Packaged E2E comparison refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
