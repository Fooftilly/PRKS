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
# Linux shared-memory mount probed read-only via statvfs (not a temp-file API).
_SHM_PROBE_PATH = Path(os.sep) / "dev" / "shm"


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
    """Return tightest finite cgroup CPU quota in CPUs, or None if unlimited/unknown.

    Matches ``detect_cgroup_cpu_count`` ancestor walk: nested parents can impose
    a tighter quota than the leaf, so the first finite value is not enough.
    """
    from tests.e2e import sharding

    quota_count = None
    for directory in sharding._cgroup_v2_self_dirs():
        parsed = sharding._parse_cpu_max(sharding._read_first((directory / "cpu.max",)))
        if parsed is not None:
            quota_count = parsed if quota_count is None else min(quota_count, parsed)

    if quota_count is None:
        for directory in sharding._cgroup_v1_self_dirs("cpu", "cpu", "cpu,cpuacct"):
            parsed = sharding._parse_cfs_quota(
                sharding._read_first((directory / "cpu.cfs_quota_us",)),
                sharding._read_first((directory / "cpu.cfs_period_us",)),
            )
            if parsed is not None:
                quota_count = (
                    parsed if quota_count is None else min(quota_count, parsed)
                )

    if quota_count is None:
        raw = sharding._read_first(("/sys/fs/cgroup/cpu.max",))
        quota_count = sharding._parse_cpu_max(raw)

    if quota_count is None:
        quota_count = sharding._parse_cfs_quota(
            sharding._read_first(("/sys/fs/cgroup/cpu/cpu.cfs_quota_us",)),
            sharding._read_first(("/sys/fs/cgroup/cpu/cpu.cfs_period_us",)),
        )
    return quota_count


def _e2e_browsers_dir(repo: Path) -> Path:
    """Repository-local Chromium cache the E2E runner forces via apply_playwright_browser_env.

    Read-only: does not mkdir or mutate PLAYWRIGHT_BROWSERS_PATH.
    """
    if repo == REPO:
        return BROWSERS_DIR
    return repo / BROWSERS_DIR.name


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

    # Available only when the binary both exists and answers --version.
    # A stale/corrupt path that merely exists is a toolchain gap.
    return {
        "playwright_installed": installed,
        "playwright_pinned": pinned,
        "playwright_pin_error": pin_error,
        "playwright_match": bool(installed and pinned and installed == pinned),
        "chromium_revision": revision,
        "chromium_revision_error": revision_error,
        "chromium_available": chrome_version is not None,
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
    # Nonzero exit with stderr noise must not look like a usable Chromium.
    if completed.returncode != 0:
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


def _browser_setup_reasons(browser: dict) -> list[str]:
    reasons = []
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
    return reasons


def _resource_pressure_reasons(resources: dict, agent_jobs: int) -> list[str]:
    reasons = []
    cpu_eff = resources["cpu_effective"]
    mem = resources["memory_limit_bytes"]
    shm_avail = resources["shm"]["avail_bytes"]
    temp_avail = resources["temp"]["avail_bytes"]

    if cpu_eff is not None and cpu_eff <= 1:
        reasons.append("effective CPU is %s (agent workers serial)" % cpu_eff)
    if mem is not None and mem < AGENT_MEMORY_PER_JOB_BYTES:
        reasons.append(
            "cgroup memory %s is below one agent worker budget (%s)"
            % (_fmt_bytes(mem), _fmt_bytes(AGENT_MEMORY_PER_JOB_BYTES))
        )
    if agent_jobs <= 1 and reasons:
        reasons.append("agent default workers=%d" % agent_jobs)
    if shm_avail is not None and shm_avail < _MIN_ADEQUATE_SHM_BYTES:
        reasons.append(
            "shm available %s is below %s"
            % (_fmt_bytes(shm_avail), _fmt_bytes(_MIN_ADEQUATE_SHM_BYTES))
        )
    if temp_avail is not None and temp_avail < _MIN_ADEQUATE_TEMP_FREE_BYTES:
        reasons.append(
            "temp free %s is below %s"
            % (_fmt_bytes(temp_avail), _fmt_bytes(_MIN_ADEQUATE_TEMP_FREE_BYTES))
        )
    return reasons


def _resource_incomplete_reasons(resources: dict) -> list[str]:
    """Probe failures that must block a resources_look_adequate claim."""
    reasons = []
    shm = resources.get("shm") or {}
    temp = resources.get("temp") or {}
    if shm.get("avail_bytes") is None:
        reasons.append("shm available space unknown (probe failed or missing)")
    if temp.get("avail_bytes") is None:
        reasons.append("temp disk available space unknown (probe failed or missing)")
    return reasons


def _assessment_for(
    setup_reasons: list[str],
    resource_reasons: list[str],
    incomplete_reasons: list[str] | None = None,
) -> dict:
    setup_gap = bool(setup_reasons)
    under_resourced = bool(resource_reasons)
    incomplete = list(incomplete_reasons or [])

    if setup_gap and under_resourced:
        return {
            "kind": "setup_and_under_resourced",
            "reasons": setup_reasons + resource_reasons,
            "hint": (
                "Fix browser toolchain first; remaining cloud flakiness may still be "
                "container resource pressure rather than an app regression."
            ),
        }
    if setup_gap:
        return {
            "kind": "browser_toolchain_gap",
            "reasons": setup_reasons,
            "hint": (
                "E2E cannot run until Playwright/Chromium match the project pin. "
                "This is an environment gap, not an application regression."
            ),
        }
    if under_resourced:
        return {
            "kind": "under_resourced_container",
            "reasons": resource_reasons,
            "hint": (
                "Cloud container looks under-resourced for parallel Chromium. "
                "Treat timeouts/stalls here as resource pressure until CPU/memory/shm "
                "look adequate; do not classify as an app regression on resource facts alone."
            ),
        }
    if incomplete:
        return {
            "kind": "resource_facts_incomplete",
            "reasons": incomplete,
            "hint": (
                "Resource probes are incomplete; do not treat E2E failures as an "
                "application regression until shm/temp (and related) facts are readable."
            ),
        }
    return {
        "kind": "resources_look_adequate",
        "reasons": [],
        "hint": (
            "Browser toolchain and container resources look sufficient for "
            "scripts/e2e agent. Prefer app/regression investigation for E2E failures."
        ),
    }


def classify_assessment(facts: dict) -> dict:
    """Map collected facts to a pasteable failure-classification hint."""
    setup = _browser_setup_reasons(facts["browser"])
    pressure = _resource_pressure_reasons(
        facts["resources"], facts["agent_default_workers"]
    )
    incomplete = _resource_incomplete_reasons(facts["resources"])
    return _assessment_for(setup, pressure, incomplete)


def collect_report(repo: Path | None = None, environ=None) -> dict:
    """Gather read-only facts. Pure enough to unit-test with injected environ."""
    root = Path(repo) if repo is not None else REPO
    env = os.environ if environ is None else environ
    # E2E always forces the repo-local cache (apply_playwright_browser_env).
    # Probe that path; surface any inherited PLAYWRIGHT_BROWSERS_PATH separately.
    browsers_dir = _e2e_browsers_dir(root)
    inherited_browsers = env.get("PLAYWRIGHT_BROWSERS_PATH")

    affinity = _cpu_affinity_count()
    quota = _cgroup_cpu_quota_count()
    effective = detect_cgroup_cpu_count()
    memory = detect_cgroup_memory_limit_bytes()
    agent_jobs = agent_default_jobs(cpu_count=effective, memory_limit_bytes=memory)

    temp_dir = Path(tempfile.gettempdir())
    shm = _statvfs_bytes(_SHM_PROBE_PATH)
    temp = _statvfs_bytes(temp_dir)

    browser = _chromium_probe(browsers_dir)
    browser["browsers_dir_inherited"] = inherited_browsers

    facts = {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "browser": browser,
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
    lines.append(
        "browsers_dir: %s (E2E repo cache; apply_playwright_browser_env)"
        % browser["browsers_dir"]
    )
    inherited = browser.get("browsers_dir_inherited")
    lines.append(
        "playwright_browsers_path_inherited: %s"
        % ("(unset)" if inherited is None else inherited)
    )

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
