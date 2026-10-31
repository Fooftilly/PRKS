"""Opt-in, fixed-name E2E child process startup timing (never production telemetry).

Only the disposable, testing-mode E2E child can emit phase records. Output is
bounded fixed labels and finite durations: no paths, URLs, identities, or values
from the application are serialized. The parent reads its own private stderr
file after readiness, never the application's logging subsystem.
"""
from __future__ import annotations

import math
import os
import re
import sys
import time
from contextlib import contextmanager

PROFILE_ENV = "PRKS_E2E_STARTUP_PROFILE"
PREFIX = "PRKS_E2E_STARTUP_PHASE "
PHASES = frozenset({
    "startup_imports",
    "startup_dependency_gate",
    "startup_config",
    "startup_identity",
    "startup_jobs",
    "startup_storage_open",
    "startup_restore",
    "startup_bind",
    "startup_logging",
    "startup_worker_bind",
    "startup_thumbnail_cleanup",
    "startup_pdf_cleanup",
    "startup_text_reconcile",
    "startup_research_reconcile",
    "startup_staging_cleanup",
    "startup_socket",
})
_RECORD = re.compile(
    r"^PRKS_E2E_STARTUP_PHASE (startup_[a-z_]+(?:_failed)?) "
    r"([0-9]+(?:\.[0-9]{1,6})?)$"
)


def enabled() -> bool:
    """Reject accidental activation in non-E2E/non-testing processes."""
    return (
        os.environ.get(PROFILE_ENV) == "1"
        and os.environ.get("PRKS_E2E") == "1"
        and os.environ.get("PRKS_TESTING") == "1"
    )


def _record(name: str, seconds: float) -> None:
    if name.removesuffix("_failed") not in PHASES:
        raise ValueError("unknown startup profile phase")
    if not (math.isfinite(seconds) and seconds >= 0):
        raise ValueError("invalid startup profile duration")
    if enabled():
        print(f"{PREFIX}{name} {seconds:.6f}", file=sys.stderr, flush=True)


def record_imports(since: float) -> None:
    if enabled():
        _record("startup_imports", time.perf_counter() - since)


@contextmanager
def phase(name: str):
    """Preserve the wrapped stage and its exception; do nothing outside E2E."""
    if not enabled():
        yield
        return
    if name not in PHASES:
        raise ValueError("unknown startup profile phase")
    started = time.perf_counter()
    try:
        yield
    except BaseException:
        _record(name + "_failed", time.perf_counter() - started)
        raise
    else:
        _record(name, time.perf_counter() - started)


def parse_records(log: str) -> dict[str, float]:
    """Parse only exact, whitelisted records from the private child stderr.

    Invalid or duplicated lines are never interpreted as timing data.
    A partial profile remains obviously partial in the aggregate output.
    """
    results: dict[str, float] = {}
    for line in log.splitlines():
        m = _RECORD.fullmatch(line)
        if not m:
            continue
        name, raw = m.groups()
        if name.removesuffix("_failed") not in PHASES or name in results:
            continue
        number = float(raw)
        if math.isfinite(number):
            results[name] = number
    return results
