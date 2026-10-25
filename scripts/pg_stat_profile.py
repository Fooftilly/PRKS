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

    The first call of each operation is NOT a process/DB cold start: seeding
    and prior workload families have used the same Database pool. Keep it out of warm p50/p95 and SQL stats.
    """
    first = time.perf_counter()
    first_result = operation()
    first_ms = (time.perf_counter() - first) * 1000
    verify_result(first_result)
    for _ in range(2):
        verify_result(operation())
    stats.execute(_RESET_SQL)
    if snapshot(stats):
        raise RuntimeError("profiler maintenance contaminated workload statistics")
    durations = []
    for _ in range(repetitions):
        start = time.perf_counter()
        value = operation()
        elapsed = (time.perf_counter() - start) * 1000
        verify_result(value)
        durations.append(elapsed)
    statements = snapshot(stats)
    if not statements:
        raise RuntimeError("profile contained no observed workload SQL")
    return {
        "repetitions": repetitions,
        "completed_samples": len(durations),
        "warmup_calls": 2,
        "correctness_checks": repetitions + 3,
        "first_observed_call_ms": round(first_ms, 3),
        "first_call_context": "first_call_of_operation_not_cold_start",
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



def expected_recent_work_ids(
    catalog: Any, fixture_ids: Sequence[str], limit: int
) -> tuple[str, ...]:
    """Derive newest IDs independently of the query's returned row order.

    The PostgreSQL path orders by created_at at *second* precision descending,
    with a byte-wise ID ascending tie-breaker. Inserting fixtures quickly can
    produce ties: insertion order alone is not a valid expected result.
    """
    if not isinstance(catalog, list) or len(catalog) != len(fixture_ids):
        raise RuntimeError("synthetic Work catalog is incomplete")
    timestamps: dict[str, str] = {}
    for row in catalog:
        if not isinstance(row, dict):
            raise RuntimeError("synthetic Work catalog contains malformed rows")
        work_id, created_at = row.get("id"), row.get("created_at")
        if (
            not isinstance(work_id, str) or not isinstance(created_at, str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} "
                                r"[0-9]{2}:[0-9]{2}:[0-9]{2}", created_at)
            or work_id in timestamps
        ):
            raise RuntimeError("synthetic Work catalog has invalid creation metadata")
        timestamps[work_id] = created_at
    if set(timestamps) != set(fixture_ids):
        raise RuntimeError("synthetic Work catalog has unexpected IDs")
    # Stable sorts implement SQL's descending seconds and ascending ID ties.
    by_id = sorted(timestamps)
    newest_first = sorted(by_id, key=lambda wid: timestamps[wid], reverse=True)
    return tuple(newest_first[:limit])


def assert_bounded_works(value: Any, expected_ids: tuple[str, ...]) -> None:
    """Check the exact newest IDs and order, including timestamp ties."""
    if not isinstance(value, list) or len(value) != len(expected_ids):
        raise RuntimeError("bounded synthetic Work browse has the wrong count")
    ids = [row.get("id") if isinstance(row, dict) else None for row in value]
    if ids != list(expected_ids):
        raise RuntimeError("bounded synthetic Work browse returned wrong newest IDs")


def assert_ordered_work_ids(value: Any, expected: tuple[str, ...]) -> None:
    """Ordered batch fetch must preserve exactly the requested IDs and shape."""
    if not isinstance(value, list) or len(value) != len(expected):
        raise RuntimeError("synthetic batch lookup has the wrong count")
    ids = [item.get("id") if isinstance(item, dict) else None for item in value]
    if ids != list(expected):
        raise RuntimeError("synthetic batch lookup returned wrong or reordered IDs")


def verify_disposable_cluster(stats: Any, nonce: str) -> int:
    """Refuse an unproven server before profile DDL, resets or test DB creation."""
    cluster = stats.execute("SHOW cluster_name").fetchone()["cluster_name"]
    if cluster != f"prks-profile-{nonce}":
        raise RuntimeError("database server is not this disposable profile cluster")
    preload = stats.execute(
        "SELECT current_setting('shared_preload_libraries') AS loaded"
    ).fetchone()["loaded"]
    if "pg_stat_statements" not in {str(item).strip() for item in str(preload).split(",")}:
        raise RuntimeError("pg_stat_statements is not preloaded on the disposable server")
    version = int(stats.execute("SHOW server_version_num").fetchone()["server_version_num"])
    if version < 180000:
        raise RuntimeError("PostgreSQL 18 is required for disposable profiling")
    return version


def create_verified_profile_database(
    harness: Any, nonce: str, connect: Any, row_factory: Any
) -> str:
    """Read-only maintenance preflight MUST succeed before CREATE DATABASE.

    The harness's maintenance URL is validated by get_harness(); using its
    connection info avoids a second, independently selected server target.
    """
    with connect(
        harness._admin_conninfo, autocommit=True,
        options="-c pg_stat_statements.track=none", row_factory=row_factory
    ) as maintenance:
        verify_disposable_cluster(maintenance, nonce)
    return harness.create_database("profile")


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
    # The harness has not created any database yet. Check the cluster identity
    # read-only on its maintenance connection before migrations/CREATE DATABASE.
    dbname = create_verified_profile_database(harness, nonce, psycopg.connect, dict_row)
    try:
        # Disable tracking for this administrative connection at startup;
        # otherwise the reset SELECT is itself recorded after its reset.
        # The independent facade pool retains its ordinary top-level tracking.
        with psycopg.connect(harness.conninfo(dbname), autocommit=True,
                             options="-c pg_stat_statements.track=none",
                             row_factory=dict_row) as stats:
            # Recheck on the just-created, harness-owned database before any
            # extension DDL or server-shared pg_stat_statements reset.
            version = verify_disposable_cluster(stats, nonce)
            stats.execute("CREATE EXTENSION pg_stat_statements WITH SCHEMA public")
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
                            # T07: only profile read paths already ported to the PG facade.
                            # Search, graph and generic offset pagination are NOT ported.
                            bounded_limit = min(size, 10)
                            # Capture the already-timed first list result in its
                            # verifier: do not pre-read the catalog and turn the
                            # first-observed list measurement into a second call.
                            recent_expectation: list[tuple[str, ...]] = []

                            def verify_list(value: Any) -> None:
                                assert_works_count(value, size)
                                expected = expected_recent_work_ids(
                                    value, fixture_ids, bounded_limit
                                )
                                if recent_expectation and recent_expectation[0] != expected:
                                    raise RuntimeError("synthetic catalog changed during profiling")
                                if not recent_expectation:
                                    recent_expectation.append(expected)

                            def verify_recent(value: Any) -> None:
                                if len(recent_expectation) != 1:
                                    raise RuntimeError("missing validated list baseline")
                                assert_bounded_works(value, recent_expectation[0])
                            # Deliberately non-natural: neither SQL id order nor
                            # fixture insertion order should satisfy batch lookup.
                            by_id = tuple(sorted(fixture_ids[:bounded_limit]))
                            batch_ids = by_id[::2] + by_id[1::2]
                            if batch_ids == tuple(fixture_ids[:bounded_limit]):
                                batch_ids = batch_ids[1:] + batch_ids[:1]
                            workloads = (
                                ("works_list", library.get_all_works, verify_list),
                                ("works_browse", library.get_works_browse_catalog,
                                 lambda value: assert_works_count(value, size)),
                                ("work_detail", lambda: library.get_work(work_id),
                                 lambda value: assert_work_detail(value, work_id)),
                                ("works_recent_bounded",
                                 lambda: library.get_recently_added_browse(limit=bounded_limit),
                                 verify_recent),
                                ("works_batch_by_ids",
                                 lambda: library.get_work_summaries_by_ids_ordered(list(batch_ids)),
                                 lambda value: assert_ordered_work_ids(value, batch_ids)),
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
