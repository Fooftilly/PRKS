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


def validate_scope(data: dict) -> list[int]:
    """Prove that the source is a disposable, known schema and fixture tier."""
    scope_keys = {
        "tool", "server_version_num", "fixture", "fixture_version",
        "fixture_tiers", "backend", "measurement", "readiness",
        "query_fingerprints", "profiles",
    }
    if set(data) != scope_keys:
        raise ValueError("unknown profile metadata: possible private field disclosure")
    if data.get("tool") != "pg_stat_statements":
        raise ValueError("not a PostgreSQL statement profile")
    if nonnegative(data.get("server_version_num"), "server_version_num") < 180000:
        raise ValueError("PostgreSQL 18 required")
    expected = {
        "backend": "postgresql_facade",
        "fixture": "synthetic_works_only",
        "fixture_version": "works-v1",
        "measurement": "warm_facade_calls_not_http",
        "readiness": "disposable_pg18_identity_verified",
        "query_fingerprints": "run_local_only; no SQL text is collected",
    }
    if any(data.get(key) != value for key, value in expected.items()):
        raise ValueError("unrecognized PostgreSQL fixture/measurement scope")
    tiers = data.get("fixture_tiers")
    if (
        not isinstance(tiers, list) or not 2 <= len(tiers) <= 4
        or any(type(n) is not int or n < 1 or n > 500 for n in tiers)
        or tiers != sorted(set(tiers))
    ):
        raise ValueError("missing/invalid comparison fixture tiers")
    return tiers


def validate_statement(item: dict) -> None:
    if not isinstance(item, dict) or set(item) != STAT_KEYS:
        raise ValueError("unknown profile field: possible SQL text disclosure")
    if not re.fullmatch(r"[0-9a-f]{16}", str(item["query_fingerprint"])):
        raise ValueError("unrecognized query fingerprint format")
    for key in STAT_KEYS - {"query_fingerprint", "shared_cache_hit_percent"}:
        nonnegative(item[key], key)
    if item["shared_cache_hit_percent"] is not None:
        if nonnegative(item["shared_cache_hit_percent"], "shared_cache_hit_percent") > 100:
            raise ValueError("invalid cache hit rate")


def validate_row(record: dict, tiers: list[int]) -> tuple[int, str]:
    if not isinstance(record, dict) or set(record) != REQUIRED_KEYS:
        raise ValueError("incomplete profiler row")
    works, operation = record["fixture_works"], record["operation"]
    if type(works) is not int or works not in tiers or operation not in OPERATIONS:
        raise ValueError("unexpected profiler operation/fixture")
    samples = record["repetitions"]
    if type(samples) is not int or not 2 <= samples <= 30:
        raise ValueError("invalid profiler sample count")
    if (
        record["completed_samples"] != samples or record["warmup_calls"] != 2
        or record["correctness_checks"] != samples + 3
    ):
        raise ValueError("partial or unchecked profiler run")
    if record["first_call_context"] != "first_call_of_operation_not_cold_start":
        raise ValueError("first-observed call mislabeled as a cold start")
    for metric in ("first_observed_call_ms", "call_wall_p50_ms", "call_wall_p95_ms"):
        nonnegative(record[metric], metric)
    statements = record["top_statements"]
    if not isinstance(statements, list) or not 1 <= len(statements) <= 5:
        raise ValueError("unobserved SQL is not a successful measurement")
    for item in statements:
        validate_statement(item)
    return works, operation


def validate(data: dict) -> list[dict]:
    """Never summarize a partial, incorrect, or privacy-unsafe measurement."""
    tiers = validate_scope(data)
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != len(tiers) * len(OPERATIONS):
        raise ValueError("incomplete or duplicated profiler workload inventory")
    seen: set[tuple[int, str]] = set()
    for record in profiles:
        key = validate_row(record, tiers)
        if key in seen:
            raise ValueError("duplicate profiler operation")
        seen.add(key)
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
        "First recorded call is the **first call of each operation**; previous operations and seeding may have warmed caches, so none is a process/DB cold start. "
        "Warm p50/p95 exclude it and two warmups. "
        "Single-run runner noise is not proof of a regression.",
        "",
        "| Works | Facade read | First operation call ms | Warm p50 ms | Warm p95 ms | Samples |",
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
    # Backwards-compatible, privacy-validated statement evidence remains
    # inspectable in the Actions log without uploading a new artifact.
    print(json.dumps(payload, indent=2, sort_keys=True))
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
