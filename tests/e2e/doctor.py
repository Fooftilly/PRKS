#!/usr/bin/env python3
"""Read-only E2E environment facts for ``scripts/e2e doctor``.

Does not install browsers, write timings, mutate storage, or start servers.
Output is concise, deterministic, and pasteable into issues/PRs so cloud-agent
failures can be classified as under-resourced containers vs app regressions.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.e2e.install_browser import (
    BROWSERS_DIR,
    chromium_executable,
    installed_playwright_version,
    pinned_playwright_version,
    playwright_chromium_revision,
)
from tests.e2e.sharding import (
    AGENT_MEMORY_PER_JOB_BYTES,
    BASELINE_TIMINGS_PATH,
    TIMINGS_PATH,
    _available_cpu_count,
    agent_default_jobs,
    detect_cgroup_cpu_count,
    detect_cgroup_memory_limit_bytes,
    load_timings,
)

# Env vars that change E2E runner / browser behavior. Listed in sorted order
# for deterministic pasteable output.
E2E_ENV_KEYS = (
    "GITHUB_BASE_REF",
    "PLAYWRIGHT_BROWSERS_PATH",
    "PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL",
    "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD",
    "PRKS_E2E",
    "PRKS_E2E_BASE",
    "PRKS_E2E_CHROMIUM_RECYCLE_EVERY",
    "PRKS_E2E_DIAGNOSTIC",
    "PRKS_E2E_FULL_TIMEOUT",
    "PRKS_E2E_JOBS",
    "PRKS_E2E_PORT_BASE",
    "PRKS_E2E_PORT_SPAN",
    "PRKS_E2E_PROFILE",
    "PRKS_E2E_REDUCED_MOTION",
    "PRKS_E2E_SEED_CACHE",
    "PRKS_E2E_WORKER",
    "TEMP",
    "TMP",
    "TMPDIR",
)

# Soft thresholds for classifying cloud-container scarcity (not hard failures).
_MIN_ADEQUATE_SHM_BYTES = 64 * 1024 * 1024
_MIN_ADEQUATE_TEMP_FREE_BYTES = 1024 * 1024 * 1024


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    if n < 0:
        return "unknown"
    units = (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024), ("B", 1))
    for label, size in units:
        if n >= size or size == 1:
            if size == 1:
                return "%d B" % n
            value = n / size
            if value >= 10 or abs(value - round(value)) < 1e-9:
                return "%d %s" % (int(round(value)), label)
            return "%.1f %s" % (value, label)
    return "%d B" % n


def _statvfs_bytes(path: Path) -> dict:
    try:
        st = os.statvfs(path)
    except OSError as exc:
        return {
            "path": str(path),
            "present": path.exists(),
            "total_bytes": None,
            "avail_bytes": None,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    # Prefer non-root available space (f_bavail) for operator disk pressure.
    total = int(st.f_frsize) * int(st.f_blocks)
    avail = int(st.f_frsize) * int(st.f_bavail)
    return {
        "path": str(path),
        "present": True,
        "total_bytes": total,
        "avail_bytes": avail,
        "error": None,
    }


def _cpu_affinity_count() -> int:
    return _available_cpu_count()


def _cgroup_cpu_quota_count() -> int | None:
    """Return finite cgroup CPU quota in CPUs, or None when unlimited/unknown.

    Separates quota from affinity so the doctor can show both components.
    """
    from tests.e2e import sharding

    for directory in sharding._cgroup_v2_self_dirs():
        parsed = sharding._parse_cpu_max(sharding._read_first((directory / "cpu.max",)))
        if parsed is not None:
            return parsed

    for directory in sharding._cgroup_v1_self_dirs("cpu", "cpu", "cpu,cpuacct"):
        parsed = sharding._parse_cfs_quota(
            sharding._read_first((directory / "cpu.cfs_quota_us",)),
            sharding._read_first((directory / "cpu.cfs_period_us",)),
        )
        if parsed is not None:
            return parsed

    raw = sharding._read_first(("/sys/fs/cgroup/cpu.max",))
    parsed = sharding._parse_cpu_max(raw)
    if parsed is not None:
        return parsed
    return sharding._parse_cfs_quota(
        sharding._read_first(("/sys/fs/cgroup/cpu/cpu.cfs_quota_us",)),
        sharding._read_first(("/sys/fs/cgroup/cpu/cpu.cfs_period_us",)),
    )


def _chromium_probe(browsers_dir: Path) -> dict:
    installed = installed_playwright_version()
    try:
        pinned = pinned_playwright_version()
    except Exception as exc:  # pragma: no cover - pin parse is usually pure
        pinned = None
        pin_error = "%s: %s" % (type(exc).__name__, exc)
    else:
        pin_error = None

    revision = None
    revision_error = None
    executable = None
    chrome_version = None
    if installed and pinned and installed == pinned:
        try:
            revision = playwright_chromium_revision()
        except Exception as exc:
            revision_error = "%s: %s" % (type(exc).__name__, exc)
        if revision:
            executable = chromium_executable(browsers_dir, revision)
            if executable is not None:
                chrome_version = _chrome_version_string(executable)

    return {
        "playwright_installed": installed,
        "playwright_pinned": pinned,
        "playwright_pin_error": pin_error,
        "playwright_match": bool(installed and pinned and installed == pinned),
        "chromium_revision": revision,
        "chromium_revision_error": revision_error,
        "chromium_available": executable is not None,
        "chromium_path": str(executable) if executable else None,
        "chromium_version": chrome_version,
        "browsers_dir": str(browsers_dir),
    }


def _chrome_version_string(executable: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (completed.stdout or completed.stderr or "").strip()
    if not text:
        return None
    return text.splitlines()[0].strip()


def _timing_file_facts(repo: Path, rel: Path) -> dict:
    path = repo / rel
    present = path.is_file()
    entries = 0
    if present:
        entries = len(load_timings(path))
    return {
        "path": str(rel).replace("\\", "/"),
        "present": present,
        "entries": entries,
    }


def _env_snapshot(environ=None) -> dict:
    env = os.environ if environ is None else environ
    out = {}
    for key in E2E_ENV_KEYS:
        if key in env:
            out[key] = env[key]
        else:
            out[key] = None
    return out


def classify_assessment(facts: dict) -> dict:
    """Map collected facts to a pasteable failure-classification hint."""
    reasons = []
    browser = facts["browser"]
    resources = facts["resources"]

    if not browser["playwright_installed"]:
        reasons.append("Playwright package is not installed")
    elif not browser["playwright_match"]:
        reasons.append(
            "Playwright installed=%s does not match pin=%s"
            % (browser["playwright_installed"], browser["playwright_pinned"])
        )
    if browser["playwright_match"] and not browser["chromium_available"]:
        reasons.append(
            "Chromium for the pinned Playwright revision is not in %s"
            % browser["browsers_dir"]
        )

    setup_gap = bool(reasons)

    cpu_eff = resources["cpu_effective"]
    mem = resources["memory_limit_bytes"]
    shm_avail = resources["shm"]["avail_bytes"]
    temp_avail = resources["temp"]["avail_bytes"]
    agent_jobs = facts["agent_default_workers"]

    resource_reasons = []
    if cpu_eff is not None and cpu_eff <= 1:
        resource_reasons.append("effective CPU is %s (agent workers serial)" % cpu_eff)
    if mem is not None and mem < AGENT_MEMORY_PER_JOB_BYTES:
        resource_reasons.append(
            "cgroup memory %s is below one agent worker budget (%s)"
            % (_fmt_bytes(mem), _fmt_bytes(AGENT_MEMORY_PER_JOB_BYTES))
        )
    if agent_jobs <= 1 and resource_reasons:
        resource_reasons.append("agent default workers=%d" % agent_jobs)
    if shm_avail is not None and shm_avail < _MIN_ADEQUATE_SHM_BYTES:
        resource_reasons.append(
            "/dev/shm available %s is below %s"
            % (_fmt_bytes(shm_avail), _fmt_bytes(_MIN_ADEQUATE_SHM_BYTES))
        )
    if temp_avail is not None and temp_avail < _MIN_ADEQUATE_TEMP_FREE_BYTES:
        resource_reasons.append(
            "temp free %s is below %s"
            % (_fmt_bytes(temp_avail), _fmt_bytes(_MIN_ADEQUATE_TEMP_FREE_BYTES))
        )

    under_resourced = bool(resource_reasons)

    if setup_gap and under_resourced:
        kind = "setup_and_under_resourced"
        hint = (
            "Fix browser toolchain first; remaining cloud flakiness may still be "
            "container resource pressure rather than an app regression."
        )
        reasons = reasons + resource_reasons
    elif setup_gap:
        kind = "browser_toolchain_gap"
        hint = (
            "E2E cannot run until Playwright/Chromium match the project pin. "
            "This is an environment gap, not an application regression."
        )
    elif under_resourced:
        kind = "under_resourced_container"
        hint = (
            "Cloud container looks under-resourced for parallel Chromium. "
            "Treat timeouts/stalls here as resource pressure until CPU/memory/shm "
            "look adequate; do not classify as an app regression on resource facts alone."
        )
        reasons = resource_reasons
    else:
        kind = "resources_look_adequate"
        hint = (
            "Browser toolchain and container resources look sufficient for "
            "scripts/e2e agent. Prefer app/regression investigation for E2E failures."
        )
        reasons = []

    return {"kind": kind, "reasons": reasons, "hint": hint}


def collect_report(repo: Path | None = None, environ=None) -> dict:
    """Gather read-only facts. Pure enough to unit-test with injected environ."""
    root = Path(repo) if repo is not None else REPO
    env = os.environ if environ is None else environ
    raw_browsers = env.get("PLAYWRIGHT_BROWSERS_PATH")
    if raw_browsers:
        browsers_dir = Path(raw_browsers)
        if not browsers_dir.is_absolute():
            browsers_dir = root / browsers_dir
    else:
        # Prefer the install helper's absolute cache when probing this repo.
        browsers_dir = BROWSERS_DIR if root == REPO else (root / BROWSERS_DIR.name)

    affinity = _cpu_affinity_count()
    quota = _cgroup_cpu_quota_count()
    effective = detect_cgroup_cpu_count()
    memory = detect_cgroup_memory_limit_bytes()
    agent_jobs = agent_default_jobs(cpu_count=effective, memory_limit_bytes=memory)

    temp_dir = Path(tempfile.gettempdir())
    shm = _statvfs_bytes(Path("/dev/shm"))
    temp = _statvfs_bytes(temp_dir)

    facts = {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "browser": _chromium_probe(browsers_dir),
        "resources": {
            "cpu_affinity": affinity,
            "cpu_cgroup_quota": quota,
            "cpu_effective": effective,
            "memory_limit_bytes": memory,
            "shm": shm,
            "temp": temp,
        },
        "agent_default_workers": agent_jobs,
        "timing_history_local": _timing_file_facts(root, TIMINGS_PATH),
        "timing_baseline_committed": _timing_file_facts(root, BASELINE_TIMINGS_PATH),
        "env": _env_snapshot(env),
    }
    facts["assessment"] = classify_assessment(facts)
    return facts


def format_report(facts: dict) -> str:
    """Render a deterministic, pasteable plain-text report."""
    browser = facts["browser"]
    resources = facts["resources"]
    assessment = facts["assessment"]
    lines = [
        "PRKS E2E doctor (read-only)",
        "===========================",
        "python: %s (%s) executable=%s"
        % (
            facts["python"]["version"],
            facts["python"]["implementation"],
            facts["python"]["executable"],
        ),
        "playwright: installed=%s pinned=%s match=%s"
        % (
            browser["playwright_installed"] or "(absent)",
            browser["playwright_pinned"] or "(absent)",
            "yes" if browser["playwright_match"] else "no",
        ),
    ]
    if browser["playwright_pin_error"]:
        lines.append("playwright_pin_error: %s" % browser["playwright_pin_error"])

    chromium_bits = [
        "available=%s" % ("yes" if browser["chromium_available"] else "no"),
        "revision=%s" % (browser["chromium_revision"] or "(unknown)"),
        "path=%s" % (browser["chromium_path"] or "(missing)"),
    ]
    if browser["chromium_version"]:
        chromium_bits.append("version=%s" % browser["chromium_version"])
    if browser["chromium_revision_error"]:
        chromium_bits.append("error=%s" % browser["chromium_revision_error"])
    lines.append("chromium: %s" % " ".join(chromium_bits))
    lines.append("browsers_dir: %s" % browser["browsers_dir"])

    quota = resources["cpu_cgroup_quota"]
    lines.append(
        "cpu: affinity_or_cpuset=%s cgroup_quota=%s effective=%s"
        % (
            resources["cpu_affinity"],
            "unlimited/unknown" if quota is None else quota,
            resources["cpu_effective"],
        )
    )
    mem = resources["memory_limit_bytes"]
    lines.append(
        "memory_cgroup: %s"
        % ("unlimited/unknown" if mem is None else _fmt_bytes(mem))
    )

    shm = resources["shm"]
    if shm["error"]:
        lines.append("shm: path=%s error=%s" % (shm["path"], shm["error"]))
    else:
        lines.append(
            "shm: path=%s total=%s avail=%s"
            % (shm["path"], _fmt_bytes(shm["total_bytes"]), _fmt_bytes(shm["avail_bytes"]))
        )

    temp = resources["temp"]
    if temp["error"]:
        lines.append("disk_temp: path=%s error=%s" % (temp["path"], temp["error"]))
    else:
        lines.append(
            "disk_temp: path=%s total=%s avail=%s"
            % (
                temp["path"],
                _fmt_bytes(temp["total_bytes"]),
                _fmt_bytes(temp["avail_bytes"]),
            )
        )

    lines.append("agent_default_workers: %s" % facts["agent_default_workers"])

    local = facts["timing_history_local"]
    lines.append(
        "timing_history_local: present=%s path=%s entries=%s"
        % (
            "yes" if local["present"] else "no",
            local["path"],
            local["entries"],
        )
    )
    baseline = facts["timing_baseline_committed"]
    lines.append(
        "timing_baseline_committed: present=%s path=%s entries=%s"
        % (
            "yes" if baseline["present"] else "no",
            baseline["path"],
            baseline["entries"],
        )
    )

    lines.append("env:")
    for key in E2E_ENV_KEYS:
        value = facts["env"].get(key)
        lines.append("  %s=%s" % (key, "(unset)" if value is None else value))

    lines.append("assessment: %s" % assessment["kind"])
    for reason in assessment["reasons"]:
        lines.append("  - %s" % reason)
    lines.append("hint: %s" % assessment["hint"])
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    del argv  # doctor takes no flags today; reserved for future --json etc.
    # Refuse accidental writes: never call ensure_chromium_installed / save_timings.
    print(format_report(collect_report()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
