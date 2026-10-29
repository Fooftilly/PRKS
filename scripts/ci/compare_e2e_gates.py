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
    if any(j.get("status") not in (None, "completed") for j in active):
        raise InvalidBenchmark("Partial or unfinished workflow job")
    if any(j.get("run_attempt") not in (None, 1) for j in active):
        raise InvalidBenchmark("Retried workflow job")
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


def _timestamp(value: str, label: str) -> datetime:
    """Parse an explicit GitHub UTC/offset timestamp; never assume a timezone."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError, TypeError) as exc:
        raise InvalidBenchmark(f"Missing or invalid {label} timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidBenchmark(f"Timezone missing from {label} timestamp")
    return parsed


def _validated_steps(job: dict, step_name: str, job_start: datetime,
                     job_end: datetime) -> float:
    """Check every executed step for chronology, not just the browser step."""
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) for s in steps):
        raise InvalidBenchmark("Missing or malformed shard step timings")
    selected = [s for s in steps if s.get("name") == step_name]
    if len(selected) != 1 or selected[0].get("conclusion") != "success":
        raise InvalidBenchmark(f"Missing or unsuccessful test step: {step_name}")

    windows = []
    for step in steps:
        conclusion = step.get("conclusion")
        status = step.get("status")
        if status is not None and status != "completed":
            raise InvalidBenchmark("Partial or unfinished E2E step")
        if conclusion == "skipped":
            continue  # GitHub may give a skipped step equal or absent timestamps.
        if conclusion != "success":
            raise InvalidBenchmark("Failed or incomplete E2E shard step")
        start = _timestamp(step.get("started_at"), "step start")
        end = _timestamp(step.get("completed_at"), "step completion")
        if start < job_start or end > job_end or end < start:
            raise InvalidBenchmark("Test step falls outside its job or step timestamps reversed")
        if step is selected[0] and end <= start:
            raise InvalidBenchmark("Non-positive browser test step duration")
        windows.append((start, end))

    windows.sort()
    if any(current[0] < previous[1] for previous, current in zip(windows, windows[1:])):
        raise InvalidBenchmark("Overlapping executed E2E step windows")
    return (
        _timestamp(selected[0]["completed_at"], "test completion")
        - _timestamp(selected[0]["started_at"], "test start")
    ).total_seconds()


def summarize(
    run: dict, jobs: list[dict], mode: str, *, source_plan: tuple[str, str, str, str] | None = None
) -> dict:
    """Require same-head full mode, six complete jobs and nonoverlapping steps."""
    _check_run(run, mode)
    if mode == "source" and source_plan != ("full", "true", "6", "true"):
        raise InvalidBenchmark("Source planner full mode was not verified")
    shards, step_name = _check_jobs(jobs, mode)
    job_seconds = 0.0
    test_seconds = 0.0
    durations = []
    starts = []
    ends = []
    individual = []
    queue_seconds = []
    for index, job in enumerate(shards, 1):
        if job.get("status") not in (None, "completed"):
            raise InvalidBenchmark("Unfinished shard job")
        if job.get("run_attempt") not in (None, 1):
            raise InvalidBenchmark("Retried or unverified shard job")
        if job.get("run_id") not in (None, run["id"]):
            raise InvalidBenchmark("Shard job belongs to another run")
        start = _timestamp(job.get("started_at"), "job start")
        end = _timestamp(job.get("completed_at"), "job completion")
        job_duration = _seconds(job.get("started_at"), job.get("completed_at"))
        step_duration = _validated_steps(job, step_name, start, end)
        if step_duration > job_duration:
            raise InvalidBenchmark("Test step exceeds job span")

        # Job creation is not necessarily the moment a runner becomes eligible:
        # dependencies and scheduler delay may also be included in this lag.
        lag = None
        if job.get("created_at") is not None:
            created = _timestamp(job["created_at"], "job creation")
            lag = (start - created).total_seconds()
            if lag < 0:
                raise InvalidBenchmark("Job created after it started")
            queue_seconds.append(lag)

        non_test = job_duration - step_duration
        job_seconds += job_duration
        test_seconds += step_duration
        durations.append(job_duration)
        starts.append(start)
        ends.append(end)
        individual.append({
            "shard": index,
            "job_seconds": job_duration,
            "test_seconds": step_duration,
            "non_test_seconds": non_test,
            "created_to_start_seconds": lag,
        })

    # GitHub Actions' workflow-run updated_at is not a reliable completion
    # timestamp; only compute wall time from an explicit completed_at field.
    workflow_wall_seconds = None
    if run.get("completed_at") is not None and run.get("run_started_at") is not None:
        wall_start = _timestamp(run["run_started_at"], "workflow start")
        wall_end = _timestamp(run["completed_at"], "workflow completion")
        if wall_end <= wall_start:
            raise InvalidBenchmark("Contradictory workflow wall timestamps")
        for active_job in (j for j in jobs if j.get("conclusion") != "skipped"):
            active_start = _timestamp(active_job.get("started_at"), "active job start")
            active_end = _timestamp(active_job.get("completed_at"), "active job completion")
            if active_start < wall_start or active_end > wall_end or active_end < active_start:
                raise InvalidBenchmark("Contradictory workflow wall timestamps")
        workflow_wall_seconds = (wall_end - wall_start).total_seconds()

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
        "shard_envelope_minutes": (max(ends) - min(starts)).total_seconds() / 60,
        "created_to_start_count": len(queue_seconds),
        "created_to_start_minutes": sum(queue_seconds) / 60 if len(queue_seconds) == SHARDS else None,
        "workflow_wall_minutes": (workflow_wall_seconds / 60
                                  if workflow_wall_seconds is not None else None),
        "per_shard": individual,
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


def _minutes(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "unavailable"


def compare(source: dict, packaged: dict) -> str:
    if (source["mode"], packaged["mode"]) != ("source", "packaged"):
        raise InvalidBenchmark("Expected source and packaged full suites")
    if source["sha"] != packaged["sha"] or source["event"] != packaged["event"]:
        raise InvalidBenchmark("Source and packaged runs must share SHA and trigger event")
    if source["run_id"] == packaged["run_id"]:
        raise InvalidBenchmark("Expected distinct GitHub Actions runs")
    rows = [
        "Same-commit source vs packaged E2E: " + chr(96) + source["sha"] + chr(96),
        "",
        "| Six-shard measure | Source checkout | Packaged distribution |",
        "| --- | ---: | ---: |",
        f"| Passing browser shards | {source['shards']} | {packaged['shards']} |",
        f"| Summed shard runner-min | {source['runner_minutes']:.2f} | {packaged['runner_minutes']:.2f} |",
        f"| Browser execution step min | {source['test_minutes']:.2f} | {packaged['test_minutes']:.2f} |",
        f"| Non-test job residual min | {source['non_test_minutes']:.2f} | {packaged['non_test_minutes']:.2f} |",
        f"| Browser execution share | {source['test_share_percent']:.1f}% | {packaged['test_share_percent']:.1f}% |",
        f"| Longest shard job min | {source['longest_job_minutes']:.2f} | {packaged['longest_job_minutes']:.2f} |",
        f"| Shard start-time spread sec | {source['start_skew_seconds']:.0f} | {packaged['start_skew_seconds']:.0f} |",
        f"| Browser-shard envelope min (overlaps included) | {source['shard_envelope_minutes']:.2f} | {packaged['shard_envelope_minutes']:.2f} |",
        f"| Job created-to-start observations | {source['created_to_start_count']}/6 | {packaged['created_to_start_count']}/6 |",
        f"| Sum job created-to-start lag min (6/6 only) | {_minutes(source['created_to_start_minutes'])} | {_minutes(packaged['created_to_start_minutes'])} |",
        f"| Workflow wall min (explicit start/end only) | {_minutes(source['workflow_wall_minutes'])} | {_minutes(packaged['workflow_wall_minutes'])} |",
        "",
        "| Mode | Shard | Created-to-start sec | Job span sec | Browser step sec | Non-test residual sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in (source, packaged):
        for shard in item["per_shard"]:
            lag = shard["created_to_start_seconds"]
            rows.append(
                f"| {item['mode']} | {shard['shard']}/6 | "
                f"{_minutes(lag)} | {shard['job_seconds']:.2f} | "
                f"{shard['test_seconds']:.2f} | {shard['non_test_seconds']:.2f} |"
            )
    rows.extend((
        "",
        f"GitHub runs: source #{source['run_id']}; packaged #{packaged['run_id']}.",
        "Job created-to-start lag is a scheduling/queue proxy, not a measured runner-only wait: it can include unmet dependencies.",
        "Unavailable timestamps are not zero. A partial queue sample is shown per shard but no partial sum is compared.",
        "Summed job spans overlap in real time; the browser-shard envelope is not complete workflow wall time.",
        "Workflow wall time is unavailable unless GitHub supplies reliable explicit workflow start AND completion timestamps; updated_at is excluded.",
        "Job-span minutes are NOT GitHub-billed minutes (billing/rounding not queried). Start skew is not queue delay.",
        "The non-test residual includes setup, installation, post-processing, idle gaps and teardown together; these are not separately attributed.",
        "Only six browser shards are measured here; Docker, SW and pointer guards are verified but excluded from shard sums.",
        "Passing identical tests on both distributions does not establish equivalence with a narrower packaged smoke pilot.",
        "No automatic E2E selection or required gate policy is changed.",
    ))
    return "\n".join(rows)


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
