"""On-demand test-suite inventory and timing metrics.

Generates live counts from discovery + policy + timing files. This is an
operator/agent aid, not a coverage gate and not a substitute for mapping which
layer owns a contract (KEEP / SPLIT / MOVE). Feature-group counts overlap —
do not sum them as if they were a partition of the suite.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.loader import _FailedTest


class DiscoveryError(Exception):
    """unittest discovery hit import/load failures; inventory must not exit 0."""

    def __init__(self, message, *, errors=None, failed_ids=None):
        super().__init__(message)
        self.errors = list(errors or [])
        self.failed_ids = list(failed_ids or [])

from tests.e2e.policy import (
    FEATURES,
    SMOKE_TEST_IDS,
    STRESS_SCRIPTS,
    feature_names,
    select_by_selectors,
    select_features,
)
from tests.e2e.sharding import (
    BASELINE_TIMINGS_PATH,
    DEFAULT_TEST_SECONDS,
    TIMINGS_PATH,
    estimate_seconds,
    load_timings,
    merge_timing_sources,
    module_of,
)

# Node selftest runners invoked by Python frontend unit wrappers.
SELFTEST_RUNNER_GLOB = "run_*selftest*.js"
# Supporting / alternate selftest modules under tests/browser/ (not runners).
SELFTEST_MODULE_GLOB = "*selftest*.js"

COVERAGE_BOUNDARY_NOTE = (
    "Coverage boundaries matter more than raw counts. Chromium E2E owns DOM, "
    "focus, layout, navigation/history, service worker, real IndexedDB across "
    "reload, offline browser state, and renderer integration. Prefer Node "
    "selftests, API, and Python unit tests for protocol and state-machine "
    "branches. Do not optimize toward count alone."
)


def features_for_test_id(test_id: str) -> list[str]:
    """Feature groups whose selectors match this test id (may be empty or many)."""
    matched = []
    for name in feature_names():
        selectors = FEATURES[name]["selectors"]
        if select_by_selectors([test_id], selectors):
            matched.append(name)
    return matched


def e2e_by_feature(all_ids):
    """Per-feature test lists. Counts overlap across features; not a partition."""
    rows = []
    for name in feature_names():
        ids = select_features(all_ids, [name])
        rows.append(
            {
                "feature": name,
                "description": FEATURES[name]["description"],
                "count": len(ids),
                "test_ids": ids,
            }
        )
    # Inventory reports curated smoke membership without failing closed the way
    # `select_smoke` does when the suite is incomplete (useful for partial fixtures
    # and for surfacing missing curated IDs in the report).
    known = set(all_ids)
    smoke_ids = [tid for tid in SMOKE_TEST_IDS if tid in known]
    rows.append(
        {
            "feature": "smoke",
            "description": "Curated essential suite (SMOKE_TEST_IDS)",
            "count": len(smoke_ids),
            "test_ids": smoke_ids,
            "missing_curated": [tid for tid in SMOKE_TEST_IDS if tid not in known],
        }
    )
    return rows


def e2e_by_module(all_ids):
    """Unique test counts per E2E module (partition of the suite)."""
    buckets = defaultdict(list)
    for test_id in all_ids:
        buckets[module_of(test_id)].append(test_id)
    return [
        {"module": module, "count": len(ids), "test_ids": list(ids)}
        for module, ids in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    ]


def unmapped_e2e_ids(all_ids):
    """Tests not matched by any FEATURES selector (smoke-only membership ignored)."""
    mapped = set()
    for name in feature_names():
        mapped.update(select_features(all_ids, [name]))
    return [tid for tid in all_ids if tid not in mapped]


def timing_weights(repo: Path):
    """Merged local+baseline timing weights and source metadata."""
    baseline_path = repo / BASELINE_TIMINGS_PATH
    local_path = repo / TIMINGS_PATH
    baseline = load_timings(baseline_path)
    local = load_timings(local_path)
    return {
        "weights": merge_timing_sources(baseline, local),
        "baseline_path": str(BASELINE_TIMINGS_PATH),
        "baseline_keys": len(baseline or {}),
        "local_path": str(TIMINGS_PATH),
        "local_keys": len(local or {}),
        "local_present": local_path.is_file(),
        "default_seconds": DEFAULT_TEST_SECONDS,
    }


def estimated_seconds_for_ids(test_ids, weights) -> float:
    return sum(estimate_seconds(tid, weights) for tid in test_ids)


def slowest_tests(all_ids, weights, limit=15):
    ranked = sorted(
        ((estimate_seconds(tid, weights), tid) for tid in all_ids),
        key=lambda pair: (-pair[0], pair[1]),
    )
    return [{"seconds": seconds, "test_id": tid} for seconds, tid in ranked[: max(0, limit)]]


def slowest_modules(all_ids, weights, limit=15):
    totals = defaultdict(float)
    counts = defaultdict(int)
    for tid in all_ids:
        mod = module_of(tid)
        totals[mod] += estimate_seconds(tid, weights)
        counts[mod] += 1
    ranked = sorted(
        totals.items(),
        key=lambda item: (-item[1], item[0]),
    )[: max(0, limit)]
    return [
        {"module": mod, "seconds": seconds, "count": counts[mod]}
        for mod, seconds in ranked
    ]


def feature_runtime_rows(feature_rows, weights):
    out = []
    for row in feature_rows:
        seconds = estimated_seconds_for_ids(row["test_ids"], weights)
        out.append(
            {
                "feature": row["feature"],
                "count": row["count"],
                "estimated_seconds": seconds,
                "description": row.get("description", ""),
            }
        )
    # Sort by estimated cost descending for the report body.
    out.sort(key=lambda r: (-r["estimated_seconds"], r["feature"]))
    return out


def count_node_selftests(repo: Path):
    """Count Node browser selftest scripts without executing them.

    Runners (`run_*selftest*.js`) are the practical inventory unit: each is a
    Node entrypoint wrapped by a Python frontend unit test. Other `*selftest*.js`
    files are supporting modules listed separately.
    """
    browser = repo / "tests" / "browser"
    runners = sorted(p for p in browser.glob(SELFTEST_RUNNER_GLOB) if p.is_file())
    modules = sorted(
        p
        for p in browser.glob(SELFTEST_MODULE_GLOB)
        if p.is_file() and p not in runners
    )
    return {
        "runners": [str(p.relative_to(repo)).replace("\\", "/") for p in runners],
        "support_modules": [
            str(p.relative_to(repo)).replace("\\", "/") for p in modules
        ],
        "runner_count": len(runners),
        "support_module_count": len(modules),
    }


def _is_failed_test(case) -> bool:
    """True for unittest import/load placeholders (never real cases)."""
    return isinstance(case, _FailedTest) or type(case).__name__ == "_FailedTest"


def collect_test_ids(suite):
    """Walk a suite; return (real_ids, failed_cases).

    `_FailedTest` placeholders and other non-TestCase suite entries are never
    counted as real inventory IDs.
    """
    ids = []
    failed = []

    def walk(node):
        for item in node:
            if isinstance(item, unittest.TestSuite):
                walk(item)
            elif isinstance(item, unittest.TestCase):
                if _is_failed_test(item):
                    failed.append(item)
                else:
                    ids.append(item.id())
            else:  # pragma: no cover - defensive
                failed.append(item)

    walk(suite)
    return ids, failed


def format_discovery_errors(scope: str, *, errors, failed) -> str:
    """Human-readable discovery failure for stderr / DiscoveryError."""
    lines = [
        "%s discovery failed; refusing to report inventory counts "
        "(unittest load/import errors are not real tests)." % scope
    ]
    for err in errors or []:
        text = err if isinstance(err, str) else str(err)
        for part in text.strip().splitlines() or [text]:
            lines.append("  %s" % part)
    for case in failed or []:
        if isinstance(case, unittest.TestCase):
            detail = ""
            exc = getattr(case, "_exception", None)
            if exc is not None:
                detail = ": %s" % exc
            lines.append("  _FailedTest %s%s" % (case.id(), detail))
        else:
            lines.append("  unresolved suite entry: %r" % (case,))
    return "\n".join(lines)


def raise_if_discovery_errors(scope: str, *, errors, failed):
    """Raise DiscoveryError when loader.errors or _FailedTest entries exist."""
    error_list = list(errors or [])
    failed_list = list(failed or [])
    if not error_list and not failed_list:
        return
    failed_ids = []
    for case in failed_list:
        if isinstance(case, unittest.TestCase):
            failed_ids.append(case.id())
        else:
            failed_ids.append(repr(case))
    raise DiscoveryError(
        format_discovery_errors(scope, errors=error_list, failed=failed_list),
        errors=error_list,
        failed_ids=failed_ids,
    )


def discover_e2e_test_ids(modules):
    """Discover E2E test IDs from module names; fail closed on load errors."""
    loader = unittest.TestLoader()
    all_ids = []
    all_failed = []
    for name in modules:
        suite = loader.loadTestsFromName(name)
        ids, failed = collect_test_ids(suite)
        all_ids.extend(ids)
        all_failed.extend(failed)
    raise_if_discovery_errors(
        "E2E",
        errors=loader.errors,
        failed=all_failed,
    )
    return all_ids


def count_python_unit_tests(repo: Path):
    """Discover the default `run_tests.py` unit suite (no Chromium E2E).

    Relies on E2E modules' `load_tests` returning empty when PRKS_E2E != 1, and
    UX tour not matching `test_*.py` discovery under tests/ux_tour the same way
    when gated. Counts are approximate live discovery results.

    Raises DiscoveryError when any module fails to import/load so inventory
    never exits 0 with inflated `_FailedTest` placeholders as "tests".
    """
    previous_e2e = os.environ.get("PRKS_E2E")
    previous_testing = os.environ.get("PRKS_TESTING")
    previous_storage = os.environ.get("PRKS_STORAGE")
    previous_for_processing = os.environ.get("PRKS_FOR_PROCESSING_DIR")
    previous_log_file = os.environ.get("PRKS_LOG_FILE")
    # Ensure E2E modules stay gated out of unit discovery.
    os.environ.pop("PRKS_E2E", None)
    # Match run_tests.apply_isolated_test_env: never trust live storage /
    # processing / log overrides during import-time discovery.
    repo_s = str(repo)
    inserted = False
    if repo_s not in sys.path:
        sys.path.insert(0, repo_s)
        inserted = True
    try:
        with tempfile.TemporaryDirectory(prefix="prks-inventory-unit-") as tmp:
            os.environ["PRKS_TESTING"] = "1"
            os.environ["PRKS_STORAGE"] = tmp
            os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
            os.environ.pop("PRKS_LOG_FILE", None)
            loader = unittest.TestLoader()
            # Match run_tests.py: discover under tests/ without top_level_dir
            # (tests/ is not a package).
            suite = loader.discover(
                start_dir=str(repo / "tests"),
                pattern="test_*.py",
            )
            ids, failed = collect_test_ids(suite)
            raise_if_discovery_errors(
                "Python unit/API",
                errors=loader.errors,
                failed=failed,
            )
            e2e_ids = [tid for tid in ids if tid.startswith("tests.e2e.")]
            ux_ids = [tid for tid in ids if tid.startswith("tests.ux_tour.")]
            unit_ids = [
                tid
                for tid in ids
                if not tid.startswith("tests.e2e.")
                and not tid.startswith("tests.ux_tour.")
            ]
            return {
                "total_discovered": len(ids),
                "unit_api_count": len(unit_ids),
                "e2e_leaked_into_unit": len(e2e_ids),
                "ux_tour_leaked_into_unit": len(ux_ids),
            }
    finally:
        if inserted and repo_s in sys.path:
            try:
                sys.path.remove(repo_s)
            except ValueError:
                pass
        if previous_testing is None:
            os.environ.pop("PRKS_TESTING", None)
        else:
            os.environ["PRKS_TESTING"] = previous_testing
        if previous_storage is None:
            os.environ.pop("PRKS_STORAGE", None)
        else:
            os.environ["PRKS_STORAGE"] = previous_storage
        if previous_for_processing is None:
            os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
        else:
            os.environ["PRKS_FOR_PROCESSING_DIR"] = previous_for_processing
        if previous_log_file is None:
            os.environ.pop("PRKS_LOG_FILE", None)
        else:
            os.environ["PRKS_LOG_FILE"] = previous_log_file
        if previous_e2e is None:
            os.environ.pop("PRKS_E2E", None)
        else:
            os.environ["PRKS_E2E"] = previous_e2e


def build_inventory(
    all_ids,
    *,
    repo: Path,
    include_unit: bool = True,
    include_node: bool = True,
    slowest_limit: int = 15,
):
    """Assemble a structured inventory dict from discovered E2E IDs."""
    timing = timing_weights(repo)
    weights = timing["weights"]
    feature_rows = e2e_by_feature(all_ids)
    module_rows = e2e_by_module(all_ids)
    unmapped = unmapped_e2e_ids(all_ids)
    smoke_row = next(r for r in feature_rows if r["feature"] == "smoke")
    inventory = {
        "coverage_note": COVERAGE_BOUNDARY_NOTE,
        "e2e": {
            "total": len(all_ids),
            "module_count": len(module_rows),
            "feature_group_count": len(feature_names()),
            "smoke_curated_count": len(SMOKE_TEST_IDS),
            "smoke_present_count": smoke_row["count"],
            "smoke_missing_curated": list(smoke_row.get("missing_curated") or []),
            "unmapped_count": len(unmapped),
            "unmapped_ids": unmapped,
            "stress_scripts": list(STRESS_SCRIPTS),
            "by_feature": feature_runtime_rows(feature_rows, weights),
            "by_module": [
                {
                    "module": row["module"],
                    "count": row["count"],
                    "estimated_seconds": estimated_seconds_for_ids(
                        row["test_ids"], weights
                    ),
                }
                for row in module_rows
            ],
            "slowest_tests": slowest_tests(all_ids, weights, limit=slowest_limit),
            "slowest_modules": slowest_modules(all_ids, weights, limit=slowest_limit),
            "timing": {
                "baseline_path": timing["baseline_path"],
                "baseline_keys": timing["baseline_keys"],
                "local_path": timing["local_path"],
                "local_keys": timing["local_keys"],
                "local_present": timing["local_present"],
                "default_seconds": timing["default_seconds"],
                "note": (
                    "Estimates use machine-local exact timings overriding the "
                    "committed baseline prefix weights; unknown tests use "
                    "DEFAULT_TEST_SECONDS. These are LPT scheduling weights, "
                    "not a measured wall-clock full-gate claim."
                ),
            },
            "estimated_suite_seconds_serial": estimated_seconds_for_ids(
                all_ids, weights
            ),
        },
    }
    if include_node:
        inventory["node_selftests"] = count_node_selftests(repo)
    if include_unit:
        inventory["python_unit"] = count_python_unit_tests(repo)
    return inventory


def format_inventory_text(inventory) -> str:
    """Human-readable inventory report."""
    lines = []
    lines.append("PRKS test inventory (on-demand discovery; not a coverage gate)")
    lines.append("")
    lines.append(inventory["coverage_note"])
    lines.append("")

    e2e = inventory["e2e"]
    lines.append("== E2E (Chromium / tests/e2e) ==")
    lines.append("Total tests: %d" % e2e["total"])
    lines.append(
        "Modules: %d | Feature groups: %d | Smoke curated: %d (present: %d) | Unmapped to any feature: %d"
        % (
            e2e["module_count"],
            e2e["feature_group_count"],
            e2e["smoke_curated_count"],
            e2e["smoke_present_count"],
            e2e["unmapped_count"],
        )
    )
    if e2e.get("smoke_missing_curated"):
        lines.append(
            "WARNING: curated smoke IDs missing from discovery: %s"
            % ", ".join(e2e["smoke_missing_curated"])
        )
    timing = e2e["timing"]
    lines.append(
        "Timing sources: baseline=%s (%d keys), local=%s (%s, %d keys); default=%.1fs"
        % (
            timing["baseline_path"],
            timing["baseline_keys"],
            timing["local_path"],
            "present" if timing["local_present"] else "absent",
            timing["local_keys"],
            timing["default_seconds"],
        )
    )
    lines.append(timing["note"])
    lines.append(
        "Approx. serial suite estimate (sum of per-test weights): %.0fs"
        % e2e["estimated_suite_seconds_serial"]
    )
    if e2e["stress_scripts"]:
        lines.append(
            "Stress/opt-in (not in full gate): %s" % ", ".join(e2e["stress_scripts"])
        )
    lines.append("")
    lines.append(
        "By feature (overlapping — counts are NOT additive; use module table for a partition):"
    )
    lines.append("  %-22s %6s %10s  %s" % ("feature", "tests", "est.", "description"))
    for row in e2e["by_feature"]:
        lines.append(
            "  %-22s %6d %8.0fs  %s"
            % (
                row["feature"],
                row["count"],
                row["estimated_seconds"],
                row["description"],
            )
        )
    lines.append("")
    lines.append("By module (partition of the suite):")
    lines.append("  %-42s %6s %10s" % ("module", "tests", "est."))
    for row in e2e["by_module"]:
        lines.append(
            "  %-42s %6d %8.0fs"
            % (row["module"], row["count"], row["estimated_seconds"])
        )
    if e2e["unmapped_ids"]:
        lines.append("")
        lines.append("Unmapped E2E test IDs:")
        for tid in e2e["unmapped_ids"]:
            lines.append("  %s" % tid)

    lines.append("")
    lines.append("Slowest tests (by merged timing weight):")
    for row in e2e["slowest_tests"]:
        lines.append("  %7.2fs  %s" % (row["seconds"], row["test_id"]))

    lines.append("")
    lines.append("Slowest modules (sum of per-test weights):")
    for row in e2e["slowest_modules"]:
        lines.append(
            "  %7.0fs  %s (%d tests)"
            % (row["seconds"], row["module"], row["count"])
        )

    if "python_unit" in inventory:
        unit = inventory["python_unit"]
        lines.append("")
        lines.append("== Python unit / API (run_tests.py default discovery) ==")
        lines.append(
            "Collected tests: %d (unit/API; E2E gated out when PRKS_E2E unset)"
            % unit["unit_api_count"]
        )
        if unit["e2e_leaked_into_unit"] or unit["ux_tour_leaked_into_unit"]:
            lines.append(
                "  note: discovery also saw e2e=%d ux_tour=%d (unexpected leak)"
                % (unit["e2e_leaked_into_unit"], unit["ux_tour_leaked_into_unit"])
            )

    if "node_selftests" in inventory:
        node = inventory["node_selftests"]
        lines.append("")
        lines.append("== Node browser selftests ==")
        lines.append(
            "Runner scripts (tests/browser/%s): %d"
            % (SELFTEST_RUNNER_GLOB, node["runner_count"])
        )
        if node["support_module_count"]:
            lines.append(
                "Support/selftest modules (non-runner): %d"
                % node["support_module_count"]
            )
        lines.append(
            "Assertion counts require executing each runner; inventory lists scripts only."
        )

    lines.append("")
    lines.append(
        "Reminder: a high E2E count is not itself a defect; redundant Chromium coverage "
        "of invariants already owned below the browser is."
    )
    return "\n".join(lines)


def format_inventory_json(inventory) -> str:
    """JSON form without embedding full per-feature test_id lists (already dropped)."""
    return json.dumps(inventory, indent=2, sort_keys=True) + "\n"
