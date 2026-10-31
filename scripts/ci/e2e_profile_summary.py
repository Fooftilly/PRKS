#!/usr/bin/env python3
"""Render an opt-in E2E profile as an aggregate, privacy-safe Actions summary.

Consume the existing schema-v2 serial benchmark report. Never read browser
traces, test IDs, application content, or arbitrary diagnostic text.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

_PHASE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHARD = re.compile(r"^([1-9][0-9]*)/([1-9][0-9]*)$")

# Fixed diagnostic priority: distinguish PostgreSQL overhead from the already
# profiled fixture/server/browser phases. Other safe phase names follow below.
_PRIORITY = (
    "postgres_provision",
    "postgres_provision_failed",
    "postgres_drop",
    "postgres_drop_failed",
    "seed_build",
    "seed_clone",
    "server_start",
    "server_spawn",
    "server_http_ready",
    "server_stop",
    "browser_context",
    "app_ready",
    "async_wait",
    "request_routing",
    "routing_decision",
)


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _seconds(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _validated_profile(payload: object) -> tuple[int, float, dict[str, float], dict]:
    """Reject malformed reports without interpolating possibly private values."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("profile schema version or envelope invalid")
    modes = payload.get("benchmark_modes")
    if (
        not isinstance(modes, list)
        or "profile" not in modes
        or not all(isinstance(mode, str) and _PHASE_NAME.fullmatch(mode.replace("-", "_"))
                   for mode in modes)
        or payload.get("workers") != 1
        or type(payload.get("workers")) is not int
    ):
        raise ValueError("profile benchmark mode or worker count invalid")
    counts = [payload.get(key) for key in ("tests", "failures", "errors", "skipped")]
    if not all(_nonnegative_int(x) for x in counts) or counts[0] == 0 or any(counts[1:]):
        raise ValueError("profile test counts invalid or run incomplete")
    wall = payload.get("wall_seconds")
    if not _seconds(wall) or wall == 0:
        raise ValueError("profile wall duration invalid")
    nav = payload.get("navigation_samples")
    if (
        not isinstance(nav, dict)
        or set(nav) != {"attempted", "captured"}
        or not all(_nonnegative_int(v) for v in nav.values())
        or nav["captured"] > nav["attempted"]
    ):
        raise ValueError("profile navigation counters invalid")
    phases = payload.get("phase_seconds")
    if (
        not isinstance(phases, dict)
        or not phases
        or len(phases) > 200
        or any(
            not isinstance(key, str)
            or not _PHASE_NAME.fullmatch(key)
            or not _seconds(seconds)
            for key, seconds in phases.items()
        )
    ):
        raise ValueError("profile phase data invalid")
    return counts[0], float(wall), {k: float(v) for k, v in phases.items()}, nav


def render_summary(payload: object, *, shard: str) -> str:
    match = _SHARD.fullmatch(shard)
    if not match or int(match[1]) > int(match[2]) or int(match[2]) > 12:
        raise ValueError("profile shard label invalid")
    tests, wall, phases, nav = _validated_profile(payload)
    names = [name for name in _PRIORITY if name in phases]
    names.extend(sorted(set(phases) - set(_PRIORITY)))
    lines = [
        f"### Manual E2E infrastructure profile — shard {shard}",
        "",
        f"**{tests} passing tests**, {wall:.2f}s serial runner wall time. "
        "Recorded only for opt-in manual profiling.",
        "",
        "| Phase (inclusive unless noted) | Total seconds | Seconds/test |",
        "| --- | ---: | ---: |",
    ]
    for name in names:
        value = phases[name]
        lines.append(f"| \`{name}\` | {value:.3f} | {value / tests:.3f} |")
    for name in ("postgres_provision", "postgres_drop"):
        if name not in phases:
            lines.append(f"| \`{name}\` | not observed | — |")
    lines.extend([
        "",
        f"Browser navigation samples: {nav['captured']}/{nav['attempted']}.",
        "",
        "**Interpretation:** Nested phases overlap their parents (notably "
        "`postgres_drop` inside `server_stop`, and server spawn/readiness "
        "inside `server_start`). Do not add the rows or call them wall-clock "
        "partitions. An absent phase is unobserved, not measured as zero. "
        "This single shard is not a baseline-vs-change experiment. "
        "No test IDs, fixtures, traces, URLs or database names are included.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Existing schema-v2 serial benchmark JSON")
    parser.add_argument("--shard", required=True, help="1-based INDEX/TOTAL label")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
        summary = render_summary(payload, shard=args.shard)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        parser.exit(2, "E2E manual profile summary invalid or missing; no metrics reported\n")
    print(summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
