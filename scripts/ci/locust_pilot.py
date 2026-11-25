#!/usr/bin/env python3
"""Bounded read-only Locust load pilot (#859; #765 T08). Advisory and manual.

Starts a disposable ``--testing`` PRKS server on loopback (its own database on
the ``PRKS_TEST_DATABASE_URL`` PostgreSQL server, loopback-owner sessions,
synthetic fixtures), seeds a small or medium synthetic library, checks each
route once, then runs ``scripts/ci/locustfile_readonly.py`` headless with a
fixed user count, spawn rate and duration. Every answer is checked for its
meaning, so errors are never counted as fast successes.

The summary is bounded and privacy-safe: tier, user/spawn/duration, PRKS and
PostgreSQL versions, and per route the request and failure counts, p50, p95,
max and requests per second, the load generator's own CPU share (so a saturated
generator is not mistaken for a slow server) and the PostgreSQL activity of the
pilot's database during the run. No titles, ids, bodies or URLs with values.
Never targets anything but the server it started.

``--compare BASELINE.json`` adds the p95 and throughput ratios of this run to
an earlier summary of the same tier, the comparison method for two runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOCUSTFILE = ROOT / "scripts" / "ci" / "locustfile_readonly.py"
# Extra synthetic Works per tier, on top of the UX-tour fixture library.
TIERS = {"small": 8, "medium": 64}
SEARCH_TERM = "Locust"
USERS = 5
SPAWN_RATE = 5
DURATION_SECONDS = 30
CLI_TIMEOUT = DURATION_SECONDS + 60
ROUTES = (
    "GET /api/works",
    "GET /api/works/:id",
    "GET /api/search",
    "GET /api/tags",
    "GET /api/research-graph",
)
# The search answers at most this many Works; the check asks for no more.
SEARCH_HIT_FLOOR = 8
# Above this share of one CPU the generator, not the server, may set the pace.
GENERATOR_CPU_LIMIT = 0.8
# Cumulative pg_stat_database counters reported as the run's database impact.
DB_COUNTERS = ("xact_commit", "xact_rollback", "blks_read", "blks_hit", "tup_returned", "tup_fetched")


def require_loopback(origin: str) -> int:
    """The port of an ``http://127.0.0.1:<port>`` origin; anything else is refused."""
    parsed = urlsplit(origin)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("the load pilot targets only its own loopback server")
    return parsed.port


def command(binary: str, origin: str, csv_prefix: str) -> list[str]:
    require_loopback(origin)
    return [
        binary, "-f", str(LOCUSTFILE), "--headless",
        "--users", str(USERS), "--spawn-rate", str(SPAWN_RATE), "--run-time", f"{DURATION_SECONDS}s",
        "--host", origin, "--csv", csv_prefix, "--only-summary", "--stop-timeout", "5",
        "--loglevel", "WARNING", "--exit-code-on-error", "1",
    ]


def seeder(extra: int) -> Callable[[str], dict[str, Any]]:
    def seed(storage_root: str) -> dict[str, Any]:
        from backend.domain.organization import tag_commands
        from backend.storage.config import StorageConfig
        from tests.acting_context import current_context
        from tests.e2e.fixtures import open_library
        from tests.ux_tour.fixtures import seed_ux_tour_library

        ids = seed_ux_tour_library(storage_root)
        db = open_library(StorageConfig.for_testing(storage_root))
        ids["load_works"] = [
            db.add_work(title=f"{SEARCH_TERM} load work {index:03d}", abstract="Synthetic load fixture.",
                        doc_type="article", status="Planned")
            for index in range(extra)
        ]
        # The whole seeded library, so a catalog or tag list missing any fixture fails.
        ids["catalog_size"] = len(db.get_all_works())
        ids["tag_count"] = len(tag_commands.all_tags(db.database, current_context()))
        return ids

    return seed


def summarize(stats_csv: Path) -> dict[str, dict[str, Any]]:
    """Per-route numbers from Locust's ``*_stats.csv``; the aggregate row is ``total``."""
    out: dict[str, dict[str, Any]] = {}
    with stats_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = row["Name"] if row["Name"] != "Aggregated" else "total"
            if name != "total":
                name = f"{row['Type']} {row['Name']}"
            out[name] = {
                "requests": int(row["Request Count"]),
                "failures": int(row["Failure Count"]),
                "p50_ms": float(row["50%"] or 0),
                "p95_ms": float(row["95%"] or 0),
                "max_ms": float(row["Max Response Time"] or 0),
                "rps": round(float(row["Requests/s"] or 0), 2),
            }
    return out


def problems(routes: dict[str, dict[str, Any]], generator_cpu_share: float = 0.0) -> list[str]:
    """Why a run does not count: a route never exercised, any failed request, or a saturated generator."""
    found = []
    for route in ROUTES:
        numbers = routes.get(route)
        if numbers is None or numbers["requests"] == 0:
            found.append(f"no requests: {route}")
        elif numbers["failures"]:
            found.append(f"{numbers['failures']} failed: {route}")
    unexpected = sorted(set(routes) - set(ROUTES) - {"total"})
    if unexpected:
        found.append("unexpected routes: " + ", ".join(unexpected))
    if generator_cpu_share > GENERATOR_CPU_LIMIT:
        found.append(f"load generator saturated: {generator_cpu_share:.2f} of one CPU")
    return found


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Per route, this run's p95 and requests per second as ratios of ``baseline``'s.

    Only numbers and one boolean come out: no text from the baseline file is
    copied into the new summary.
    """
    if (baseline.get("pilot"), baseline.get("tier")) != (current["pilot"], current["tier"]):
        raise ValueError("a baseline compares only with a run of the same pilot and tier")
    ratios: dict[str, dict[str, float | None]] = {}
    for route, now in current["routes"].items():
        then = baseline.get("routes", {}).get(route)
        if not then:
            continue
        then_p95, then_rps = float(then["p95_ms"]), float(then["rps"])
        if not (math.isfinite(then_p95) and math.isfinite(then_rps)):
            raise ValueError("baseline route metrics must be finite numbers")
        ratios[route] = {
            "p95": round(now["p95_ms"] / then_p95, 2) if then_p95 else None,
            "rps": round(now["rps"] / then_rps, 2) if then_rps else None,
        }
    version = current.get("prks_version")
    same = version not in (None, "unknown") and baseline.get("prks_version") == version
    return {"same_prks_version": same, "routes": ratios}


def database_activity(database: str) -> dict[str, int]:
    """The cumulative ``pg_stat_database`` counters of the pilot's own database."""
    import psycopg
    from psycopg import sql

    query = sql.SQL("SELECT {} FROM pg_stat_database WHERE datname = %s").format(
        sql.SQL(", ").join(sql.Identifier(name) for name in DB_COUNTERS))
    with psycopg.connect(os.environ["PRKS_TEST_DATABASE_URL"], connect_timeout=10) as conn:
        row = conn.execute(query, (database,)).fetchone()
    return dict(zip(DB_COUNTERS, (int(value or 0) for value in row))) if row else dict.fromkeys(DB_COUNTERS, 0)


def postgres_version() -> str:
    import psycopg

    with psycopg.connect(os.environ["PRKS_TEST_DATABASE_URL"], connect_timeout=10) as conn:
        row = conn.execute("SHOW server_version").fetchone()
    return str(row[0]).split()[0] if row else "unknown"


def prks_version() -> str:
    try:
        from backend.domain.maintenance.format import PRKS_VERSION

        return str(PRKS_VERSION)
    except Exception:  # noqa: BLE001 - the summary only labels the run
        return "unknown"


def warm_up(origin: str, ids: dict[str, Any]) -> int:
    """Each route once, checked, before any load: a broken fixture fails here, not as load errors.

    Answers the catalog size, which the summary reports as the library size.
    """
    import http.client

    port = require_loopback(origin)
    work_ids = ids["load_works"] + [ids["work_a"]]
    hits = min(len(ids["load_works"]), SEARCH_HIT_FLOOR)
    for path, check in (
        ("/api/works", lambda body: isinstance(body, list) and len(body) == ids["catalog_size"]),
        (f"/api/works/{work_ids[0]}", lambda body: body.get("id") == work_ids[0]),
        (f"/api/search?q={SEARCH_TERM}", lambda body: isinstance(body, list) and len(body) >= hits),
        ("/api/tags", lambda body: isinstance(body, list) and len(body) == ids["tag_count"] > 0),
        ("/api/research-graph", lambda body: bool(body.get("nodes"))),
    ):
        # A plain HTTP connection to loopback: no URL, so no scheme to choose.
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status != 200:
                raise AssertionError(f"warm-up got {response.status} for a seeded route: {path.split('?')[0]}")
            body = json.loads(response.read())
        finally:
            connection.close()
        if not check(body):
            raise AssertionError(f"warm-up check failed for a seeded route: {path.split('?')[0]}")
    return int(ids["catalog_size"])


def summary_path(path: Path) -> Path:
    """A ``.json`` summary path inside the working directory; anything else is refused."""
    root = Path.cwd().resolve()
    resolved = (root / path).resolve()
    if resolved.suffix != ".json" or not resolved.is_relative_to(root):
        raise ValueError("summary paths must be .json files inside the working directory")
    return resolved


def run(tier: str, out: Path | None, baseline: Path | None = None) -> int:
    out = summary_path(out) if out is not None else None
    baseline = summary_path(baseline) if baseline is not None else None
    if os.environ.get("PRKS_AUTH", "").strip() or os.environ.get("PRKS_DATABASE_URL", "").strip():
        raise RuntimeError("refusing an inherited PRKS_AUTH or PRKS_DATABASE_URL: the pilot uses only its own server")
    if not os.environ.get("PRKS_TEST_DATABASE_URL"):
        raise RuntimeError("PRKS_TEST_DATABASE_URL must name a disposable PostgreSQL server")
    prior = json.loads(baseline.read_text(encoding="utf-8")) if baseline is not None else None
    binary = shutil.which("locust")
    binary = os.path.abspath(binary) if binary else None  # the CLI runs from a temporary directory
    if not binary:
        raise RuntimeError("the pinned Locust CLI is not installed")
    from tests.e2e.harness import AppServer

    extra = TIERS[tier]
    server = AppServer(seed_fn=seeder(extra))
    started = time.monotonic()
    server.start()
    try:
        catalog = warm_up(server.origin, server.ids)
        work_ids = server.ids["load_works"] + [server.ids["work_a"]]
        env = dict(os.environ, PRKS_LOCUST_WORK_IDS=",".join(work_ids), PRKS_LOCUST_SEARCH=SEARCH_TERM,
                   PRKS_LOCUST_WORKS=str(catalog), PRKS_LOCUST_TAGS=str(server.ids["tag_count"]),
                   PRKS_LOCUST_MIN_SEARCH_HITS=str(min(extra, SEARCH_HIT_FLOOR)))
        if server.library is None or server.proc is None:
            raise RuntimeError("the pilot server did not start")
        database = server.library.database
        before = database_activity(database)
        with tempfile.TemporaryDirectory(prefix="prks-locust-") as directory:
            prefix = os.path.join(directory, "run")
            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            result = subprocess.run(command(binary, server.origin, prefix), cwd=directory, env=env,
                                    capture_output=True, text=True, timeout=CLI_TIMEOUT, check=False)
            spent = resource.getrusage(resource.RUSAGE_CHILDREN)
            stats = Path(prefix + "_stats.csv")
            routes = summarize(stats) if stats.is_file() else {}
        generator_cpu_share = round((spent.ru_utime + spent.ru_stime - children.ru_utime - children.ru_stime)
                                    / DURATION_SECONDS, 2)
        # Stopping the server closes its connections, and a closing backend
        # flushes its statistics, so the counters below include the whole run.
        server.proc.terminate()
        server.proc.wait(timeout=30)
        after = database_activity(database)
    finally:
        server.stop()
    found = problems(routes, generator_cpu_share)
    if result.returncode and not found:
        found.append(f"locust exited {result.returncode}")
    summary = {
        "pilot": "locust-readonly", "tier": tier, "synthetic_works": catalog,
        "users": USERS, "spawn_rate": SPAWN_RATE, "duration_s": DURATION_SECONDS,
        "prks_version": prks_version(), "postgresql": postgres_version(),
        "elapsed_s": round(time.monotonic() - started, 1), "routes": routes, "problems": found,
        "generator_cpu_share": generator_cpu_share,
        "database": {name: after[name] - before[name] for name in DB_COUNTERS},
    }
    if prior is not None:
        summary["comparison"] = compare(summary, prior)
    text = json.dumps(summary, indent=1, sort_keys=True)
    if out is not None:
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=sorted(TIERS), default="small")
    parser.add_argument("--out", type=Path, help="write the JSON summary here as well")
    parser.add_argument("--compare", type=Path, help="an earlier summary of the same tier to compare with")
    options = parser.parse_args(argv)
    try:
        return run(options.tier, options.out, options.compare)
    except (ValueError, RuntimeError, AssertionError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"[locust] pilot could not complete: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
