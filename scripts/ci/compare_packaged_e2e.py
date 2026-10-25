"""Compare completed, same-commit manual packaged full and smoke E2E runs.

Metrics are summed GitHub job spans (not billed minutes). This does not assess
defect-coverage equivalence or change automatic packaged E2E selection.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
import requests

WORKFLOW = ".github/workflows/e2e-classic-distribution-gate.yml"
FULL_JOB = re.compile(r"Packaged classic bundles \(([1-6])/6\)\Z")
SMOKE_JOB = "Packaged classic smoke (manual pilot)"
SKIPPED_FULL_MATRIX = "Packaged classic bundles (${{ matrix.shard }}/6)"
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


def _validate_run(run: dict, expected_mode: str) -> None:
    if not isinstance(run, dict):
        raise InvalidBenchmark("Workflow run must be a JSON object")
    if expected_mode not in ("full", "smoke"):
        raise InvalidBenchmark("Unknown requested comparison mode")
    if run.get("event") != "workflow_dispatch" or run.get("conclusion") != "success":
        raise InvalidBenchmark("Expected a successful manual workflow_dispatch run")
    if run.get("path") != WORKFLOW or not run.get("head_sha"):
        raise InvalidBenchmark("Not a verified packaged E2E workflow run")
    if run.get("run_attempt") != 1:
        raise InvalidBenchmark("Re-run attempts are not comparable: prior job cost is omitted")


def _successful_jobs(jobs: list[dict]) -> list[dict]:
    if not isinstance(jobs, list) or not jobs:
        raise InvalidBenchmark("No workflow jobs returned")
    if any(not isinstance(job, dict) for job in jobs):
        raise InvalidBenchmark("Workflow jobs must be JSON objects")
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
    return active


def _browser_jobs(active: list[dict], jobs: list[dict], mode: str) -> list[dict]:
    shards = [job for job in active if FULL_JOB.fullmatch(job.get("name") or "")]
    smoke = [job for job in active if job.get("name") == SMOKE_JOB]
    skipped = [job.get("name") or "" for job in jobs if job.get("conclusion") == "skipped"]
    if mode == "full":
        indices = sorted(int(FULL_JOB.fullmatch(job["name"]).group(1)) for job in shards)
        if indices != list(range(1, 7)) or smoke:
            raise InvalidBenchmark("Full run must have six distinct passing shards and no smoke job")
        if SMOKE_JOB not in skipped:
            raise InvalidBenchmark("Full run is missing its skipped smoke job")
        return shards
    if shards or len(smoke) != 1:
        raise InvalidBenchmark("Smoke run must have one passing pilot and no full shard")
    if skipped.count(SKIPPED_FULL_MATRIX) != 1 or any(FULL_JOB.fullmatch(name) for name in skipped):
        raise InvalidBenchmark("Smoke run is missing its skipped full matrix")
    return smoke


def summarize(run: dict, jobs: list[dict], expected_mode: str) -> dict:
    _validate_run(run, expected_mode)
    active = _successful_jobs(jobs)
    browser_jobs = _browser_jobs(active, jobs, expected_mode)
    def minutes(selected: list[dict]) -> float:
        return sum(
            _seconds(job["started_at"], job["completed_at"]) for job in selected
        ) / 60
    return {
        "sha": run["head_sha"],
        "run_id": run["id"],
        "mode": expected_mode,
        "browser_count": len(browser_jobs),
        "browser_minutes": minutes(browser_jobs),
        "shared_minutes": minutes([
            job for job in active if job.get("name") in SHARED_JOBS
        ]),
        "all_job_minutes": minutes(active),
        "longest_browser_minutes": max(
            _seconds(job["started_at"], job["completed_at"]) for job in browser_jobs
        ) / 60,
    }


def _report_mode(full: dict, smoke: dict, paired: bool) -> str:
    if full["sha"] != smoke["sha"]:
        raise InvalidBenchmark("Full and smoke runs must use the same exact SHA")
    if (full["run_id"] == smoke["run_id"]) != paired:
        raise InvalidBenchmark("Incorrect distinct/paired workflow run IDs")
    if full["mode"] != "full" or smoke["mode"] != "smoke":
        raise InvalidBenchmark("Expected full and smoke package summaries")
    if not paired:
        return f"GitHub runs: full #{full['run_id']}; smoke #{smoke['run_id']}."
    return (
        f"GitHub run #{full['run_id']}: BOTH modes on one SHA; guards ran once. "
        "Per-mode totals allocate the shared guards to each mode independently, "
        "not two sets of actual billed jobs."
    )


def summarize_paired(run: dict, jobs: list[dict]) -> tuple[dict, dict]:
    """Validate a manual compare dispatch containing BOTH complete browser legs.

    Each counterfactual mode includes the shared Docker and SW guard cost
    once; the actual compare dispatch executes those guard jobs only once.
    """
    _validate_run(run, "full")
    active = _successful_jobs(jobs)
    shards = [j for j in active if FULL_JOB.fullmatch(j.get("name") or "")]
    smoke = [j for j in active if j.get("name") == SMOKE_JOB]
    shard_ids = sorted(int(FULL_JOB.fullmatch(j["name"]).group(1)) for j in shards)
    if shard_ids != list(range(1, 7)) or len(smoke) != 1:
        raise InvalidBenchmark("Paired run requires all six full shards and one smoke job")
    permitted = {*(j["name"] for j in shards), SMOKE_JOB, *SHARED_JOBS}
    if any(j.get("name") not in permitted for j in active):
        raise InvalidBenchmark("Unexpected successful job in paired run")
    skipped = {j.get("name") for j in jobs if j.get("conclusion") == "skipped"}
    if SMOKE_JOB in skipped or SKIPPED_FULL_MATRIX in skipped:
        raise InvalidBenchmark("Paired run contains a skipped browser mode")
    guard_jobs = [j for j in active if j.get("name") in SHARED_JOBS]

    def select(mode: str, browser_jobs: list[dict]) -> dict:
        def minutes(selected: list[dict]) -> float:
            return sum(_seconds(j["started_at"], j["completed_at"]) for j in selected) / 60
        return {
            "sha": run["head_sha"],
            "run_id": run["id"],
            "mode": mode,
            "browser_count": len(browser_jobs),
            "browser_minutes": minutes(browser_jobs),
            "shared_minutes": minutes(guard_jobs),
            "all_job_minutes": minutes(browser_jobs + guard_jobs),
            "longest_browser_minutes": max(
                _seconds(j["started_at"], j["completed_at"]) for j in browser_jobs
            ) / 60,
        }
    return select("full", shards), select("smoke", smoke)


def compare(full: dict, smoke: dict, *, paired: bool = False) -> str:
    provenance = _report_mode(full, smoke, paired)

    def reduction(a: float, b: float) -> str:
        if a <= 0:
            raise InvalidBenchmark("Invalid zero full baseline")
        return f"{(1 - b / a) * 100:.1f}%"

    return "\n".join((
        f"Same-commit packaged E2E comparison: `{full['sha']}`",
        "",
        "| Measured job spans | Full | Smoke | Reduction |",
        "| --- | ---: | ---: | ---: |",
        f"| Passing browser jobs | {full['browser_count']} | {smoke['browser_count']} | — |",
        f"| Browser runner-min | {full['browser_minutes']:.2f} | {smoke['browser_minutes']:.2f} | {reduction(full['browser_minutes'], smoke['browser_minutes'])} |",
        f"| Shared Docker + SW runner-min | {full['shared_minutes']:.2f} | {smoke['shared_minutes']:.2f} | — |",
        f"| All successful job runner-min | {full['all_job_minutes']:.2f} | {smoke['all_job_minutes']:.2f} | {reduction(full['all_job_minutes'], smoke['all_job_minutes'])} |",
        f"| Longest browser job (min) | {full['longest_browser_minutes']:.2f} | {smoke['longest_browser_minutes']:.2f} | — |",
        "",
        provenance,
        "These are summed job start-to-complete spans, excluding queued time and billing adjustments.",
        "Passing jobs and matching SHA do not establish equivalent regression coverage.",
        "Keep default full packaged E2E until defect-class coverage and maintainer approval.",
    ))


def _get_json(path: str, token: str | None) -> dict:
    """Fetch one validated GitHub API path with certificate and proxy handling.

    Requests enforces TLS certificate checks and honors HTTPS_PROXY / NO_PROXY
    without reimplementing CONNECT or depending on version-sensitive stdlib
    HTTP connection internals. Disable redirects to prevent origin changes.
    """
    if not path.startswith("/repos/"):
        raise InvalidBenchmark("Unexpected GitHub API path")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "PRKS-E2E-benchmark"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(
        f"https://api.github.com{path}",
        headers=headers,
        timeout=30,
        verify=True,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise InvalidBenchmark(f"GitHub API returned HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise InvalidBenchmark("GitHub API response must be a JSON object")
    return payload


def fetch_run(repo: str, run_id: int, token: str | None) -> tuple[dict, list[dict]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise InvalidBenchmark("Invalid GitHub repository")
    if run_id <= 0:
        raise InvalidBenchmark("Run ID must be positive")
    base = f"/repos/{repo}/actions/runs/{run_id}"
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
    parser.add_argument("--full-run", type=int)
    parser.add_argument("--smoke-run", type=int)
    parser.add_argument("--paired-run", type=int,
                        help="Single manual compare dispatch with full and smoke jobs")
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        if args.paired_run is not None:
            if args.full_run is not None or args.smoke_run is not None:
                raise InvalidBenchmark("Use either --paired-run OR --full-run and --smoke-run")
            paired_run, paired_jobs = fetch_run(args.repo, args.paired_run, token)
            full, smoke = summarize_paired(paired_run, paired_jobs)
            print(compare(full, smoke, paired=True))
        else:
            if args.full_run is None or args.smoke_run is None:
                raise InvalidBenchmark("Both --full-run and --smoke-run are required")
            full_run, full_jobs = fetch_run(args.repo, args.full_run, token)
            smoke_run, smoke_jobs = fetch_run(args.repo, args.smoke_run, token)
            print(compare(summarize(full_run, full_jobs, "full"),
                          summarize(smoke_run, smoke_jobs, "smoke")))
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        print(f"Packaged E2E comparison refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
