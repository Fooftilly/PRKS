#!/usr/bin/env python3
"""Compare six-shard source and packaged E2E costs on one exact Git commit.

Read-only GitHub API analysis. Measurements are summed job/step wall spans,
NOT GitHub-billed minutes or evidence of equivalent regression coverage.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.compare_packaged_e2e import InvalidBenchmark, _seconds, fetch_run

SOURCE_WORKFLOW = ".github/workflows/e2e-gate.yml"
PACKAGED_WORKFLOW = ".github/workflows/e2e-classic-distribution-gate.yml"
SHARDS = 6
PLAN_SUMMARY = re.compile(
    r"(?m)^\S+ mode=(full|affected|skip) run=(true|false) "
    r"features=\S+ test_count=\S+ shards=(\d+) pointer=(true|false)\s*$"
)
KINDS = {
    "source": (
        SOURCE_WORKFLOW,
        "E2E ({}/6)",
        "Run E2E selection shard",
        ("E2E plan", "E2E pointer capture", "Full E2E gate"),
    ),
    "packaged": (
        PACKAGED_WORKFLOW,
        "Packaged classic bundles ({}/6)",
        "Run complete E2E shard on the packaged frontend",
        (
            "Docker classic bundle and rollback build smoke",
            "Classic bundle SW upgrade and rollback (real Chromium)",
        ),
    ),
}


def _check_run(run: dict, mode: str) -> None:
    if not isinstance(run, dict) or mode not in KINDS:
        raise InvalidBenchmark("Unknown comparison mode or malformed workflow run")
    if run.get("path") != KINDS[mode][0] or run.get("status") != "completed":
        raise InvalidBenchmark(f"Expected a completed {mode} E2E workflow")
    if run.get("conclusion") != "success" or run.get("run_attempt") != 1:
        raise InvalidBenchmark("Only successful first-attempt workflow runs are comparable")
    if run.get("event") != "pull_request":
        raise InvalidBenchmark("Only uninstrumented pull-request runs are comparable")
    if not run.get("head_sha") or not isinstance(run.get("id"), int):
        raise InvalidBenchmark("Missing workflow run identity")


def _check_jobs(jobs: list[dict], mode: str) -> tuple[list[dict], str]:
    if not isinstance(jobs, list) or not jobs or any(not isinstance(j, dict) for j in jobs):
        raise InvalidBenchmark("Missing or malformed workflow jobs")
    active = [j for j in jobs if j.get("conclusion") != "skipped"]
    if any(j.get("conclusion") != "success" for j in active):
        raise InvalidBenchmark("A workflow job did not succeed")
    _, template, step_name, guards = KINDS[mode]
    names = [j.get("name") for j in active]
    for guard in guards:
        if names.count(guard) != 1:
            raise InvalidBenchmark(f"Missing or repeated required {mode} guard: {guard}")
    required_names = [template.format(i) for i in range(1, SHARDS + 1)]
    if any(names.count(name) != 1 for name in required_names):
        raise InvalidBenchmark(f"{mode} run does not contain six distinct passing shards")
    prefix = "E2E (" if mode == "source" else "Packaged classic bundles ("
    if any(name.startswith(prefix) and name not in required_names
           for name in names if isinstance(name, str)):
        raise InvalidBenchmark(f"{mode} run contains unexpected browser shards")
    by_name = {j["name"]: j for j in active if j.get("name") in required_names}
    return [by_name[name] for name in required_names], step_name


def summarize(
    run: dict, jobs: list[dict], mode: str, *, source_plan: tuple[str, str, str, str] | None = None
) -> dict:
    """Require verified source full mode, six complete shards and test steps."""
    _check_run(run, mode)
    if mode == "source" and source_plan != ("full", "true", "6", "true"):
        raise InvalidBenchmark("Source planner full mode was not verified")
    shards, step_name = _check_jobs(jobs, mode)
    job_seconds = 0.0
    test_seconds = 0.0
    durations = []
    starts = []
    for job in shards:
        job_duration = _seconds(job["started_at"], job["completed_at"])
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise InvalidBenchmark("Missing shard step timings")
        selected = [s for s in steps if isinstance(s, dict) and s.get("name") == step_name]
        if len(selected) != 1 or selected[0].get("conclusion") != "success":
            raise InvalidBenchmark(f"Missing or unsuccessful test step: {step_name}")
        step_duration = _seconds(selected[0]["started_at"], selected[0]["completed_at"])
        start = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
        step_start = datetime.fromisoformat(selected[0]["started_at"].replace("Z", "+00:00"))
        step_end = datetime.fromisoformat(selected[0]["completed_at"].replace("Z", "+00:00"))
        if step_start < start or step_end > end or step_duration > job_duration:
            raise InvalidBenchmark("Test step falls outside its job")
        job_seconds += job_duration
        test_seconds += step_duration
        durations.append(job_duration)
        starts.append(start)
    return {
        "mode": mode,
        "sha": run["head_sha"],
        "event": run["event"],
        "run_id": run["id"],
        "shards": len(shards),
        "runner_minutes": job_seconds / 60,
        "test_minutes": test_seconds / 60,
        "non_test_minutes": (job_seconds - test_seconds) / 60,
        "test_share_percent": 100 * test_seconds / job_seconds,
        "longest_job_minutes": max(durations) / 60,
        "start_skew_seconds": (max(starts) - min(starts)).total_seconds(),
    }


def _read_source_plan_log(repo: str, job_id: int, token: str) -> str:
    """Download the public plan job log with credentials scoped to GitHub API.

    GitHub issues a signed HTTPS blob URL. Never include the bearer token in
    the subsequent request, even if the redirect host happens to be GitHub.
    """
    response = requests.get(
        f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PRKS-E2E-benchmark",
            "Authorization": f"Bearer {token}",
        },
        timeout=30, verify=True, allow_redirects=False,
    )
    if response.status_code != 302:
        raise InvalidBenchmark(f"GitHub planner log endpoint returned HTTP {response.status_code}")
    location = response.headers.get("Location", "")
    parsed = urlsplit(location)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise InvalidBenchmark("Invalid signed GitHub planner log location")
    download = requests.get(
        location,
        headers={"User-Agent": "PRKS-E2E-benchmark"},
        timeout=30, verify=True, allow_redirects=False, stream=True,
    )
    try:
        if download.status_code != 200:
            raise InvalidBenchmark(f"Signed planner log returned HTTP {download.status_code}")
        content = bytearray()
        limit = 256_000
        for chunk in download.iter_content(chunk_size=8192):
            content.extend(chunk)
            if len(content) > limit:
                raise InvalidBenchmark("Unexpectedly large E2E planner log")
        if not content:
            raise InvalidBenchmark("Missing E2E planner log")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidBenchmark("Invalid UTF-8 E2E planner log") from exc
    finally:
        download.close()


def verify_source_full_plan(repo: str, jobs: list[dict], token: str | None) -> tuple[str, str, str, str]:
    """Prove full source selection using its successful E2E plan job log."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise InvalidBenchmark("Invalid GitHub repository")
    if not token:
        raise InvalidBenchmark("GH_TOKEN or GITHUB_TOKEN required to read E2E planner logs")
    if not isinstance(jobs, list) or any(not isinstance(j, dict) for j in jobs):
        raise InvalidBenchmark("Malformed E2E planner job metadata")
    plans = [j for j in jobs if j.get("name") == "E2E plan"]
    if len(plans) != 1 or plans[0].get("conclusion") != "success":
        raise InvalidBenchmark("Missing or unsuccessful E2E planner")
    job_id = plans[0].get("id")
    if not isinstance(job_id, int) or job_id <= 0:
        raise InvalidBenchmark("Missing E2E planner job ID")
    found = PLAN_SUMMARY.findall(_read_source_plan_log(repo, job_id, token))
    if len(found) != 1 or found[0] != ("full", "true", "6", "true"):
        raise InvalidBenchmark("E2E planner did not prove a complete six-shard full suite")
    return found[0]


def compare(source: dict, packaged: dict) -> str:
    if (source["mode"], packaged["mode"]) != ("source", "packaged"):
        raise InvalidBenchmark("Expected source and packaged full suites")
    if source["sha"] != packaged["sha"] or source["event"] != packaged["event"]:
        raise InvalidBenchmark("Source and packaged runs must share SHA and trigger event")
    if source["run_id"] == packaged["run_id"]:
        raise InvalidBenchmark("Expected distinct GitHub Actions runs")
    return "\n".join((
        "Same-commit source vs packaged E2E: " + chr(96) + source["sha"] + chr(96),
        "",
        "| Six-shard measure | Source checkout | Packaged distribution |",
        "| --- | ---: | ---: |",
        f"| Summed shard runner-min | {source['runner_minutes']:.2f} | {packaged['runner_minutes']:.2f} |",
        f"| Browser execution step min | {source['test_minutes']:.2f} | {packaged['test_minutes']:.2f} |",
        f"| Setup/teardown and other job min | {source['non_test_minutes']:.2f} | {packaged['non_test_minutes']:.2f} |",
        f"| Browser execution share | {source['test_share_percent']:.1f}% | {packaged['test_share_percent']:.1f}% |",
        f"| Longest shard job min | {source['longest_job_minutes']:.2f} | {packaged['longest_job_minutes']:.2f} |",
        f"| Shard start-time spread sec | {source['start_skew_seconds']:.0f} | {packaged['start_skew_seconds']:.0f} |",
        "",
        f"GitHub runs: source #{source['run_id']}; packaged #{packaged['run_id']}.",
        "Job-span minutes are not billing units; start skew is not total queue delay.",
        "Only six browser shards are measured here; Docker, SW and pointer guards are verified but excluded from these minute totals.",
        "Passing identical tests on both distributions does not establish equivalence with a narrower packaged smoke pilot.",
        "No automatic E2E selection or required gate policy is changed.",
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="Fooftilly/PRKS")
    parser.add_argument("--source-run", type=int, required=True)
    parser.add_argument("--packaged-run", type=int, required=True)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        source_run, source_jobs = fetch_run(args.repo, args.source_run, token)
        packaged_run, packaged_jobs = fetch_run(args.repo, args.packaged_run, token)
        _check_run(source_run, "source")
        plan_record = verify_source_full_plan(args.repo, source_jobs, token)
        source = summarize(source_run, source_jobs, "source", source_plan=plan_record)
        packaged = summarize(packaged_run, packaged_jobs, "packaged")
        print(compare(source, packaged))
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"E2E cost comparison refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
