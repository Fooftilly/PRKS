#!/usr/bin/env python3
"""Opt-in, disposable PostgreSQL pg_stat_statements workload profiler (#858).

Run ONLY through scripts/test-postgres --profile. That wrapper starts its own
throwaway PostgreSQL 18 server with the extension preloaded, never a live
database. No representative SQL text, identifiers from research libraries,
database connection strings, or fixture payloads are emitted.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Any

# Only expose numeric aggregates and salted run-local query fingerprints.
# The salt never leaves this process, so cross-run IDs are not linkable.
_RUN_FINGERPRINT_KEY = secrets.token_bytes(32)
# Always request showtext=false: never make PostgreSQL load representative SQL.
_SNAPSHOT_SQL = """
SELECT queryid, calls, total_exec_time, mean_exec_time, rows,
       shared_blks_hit, shared_blks_read, temp_blks_read, temp_blks_written
FROM public.pg_stat_statements(false)
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND userid = (SELECT usesysid FROM pg_user WHERE usename = current_user)
  AND queryid IS NOT NULL AND calls > 0
ORDER BY total_exec_time DESC, calls DESC
LIMIT 5
"""
_RESET_SQL = """
SELECT public.pg_stat_statements_reset(
    0::oid, (SELECT oid FROM pg_database WHERE datname = current_database()), 0::bigint
)
"""


def sizes_arg(value: str) -> tuple[int, ...]:
    """A bounded ascending fixture progression, not arbitrary SQL/data input."""
    try:
        sizes = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("sizes must be comma-separated integers") from exc
    if not sizes or len(sizes) > 4 or any(n < 1 or n > 500 for n in sizes):
        raise argparse.ArgumentTypeError("use 1-4 sizes between 1 and 500")
    if tuple(sorted(set(sizes))) != sizes:
        raise argparse.ArgumentTypeError("sizes must be strictly ascending")
    return sizes


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("cannot summarize an empty sample")
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 3)


def project_statement(row: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelist numeric metrics; never expose PostgreSQL's representative SQL."""
    hits = int(row["shared_blks_hit"])
    reads = int(row["shared_blks_read"])
    return {
        # HMAC shields stable PostgreSQL query IDs against cross-run matching.
        "query_fingerprint": hmac.new(
            _RUN_FINGERPRINT_KEY, str(int(row["queryid"])).encode("ascii"), hashlib.sha256
        ).hexdigest()[:16],
        "calls": int(row["calls"]),
        "total_exec_ms": round(float(row["total_exec_time"]), 3),
        "mean_exec_ms": round(float(row["mean_exec_time"]), 3),
        "rows": int(row["rows"]),
        "shared_blks_hit": hits,
        "shared_blks_read": reads,
        "shared_cache_hit_percent": round(100 * hits / (hits + reads), 2)
        if hits + reads else None,
        "temp_blks_read": int(row["temp_blks_read"]),
        "temp_blks_written": int(row["temp_blks_written"]),
    }


def snapshot(stats: Any) -> list[dict[str, Any]]:
    return [project_statement(row) for row in stats.execute(_SNAPSHOT_SQL).fetchall()]


def benchmark(
    stats: Any, operation: Any, repetitions: int, verify_result: Any
) -> dict[str, Any]:
    """Validated facade operation with separate first-observed vs warmed samples.

    The first observed call is NOT a process/DB cold start: seeding has already
    used the same Database pool. Keep it out of warm p50/p95 and SQL stats.
    """
    first = time.perf_counter()
    verify_result(operation())
    first_ms = (time.perf_counter() - first) * 1000
    for _ in range(2):
        verify_result(operation())
    stats.execute(_RESET_SQL)
    if snapshot(stats):
        raise RuntimeError("profiler maintenance contaminated workload statistics")
    durations = []
    for _ in range(repetitions):
        start = time.perf_counter()
        verify_result(operation())
        durations.append((time.perf_counter() - start) * 1000)
    statements = snapshot(stats)
    if not statements:
        raise RuntimeError("profile contained no observed workload SQL")
    return {
        "repetitions": repetitions,
        "completed_samples": len(durations),
        "warmup_calls": 2,
        "correctness_checks": repetitions + 3,
        "first_observed_call_ms": round(first_ms, 3),
        "first_call_context": "after_fixture_seed_not_cold_start",
        "call_wall_p50_ms": percentile(durations, 0.5),
        "call_wall_p95_ms": percentile(durations, 0.95),
        "top_statements": statements,
    }


def assert_works_count(value: Any, expected: int) -> None:
    """Readiness and correctness before durations are accepted as useful."""
    if not isinstance(value, list) or len(value) != expected:
        raise RuntimeError("synthetic Work catalog count does not match fixture")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in value):
        raise RuntimeError("synthetic Work catalog returned malformed summaries")


def assert_work_detail(value: Any, work_id: str) -> None:
    if not isinstance(value, dict) or value.get("id") != work_id:
        raise RuntimeError("synthetic Work detail does not match seeded Work")



def run(sizes: tuple[int, ...], repetitions: int) -> dict[str, Any]:
    nonce = os.environ.get("PRKS_PG_PROFILE_NONCE", "")
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise RuntimeError("run through scripts/test-postgres --profile only")

    import psycopg
    from psycopg.rows import dict_row

    from backend.db.config import DatabaseConfig
    from backend.db.database import Database
    from backend.pg_facade import PostgresLibrary
    from backend.pg_facade import context as facade_context
    from backend.storage.config import StorageConfig
    from tests.pg.facade_context import provision_actor
    from tests.pg.harness import get_harness

    harness = get_harness()
    dbname = harness.create_database("profile")
    try:
        # Disable tracking for this administrative connection at startup;
        # otherwise the reset SELECT is itself recorded after its reset.
        # The independent facade pool retains its ordinary top-level tracking.
        with psycopg.connect(harness.conninfo(dbname), autocommit=True,
                             options="-c pg_stat_statements.track=none",
                             row_factory=dict_row) as stats:
            # Prove the wrapper-created cluster identity before installing an
            # extension or resetting server-shared statistics. Caller-controlled
            # environment flags are insufficient to authorize the reset.
            cluster = stats.execute("SHOW cluster_name").fetchone()["cluster_name"]
            if cluster != f"prks-profile-{nonce}":
                raise RuntimeError("database server is not this disposable profile cluster")
            preload = stats.execute(
                "SELECT current_setting('shared_preload_libraries') AS loaded"
            ).fetchone()["loaded"]
            if "pg_stat_statements" not in preload.split(","):
                raise RuntimeError("pg_stat_statements is not preloaded on the disposable server")
            stats.execute("CREATE EXTENSION pg_stat_statements WITH SCHEMA public")
            version = stats.execute("SHOW server_version_num").fetchone()["server_version_num"]
            with Database(DatabaseConfig(conninfo=harness.conninfo(dbname), role="test",
                                         pool_min=0, pool_max=4)) as database:
                actor = provision_actor(database)
                with tempfile.TemporaryDirectory(prefix="prks-profile-root-") as temp:
                    storage = StorageConfig.for_testing(temp)
                    os.makedirs(storage.pdfs_dir, exist_ok=True)
                    library = PostgresLibrary(database, storage=storage)
                    fixture_ids: list[str] = []
                    output = []
                    with facade_context.acting_as(actor.context):
                        for size in sizes:
                            for i in range(len(fixture_ids), size):
                                fixture_ids.append(library.add_work(f"Fixture Work {i:04d}"))
                            # These are real PG facade read paths. The fixture is
                            # deterministic, isolated, synthetic and disposable.
                            work_id = fixture_ids[-1]
                            workloads = (
                                ("works_list", library.get_all_works,
                                 lambda value: assert_works_count(value, size)),
                                ("works_browse", library.get_works_browse_catalog,
                                 lambda value: assert_works_count(value, size)),
                                ("work_detail", lambda: library.get_work(work_id),
                                 lambda value: assert_work_detail(value, work_id)),
                            )
                            for label, operation, verify in workloads:
                                result = benchmark(stats, operation, repetitions, verify)
                                output.append({"fixture_works": size, "operation": label, **result})
                    return {
                        "tool": "pg_stat_statements",
                        "server_version_num": int(version),
                        "fixture": "synthetic_works_only",
                        "fixture_version": "works-v1",
                        "fixture_tiers": list(sizes),
                        "backend": "postgresql_facade",
                        "measurement": "warm_facade_calls_not_http",
                        "readiness": "disposable_pg18_identity_verified",
                        "query_fingerprints": "run_local_only; no SQL text is collected",
                        "profiles": output,
                    }
    finally:
        # The pool/connection close before the harness forcibly drops its own
        # database; no user database name is supplied anywhere by the CLI.
        harness.drop_database(dbname)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=sizes_arg, default=(12, 48, 128))
    parser.add_argument("--repetitions", type=int, default=8)
    args = parser.parse_args(argv)
    if not 2 <= args.repetitions <= 30:
        parser.error("--repetitions must be between 2 and 30")
    try:
        report = run(args.sizes, args.repetitions)
    except Exception as exc:
        # Fail closed without printing connection strings, query text, or data.
        print(f"Disposable PostgreSQL profiler failed ({type(exc).__name__}).", file=__import__("sys").stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
