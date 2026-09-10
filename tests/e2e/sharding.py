"""Pure scheduling/aggregation logic for the parallel E2E runner.

Imports nothing from Playwright and starts no processes, so `run_tests.py` can
cover it. `run.py` owns discovery, subprocess management and reporting; every
decision it makes about *which* tests go *where* and whether a run passed is
made here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Cost assumed for a test with no timing history. Deliberately close to the
# observed median E2E test so a first (historyless) run still spreads unknown
# tests evenly instead of piling them onto one worker.
DEFAULT_TEST_SECONDS = 3.0

# 18000 + MAX_JOBS * 1000 must stay below the Linux ephemeral range (32768),
# so a worker window can never collide with a kernel-assigned port.
MAX_JOBS = 12

TIMINGS_PATH = Path(".tests") / "e2e-timings.json"


def parse_jobs(value, env_value=None, default=1):
    """Resolve the worker count. CLI wins over env; env wins over the default.

    Raises ValueError with an actionable message rather than silently clamping,
    so `--jobs 0` or `--jobs banana` cannot quietly become a serial run.
    """
    raw = value if value is not None else env_value
    if raw is None or raw == "":
        return default
    try:
        jobs = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("worker count must be an integer, got %r" % (raw,)) from None
    if jobs < 1:
        raise ValueError("worker count must be >= 1, got %d" % jobs)
    if jobs > MAX_JOBS:
        raise ValueError("worker count must be <= %d, got %d" % (MAX_JOBS, jobs))
    return jobs


def module_of(test_id: str) -> str:
    """`tests.e2e.test_app.SomeClass.test_x` -> `tests.e2e.test_app`."""
    parts = test_id.split(".")
    if len(parts) <= 2:
        return test_id
    return ".".join(parts[:-2])


def estimate_seconds(test_id: str, timings, default=DEFAULT_TEST_SECONDS) -> float:
    """Historical duration for one test, falling back to the default estimate.

    Non-numeric and non-positive history entries are treated as absent: a
    corrupt timings file must never be able to starve a worker.
    """
    if not timings:
        return default
    raw = timings.get(test_id)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = float(raw)
    if value <= 0 or value != value or value == float("inf"):
        return default
    return value


def assign_shards(test_ids, jobs: int, timings=None, default=DEFAULT_TEST_SECONDS):
    """Split test IDs across `jobs` workers, longest-processing-time-first.

    Every discovered ID lands in exactly one shard. Tests are placed heaviest
    first onto the currently-cheapest worker, which balances by *estimated
    duration* rather than test count. Each shard is then re-ordered so tests
    from one module stay contiguous: unittest tears down and re-runs a module
    fixture whenever the module changes, and these modules launch Chromium in
    `setUpModule`, so interleaving modules would relaunch the browser per test.
    """
    if jobs < 1:
        raise ValueError("worker count must be >= 1, got %d" % jobs)
    ids = list(test_ids)
    if not ids:
        return [[] for _ in range(jobs)]

    # Deterministic ordering before scheduling: equal-cost tests must not be
    # placed in whatever order discovery happened to yield.
    ordered = sorted(ids, key=lambda t: (-estimate_seconds(t, timings, default), t))
    loads = [0.0] * jobs
    buckets = [[] for _ in range(jobs)]
    for test_id in ordered:
        target = min(range(jobs), key=lambda i: (loads[i], i))
        buckets[target].append(test_id)
        loads[target] += estimate_seconds(test_id, timings, default)

    module_order = {}
    for test_id in ids:
        module_order.setdefault(module_of(test_id), len(module_order))
    for bucket in buckets:
        bucket.sort(key=lambda t: (module_order.get(module_of(t), len(module_order)), t))
    return buckets


def shard_estimates(buckets, timings=None, default=DEFAULT_TEST_SECONDS):
    return [
        sum(estimate_seconds(test_id, timings, default) for test_id in bucket)
        for bucket in buckets
    ]


def load_timings(path):
    """Read timing history, tolerating absent/corrupt/foreign files.

    Timing history is an optimisation hint, never required state, so every
    failure mode collapses to "no history".
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if number <= 0 or number != number or number == float("inf"):
            continue
        clean[key] = number
    return clean


def merge_timings(previous, observed, known_ids=None):
    """Fold a run's measurements into the history and drop renamed tests.

    `known_ids` is the set discovered by *this* run; entries outside it are
    dropped so a rename cannot leave the file growing forever. When a run only
    executed part of the suite (a targeted debug run), pass `known_ids=None` so
    untouched history survives.
    """
    merged = dict(previous or {})
    for key, value in (observed or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if number <= 0 or number != number or number == float("inf"):
            continue
        merged[key] = round(number, 3)
    if known_ids is not None:
        allowed = set(known_ids)
        merged = {k: v for k, v in merged.items() if k in allowed}
    return dict(sorted(merged.items()))


def save_timings(path, timings) -> bool:
    """Persist history atomically. Never raises: this is runner metadata only."""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(timings, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, target)
        return True
    except OSError:
        return False


def format_slowest(timings, limit=25):
    """Lines for the "Slowest E2E tests" report, slowest first."""
    rows = sorted(
        ((float(v), k) for k, v in (timings or {}).items()),
        key=lambda pair: (-pair[0], pair[1]),
    )[: max(0, limit)]
    return ["%7.2fs  %s" % (seconds, test_id) for seconds, test_id in rows]


def worker_port_range(index: int, jobs: int, base: int = 18000, span: int = 1000):
    """Disjoint local port window per worker.

    `find_free_port()` binds, closes, and only later hands the port to a server
    subprocess, so two workers racing the same free port is possible. Giving
    each worker its own window removes the cross-worker half of that race; the
    window sits below the Linux ephemeral range so the kernel does not hand the
    same port to an unrelated socket either.
    """
    if index < 0 or index >= jobs:
        raise ValueError("worker index %d out of range for %d jobs" % (index, jobs))
    start = base + index * span
    end = start + span - 1
    if end > 65535:
        raise ValueError("worker port range for index %d exceeds the port space" % index)
    return start, end


def aggregate_worker_results(reports):
    """Decide the run's outcome from per-worker reports.

    A report is a dict with `index`, `returncode`, `reported` (did the worker
    write a result document at all), `tests`, `failures`, `errors`, `skipped`.
    A worker that vanished without reporting is a failure, never a pass.
    """
    problems = []
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for report in reports:
        index = report.get("index")
        for key in totals:
            value = report.get(key) or 0
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] += int(value)
        if not report.get("reported"):
            problems.append(
                "worker %s produced no result document (exit code %s)"
                % (index, report.get("returncode"))
            )
            continue
        if report.get("returncode") != 0:
            problems.append(
                "worker %s exited %s" % (index, report.get("returncode"))
            )
            continue
        if (report.get("failures") or 0) or (report.get("errors") or 0):
            problems.append(
                "worker %s reported %s failure(s), %s error(s)"
                % (index, report.get("failures") or 0, report.get("errors") or 0)
            )
    return (not problems), totals, problems


def run_exit_code(tests_ok: bool, pointer_returncode) -> int:
    """Process exit status for a whole run.

    `pointer_returncode` is None when the pointer-capture suite did not run
    (skipped by flag, or skipped because the tests already failed). A failing
    pointer-capture run fails the gate exactly like a failing test worker.
    """
    if not tests_ok:
        return 1
    if pointer_returncode is None:
        return 0
    return 0 if pointer_returncode == 0 else 1
