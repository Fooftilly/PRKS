#!/usr/bin/env python3
"""Strict privacy-safe report/Actions summary for the existing PostgreSQL profiler.

No new workload runner; validates completeness before presenting durations.
Input is the fixed $RUNNER_TEMP/pg-profile.json from scripts/test-postgres.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

OPERATIONS = frozenset(("works_list", "works_browse", "work_detail"))
STAT_KEYS = frozenset((
    "query_fingerprint", "calls", "total_exec_ms", "mean_exec_ms", "rows",
    "shared_blks_hit", "shared_blks_read", "shared_cache_hit_percent",
    "temp_blks_read", "temp_blks_written",
))
REQUIRED_KEYS = frozenset((
    "fixture_works", "operation", "repetitions", "completed_samples",
    "correctness_checks", "warmup_calls", "first_observed_call_ms",
    "first_call_context", "call_wall_p50_ms", "call_wall_p95_ms",
    "top_statements",
))


def nonnegative(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"non-numeric profiler value for {label}")
    num = float(value)
    if not math.isfinite(num) or num < 0:
        raise ValueError(f"invalid profiler measurement for {label}")
    return num


def validate(data: dict) -> list[dict]:
    """Never summarize a missing, truncated, or untrusted metrics report."""
    if data.get("tool") != "pg_stat_statements" or data.get("server_version_num", 0) < 180000:
        raise ValueError("not a disposable PostgreSQL 18 query report")
    if data.get("backend") != "postgresql_facade" or data.get("fixture") != "synthetic_works_only":
        raise ValueError("not the known synthetic PostgreSQL facade fixture")
    if data.get("fixture_version") != "works-v1":
        raise ValueError("unexpected synthetic fixture revision")
    if data.get("measurement") != "warm_facade_calls_not_http":
        raise ValueError("wall time cannot be represented as HTTP API latency")
    if data.get("readiness") != "disposable_pg18_identity_verified":
        raise ValueError("disposable PostgreSQL instance not verified")
    if data.get("query_fingerprints") != "run_local_only; no SQL text is collected":
        raise ValueError("query privacy contract changed")
    sizes = data.get("fixture_tiers")
    if (
        not isinstance(sizes, list) or not 2 <= len(sizes) <= 4
        or any(type(x) is not int or x < 1 or x > 500 for x in sizes)
        or sizes != sorted(set(sizes))
    ):
        raise ValueError("missing/invalid comparison fixture tiers")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != len(sizes) * len(OPERATIONS):
        raise ValueError("incomplete or duplicated profiler workload inventory")
    seen: set[tuple[int, str]] = set()
    for record in profiles:
        if not isinstance(record, dict) or not REQUIRED_KEYS <= set(record):
            raise ValueError("incomplete profiler row")
        key = (record["fixture_works"], record["operation"])
        if type(key[0]) is not int or key[0] not in sizes or key[1] not in OPERATIONS or key in seen:
            raise ValueError("unexpected/duplicate profiler operation")
        seen.add(key)
        count = record["repetitions"]
        if type(count) is not int or not 2 <= count <= 30:
            raise ValueError("invalid profiler sample count")
        if (record["completed_samples"] != count or record["warmup_calls"] != 2
            or record["correctness_checks"] != count + 3):
            raise ValueError("partial or unchecked profiler run")
        if record["first_call_context"] != "after_fixture_seed_not_cold_start":
            raise ValueError("first-observed call mislabeled as a cold start")
        for metric in ("first_observed_call_ms", "call_wall_p50_ms", "call_wall_p95_ms"):
            nonnegative(record[metric], metric)
        statements = record["top_statements"]
        if not isinstance(statements, list) or not 1 <= len(statements) <= 5:
            raise ValueError("unobserved SQL is not a successful measurement")
        for item in statements:
            if not isinstance(item, dict) or set(item) != STAT_KEYS:
                raise ValueError("unknown profile field: possible SQL text disclosure")
            if not re.fullmatch(r"[0-9a-f]{16}", str(item["query_fingerprint"])):
                raise ValueError("unrecognized query fingerprint format")
            for key_name in STAT_KEYS - {"query_fingerprint", "shared_cache_hit_percent"}:
                nonnegative(item[key_name], key_name)
            if item["shared_cache_hit_percent"] is not None:
                hit = nonnegative(item["shared_cache_hit_percent"], "shared_cache_hit_percent")
                if hit > 100:
                    raise ValueError("invalid cache hit rate")
    return sorted(profiles, key=lambda row: (row["fixture_works"], row["operation"]))


def render(data: dict) -> str:
    rows = validate(data)
    lines = [
        "## Synthetic PostgreSQL application read baseline (advisory)",
        "",
        "Backend: **PostgreSQL facade**, **not authenticated HTTP/API**. "
        "All measurements are successful validated synthetic read calls.",
        f"PostgreSQL server version: {data['server_version_num']} · "
        f"Fixture: {data['fixture_version']} · "
        f"Tiers: {', '.join(str(x) for x in data['fixture_tiers'])} Works",
        "",
        "First observed call is **after fixture seeding**, not a process or cache cold start. "
        "Warm p50/p95 exclude it and two warmups. "
        "Single-run runner noise is not proof of a regression.",
        "",
        "| Works | Facade read | First observed ms | Warm p50 ms | Warm p95 ms | Samples |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for x in rows:
        lines.append(
            f"| {x['fixture_works']} | {x['operation']} | "
            f"{x['first_observed_call_ms']:.3f} | {x['call_wall_p50_ms']:.3f} | "
            f"{x['call_wall_p95_ms']:.3f} | {x['completed_samples']} |"
        )
    lines += ["", "No query text, SQL literals, user documents or DB connection strings were retained."]
    return "\n".join(lines) + "\n"


def main() -> int:
    temp = os.environ.get("RUNNER_TEMP", "").strip()
    if not temp:
        raise RuntimeError("set RUNNER_TEMP to the disposable profiler report directory")
    payload = json.loads((Path(temp) / "pg-profile.json").read_text(encoding="utf-8"))
    summary = render(payload)
    print(summary)
    dest = os.environ.get("GITHUB_STEP_SUMMARY")
    if dest:
        with Path(dest).open("a", encoding="utf-8") as out:
            out.write(summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        print(f"Profiler summary refused incomplete evidence ({type(error).__name__}).")
        raise SystemExit(1) from None
