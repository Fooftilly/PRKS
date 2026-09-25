"""Pure scheduling/aggregation logic for the parallel E2E runner.

Imports nothing from Playwright and starts no processes, so `run_tests.py` can
cover it. `run.py` owns discovery, subprocess management and reporting; every
decision it makes about *which* tests go *where* and whether a run passed is
made here.
"""
from __future__ import annotations

import json
import math
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
BASELINE_TIMINGS_PATH = Path("tests") / "e2e" / "timing-baseline.json"

# Agent mode is intentionally conservative: two browser/server stacks already
# consume substantial RAM on small cloud VMs, while four can turn nominal
# parallelism into swap/CPU contention and intermittent Chromium stalls.
AGENT_MAX_JOBS = 2
AGENT_MEMORY_PER_JOB_BYTES = 3 * 1024 * 1024 * 1024


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


def parse_shard(value, env_value=None):
    """Parse external shard spec ``INDEX/TOTAL`` (1-based index).

    Returns ``(index_0based, total)`` or ``None`` when unset. Used by CI matrix
    runners so each GitHub Actions job owns a disjoint slice of the full gate
    while still sharing the same LPT partitioner as ``--jobs N``.
    """
    raw = value if value is not None else env_value
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if "/" not in text:
        raise ValueError(
            "shard must look like INDEX/TOTAL (1-based), got %r" % (raw,)
        )
    left, right = text.split("/", 1)
    if "/" in right:
        raise ValueError(
            "shard must look like INDEX/TOTAL (1-based), got %r" % (raw,)
        )
    try:
        index = int(left.strip())
        total = int(right.strip())
    except (TypeError, ValueError):
        raise ValueError(
            "shard INDEX and TOTAL must be integers, got %r" % (raw,)
        ) from None
    if total < 1:
        raise ValueError("shard TOTAL must be >= 1, got %d" % total)
    if total > MAX_JOBS:
        # Same ceiling as local --jobs: keeps port-window math and matrix size
        # honest rather than inventing a second, larger limit.
        raise ValueError("shard TOTAL must be <= %d, got %d" % (MAX_JOBS, total))
    if index < 1 or index > total:
        raise ValueError(
            "shard INDEX must be in 1..%d inclusive, got %d" % (total, index)
        )
    return index - 1, total


def select_external_shard(test_ids, shard_index, shard_total, timings=None, default=DEFAULT_TEST_SECONDS):
    """Return the test IDs owned by one external shard.

    ``shard_index`` is 0-based. Assignment is the same LPT partition used by
    ``--jobs``: every discovered ID lands in exactly one shard, timing-aware
    when history/baseline weights exist, and deterministic for equal costs.
    """
    if shard_total < 1:
        raise ValueError("shard total must be >= 1, got %d" % shard_total)
    if shard_index < 0 or shard_index >= shard_total:
        raise ValueError(
            "shard index %d out of range for %d shards" % (shard_index, shard_total)
        )
    buckets = assign_shards(test_ids, shard_total, timings, default=default)
    return buckets[shard_index]


def partition_external_shards(test_ids, shard_total, timings=None, default=DEFAULT_TEST_SECONDS):
    """Return all external-shard buckets (convenience for coverage / CI plan)."""
    return assign_shards(test_ids, shard_total, timings, default=default)


def aggregate_external_shard_results(reports):
    """Decide a multi-runner full-gate outcome from per-shard result dicts.

    Each report should include ``shard`` (1-based label like ``2/4``),
    ``returncode``, ``reported``, and optional ``failures`` / ``errors`` /
    ``failed_ids``. A shard that never reported is a failure. Used by unit
    tests and as the contract the GitHub Actions aggregator mirrors.
    """
    problems = []
    failed_ids = []
    seen = set()
    totals = {"shards": 0, "reported": 0, "failures": 0, "errors": 0}
    for report in reports:
        totals["shards"] += 1
        label = report.get("shard") or report.get("index")
        failures = report.get("failures") or 0
        errors = report.get("errors") or 0
        if isinstance(failures, (int, float)) and not isinstance(failures, bool):
            totals["failures"] += int(failures)
        else:
            failures = 0
        if isinstance(errors, (int, float)) and not isinstance(errors, bool):
            totals["errors"] += int(errors)
        else:
            errors = 0
        for test_id in report.get("failed_ids") or []:
            if test_id not in seen:
                seen.add(test_id)
                failed_ids.append(test_id)
        if not report.get("reported"):
            problems.append(
                "shard %s produced no result (exit code %s)"
                % (label, report.get("returncode"))
            )
            continue
        totals["reported"] += 1
        if report.get("returncode") != 0:
            problems.append(
                "shard %s exited %s" % (label, report.get("returncode"))
            )
            continue
        if failures or errors:
            problems.append(
                "shard %s reported %s failure(s), %s error(s)"
                % (label, failures, errors)
            )
    return (not problems), totals, problems, failed_ids


def module_of(test_id: str) -> str:
    """`tests.e2e.test_app.SomeClass.test_x` -> `tests.e2e.test_app`."""
    parts = test_id.split(".")
    if len(parts) <= 2:
        return test_id
    return ".".join(parts[:-2])


def _valid_timing_value(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value <= 0 or value != value or value == float("inf"):
        return None
    return value


def estimate_seconds(test_id: str, timings, default=DEFAULT_TEST_SECONDS) -> float:
    """Estimated duration for one test, with exact history preferred.

    Machine-local timing history uses exact unittest IDs. The committed
    bootstrap baseline may also contain prefix entries ending in an asterisk;
    the longest matching prefix wins. This gives stateless cloud agents useful
    first-run shard weights without pretending those coarse values are
    measured timings for a specific machine.
    """
    if not timings:
        return default

    exact = _valid_timing_value(timings.get(test_id))
    if exact is not None:
        return exact

    best = None
    best_len = -1
    for key, raw in timings.items():
        if not isinstance(key, str) or not key.endswith("*"):
            continue
        prefix = key[:-1]
        if not test_id.startswith(prefix):
            continue
        value = _valid_timing_value(raw)
        if value is None or len(prefix) <= best_len:
            continue
        best = value
        best_len = len(prefix)
    return default if best is None else best


def merge_timing_sources(baseline, local):
    """Return scheduling weights with local exact measurements overriding baseline."""
    merged = dict(baseline or {})
    merged.update(local or {})
    return merged


def _read_first(paths):
    for path in paths:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return None


def _cgroup_v2_self_dirs():
    """Yield mounted cgroup-v2 directories from the process leaf up to the mount root.

    Nested cloud-agent containers often put finite CPU/memory ceilings on a
    leaf or parent while the hierarchy root looks unlimited. Walking ancestors
    lets agent sizing use the tightest visible limit.
    """
    try:
        text = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return
    rel = None
    for line in text.splitlines():
        parts = line.split(":")
        # Unified hierarchy: "0::/path/to/cgroup"
        if len(parts) >= 3 and parts[0] == "0" and parts[1] == "":
            rel = parts[2]
            break
    if rel is None:
        return
    yield from _cgroup_ancestor_dirs(Path("/sys/fs/cgroup"), rel)


def _cgroup_ancestor_dirs(mount: Path, rel: str):
    """Yield `mount` joined with `rel` and every ancestor up to `mount`."""
    rel = (rel or "/").strip() or "/"
    if not rel.startswith("/"):
        rel = "/" + rel
    parts = [p for p in rel.split("/") if p]
    for depth in range(len(parts), -1, -1):
        if depth == 0:
            yield mount
        else:
            yield mount.joinpath(*parts[:depth])


def _cgroup_v1_rel_for(controller: str) -> str | None:
    """Return this process's relative path under a cgroup-v1 controller."""
    try:
        text = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 3 or parts[0] == "0":
            continue
        controllers = [c.strip() for c in parts[1].split(",") if c.strip()]
        if controller in controllers:
            return parts[2]
    return None


def _cgroup_v1_self_dirs(controller: str, *mount_names: str):
    """Yield nested cgroup-v1 dirs for `controller` from leaf to mount root."""
    rel = _cgroup_v1_rel_for(controller)
    if rel is None:
        return
    for name in mount_names:
        mount = Path("/sys/fs/cgroup") / name
        # Only walk mounts that actually exist; hybrid hosts may expose one.
        if not mount.is_dir():
            continue
        yield from _cgroup_ancestor_dirs(mount, rel)


def _parse_cpu_max(raw):
    if not raw:
        return None
    parts = raw.split()
    if len(parts) < 2 or parts[0] == "max":
        return None
    try:
        quota = int(parts[0])
        period = int(parts[1])
    except ValueError:
        return None
    if quota > 0 and period > 0:
        return max(1, math.floor(quota / period))
    return None


def _parse_memory_max(raw):
    if raw is None or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0 or value >= (1 << 60):
        return None
    return value


def _parse_cfs_quota(quota_raw, period_raw):
    try:
        if quota_raw is None or period_raw is None:
            return None
        quota = int(quota_raw)
        period = int(period_raw)
    except ValueError:
        return None
    if quota > 0 and period > 0:
        return max(1, math.floor(quota / period))
    return None


def _available_cpu_count() -> int:
    """CPUs this process may actually run on (affinity/cpuset), not host size.

    Python 3.12's ``os.cpu_count()`` on Linux reports the machine's CPU count.
    Cloud containers often pin a process to a cpuset while leaving ``cpu.max``
    unlimited; using the host count would then over-parallelize agent E2E.
    """
    getter = getattr(os, "sched_getaffinity", None)
    if getter is not None:
        try:
            affinity = getter(0)
        except OSError:
            affinity = None
        if affinity:
            return max(1, len(affinity))
    return max(1, int(os.cpu_count() or 1))


def detect_cgroup_cpu_count() -> int | None:
    """Best-effort effective CPU count for Linux containers/cgroups."""
    available = _available_cpu_count()
    quota_count = None

    for directory in _cgroup_v2_self_dirs():
        parsed = _parse_cpu_max(_read_first((directory / "cpu.max",)))
        if parsed is not None:
            quota_count = parsed if quota_count is None else min(quota_count, parsed)

    if quota_count is None:
        # Nested cgroup-v1: walk the process cpu controller path, not only
        # the hierarchy root (which can look unlimited while the leaf is not).
        for directory in _cgroup_v1_self_dirs("cpu", "cpu", "cpu,cpuacct"):
            parsed = _parse_cfs_quota(
                _read_first((directory / "cpu.cfs_quota_us",)),
                _read_first((directory / "cpu.cfs_period_us",)),
            )
            if parsed is not None:
                quota_count = (
                    parsed if quota_count is None else min(quota_count, parsed)
                )

    if quota_count is None:
        raw = _read_first(("/sys/fs/cgroup/cpu.max",))
        quota_count = _parse_cpu_max(raw)

    if quota_count is None:
        quota_count = _parse_cfs_quota(
            _read_first(("/sys/fs/cgroup/cpu/cpu.cfs_quota_us",)),
            _read_first(("/sys/fs/cgroup/cpu/cpu.cfs_period_us",)),
        )

    if quota_count is None:
        return available
    return max(1, min(available, quota_count))


def detect_cgroup_memory_limit_bytes() -> int | None:
    """Best-effort memory ceiling for Linux containers/cgroups.

    Returns None when no finite cgroup limit is visible. Very large v1
    sentinel values are treated as unlimited. When nested cgroup-v1/v2 dirs
    expose different ceilings, the tightest finite limit wins.
    """
    best = None
    for directory in _cgroup_v2_self_dirs():
        parsed = _parse_memory_max(_read_first((directory / "memory.max",)))
        if parsed is None:
            continue
        best = parsed if best is None else min(best, parsed)
    if best is not None:
        return best

    for directory in _cgroup_v1_self_dirs("memory", "memory"):
        parsed = _parse_memory_max(
            _read_first((directory / "memory.limit_in_bytes",))
        )
        if parsed is None:
            continue
        best = parsed if best is None else min(best, parsed)
    if best is not None:
        return best

    raw = _read_first(
        (
            "/sys/fs/cgroup/memory.max",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
        )
    )
    return _parse_memory_max(raw)


def agent_resource_limits() -> dict:
    return {
        "cpu_count": detect_cgroup_cpu_count(),
        "memory_limit_bytes": detect_cgroup_memory_limit_bytes(),
    }


def agent_default_jobs(cpu_count=None, memory_limit_bytes=None) -> int:
    """Conservative browser-worker width for unknown cloud-agent machines."""
    if cpu_count is None:
        cpu_count = detect_cgroup_cpu_count()
    if memory_limit_bytes is None:
        memory_limit_bytes = detect_cgroup_memory_limit_bytes()

    jobs = min(AGENT_MAX_JOBS, max(1, int(cpu_count or 1)))
    if memory_limit_bytes is not None:
        memory_jobs = max(1, int(memory_limit_bytes) // AGENT_MEMORY_PER_JOB_BYTES)
        jobs = min(jobs, memory_jobs)
    return max(1, jobs)


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
