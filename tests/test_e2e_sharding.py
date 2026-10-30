"""Scheduling/aggregation coverage for the parallel E2E runner. No Chromium.

The pure logic lives in `tests.e2e.sharding`, which imports nothing from
Playwright, so it can be exercised here. The parent/worker protocol is covered
by handing `tests/e2e/run.py` deliberately passing, failing and crashing cases
from `tests.e2e.runner_selfcheck_cases` -- real worker processes, no browser.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.e2e.sharding import (
    AGENT_MEMORY_PER_JOB_BYTES,
    BASELINE_TIMINGS_PATH,
    DEFAULT_TEST_SECONDS,
    MAX_JOBS,
    agent_default_jobs,
    aggregate_external_shard_results,
    aggregate_timing_baseline,
    assess_measurement_coverage,
    aggregate_worker_results,
    assign_shards,
    combine_measurement_timings,
    detect_cgroup_cpu_count,
    detect_cgroup_memory_limit_bytes,
    estimate_seconds,
    format_slowest,
    is_baseline_prefix_key,
    load_timings,
    merge_timing_sources,
    merge_timings,
    module_of,
    parse_jobs,
    parse_shard,
    partition_external_shards,
    run_exit_code,
    save_timings,
    select_external_shard,
    shard_estimates,
    slowest_report_timings,
    worker_port_range,
)

_CASES = "tests.e2e.runner_selfcheck_cases"


def _ids(module, cls, *names):
    return ["%s.%s.%s" % (module, cls, name) for name in names]


@contextlib.contextmanager
def _import_runner():
    """Import tests.e2e.run without leaking its PRKS_E2E=1 into the unit suite.

    Importing the runner flips PRKS_E2E on, which is what makes the E2E modules
    collectable. Leaving that set would let a later discovery pass pick up real
    Chromium scenarios from inside the unit run.
    """
    previous = os.environ.get("PRKS_E2E")
    # Set it here rather than leaning on the import's side effect: the module is
    # cached after the first use, so a later `import` would not re-arm it.
    os.environ["PRKS_E2E"] = "1"
    try:
        from tests.e2e import run as runner

        yield runner
    finally:
        if previous is None:
            os.environ.pop("PRKS_E2E", None)
        else:
            os.environ["PRKS_E2E"] = previous


class ShardAssignmentTests(unittest.TestCase):
    def test_every_test_is_assigned_exactly_once(self):
        ids = ["tests.e2e.test_app.C.test_%02d" % i for i in range(37)]
        for jobs in (1, 2, 3, 4, 8):
            buckets = assign_shards(ids, jobs)
            self.assertEqual(len(buckets), jobs)
            flat = [t for bucket in buckets for t in bucket]
            self.assertEqual(len(flat), len(ids), jobs)
            self.assertEqual(len(set(flat)), len(ids), jobs)
            self.assertEqual(sorted(flat), sorted(ids), jobs)

    def test_more_workers_than_tests_leaves_empty_shards(self):
        ids = _ids("tests.e2e.test_app", "C", "test_a", "test_b")
        buckets = assign_shards(ids, 5)
        self.assertEqual(len(buckets), 5)
        self.assertEqual(sorted(t for b in buckets for t in b), sorted(ids))
        self.assertEqual(sum(1 for b in buckets if b), 2)

    def test_empty_discovery_still_returns_one_bucket_per_worker(self):
        self.assertEqual(assign_shards([], 3), [[], [], []])

    def test_zero_workers_rejected(self):
        with self.assertRaises(ValueError):
            assign_shards(["a.B.c"], 0)

    def test_balancing_uses_duration_not_test_count(self):
        ids = _ids("tests.e2e.test_app", "C", "slow", "a", "b", "c", "d", "e")
        timings = {
            "tests.e2e.test_app.C.slow": 60.0,
            "tests.e2e.test_app.C.a": 10.0,
            "tests.e2e.test_app.C.b": 10.0,
            "tests.e2e.test_app.C.c": 10.0,
            "tests.e2e.test_app.C.d": 10.0,
            "tests.e2e.test_app.C.e": 10.0,
        }
        buckets = assign_shards(ids, 2, timings)
        estimates = shard_estimates(buckets, timings)
        # Count-balanced would be 3/3; duration-balanced puts the 60s test alone.
        self.assertEqual(sorted(len(b) for b in buckets), [1, 5])
        self.assertEqual(sorted(estimates), [50.0, 60.0])

    def test_balancing_is_deterministic(self):
        ids = ["tests.e2e.test_offline.C.test_%02d" % i for i in range(50)]
        timings = {t: (i % 7) + 1.0 for i, t in enumerate(ids)}
        first = assign_shards(ids, 4, timings)
        second = assign_shards(list(reversed(ids)), 4, timings)
        self.assertEqual(first, second)

    def test_module_affinity_pilot_preserves_all_ids_and_determinism(self):
        ids = []
        for module in ("tests.e2e.alpha", "tests.e2e.bravo", "tests.e2e.charlie"):
            for klass in ("One", "Two"):
                ids.extend(_ids(module, klass, *("test_%02d" % i for i in range(9))))
        timings = {t: 1.0 for t in ids}
        for count in (1, 2, 3, 6, 8):
            pilot = assign_shards(ids, count, timings, module_affinity=True)
            self.assertEqual(
                pilot,
                assign_shards(list(reversed(ids)), count, timings, module_affinity=True),
            )
            self.assertEqual(sorted(t for bucket in pilot for t in bucket), sorted(ids))
            self.assertEqual(
                pilot,
                partition_external_shards(ids, count, timings, module_affinity=True),
            )
            for index in range(count):
                self.assertEqual(
                    pilot[index],
                    select_external_shard(
                        ids, index, count, timings, module_affinity=True,
                    ),
                )
            # Explicitly selecting the normal policy never changes its layout.
            self.assertEqual(
                assign_shards(ids, count, timings),
                assign_shards(ids, count, timings, module_affinity=False),
            )

    def test_module_affinity_prefers_reuse_without_forcing_whole_modules(self):
        ids = []
        for module in ("tests.e2e.alpha", "tests.e2e.bravo", "tests.e2e.charlie"):
            ids.extend(_ids(module, "Case", "test_0", "test_1", "test_2"))
        # Representative unequal weights: a small preference avoids a duplicate
        # module fixture without growing the critical-path test estimate.
        timings = dict(zip(ids, (4, 2, 2, 1, 5, 3, 2, 3, 4)))
        baseline = assign_shards(ids, 2, timings)
        pilot = assign_shards(ids, 2, timings, module_affinity=True)

        def module_pairs(buckets):
            return sum(len({module_of(t) for t in bucket}) for bucket in buckets)
        self.assertLess(module_pairs(pilot), module_pairs(baseline))
        self.assertEqual(
            max(shard_estimates(pilot, timings)),
            max(shard_estimates(baseline, timings)),
        )
        self.assertEqual(sorted(t for b in pilot for t in b), sorted(ids))

        # A very heavy test may still be assigned separately; this is NOT
        # whole-module packing, which regressed the previous benchmark.
        heavy = _ids("tests.e2e.slow", "Case", "test_heavy", "test_small_a", "test_small_b")
        weights = {heavy[0]: 200.0, heavy[1]: 1.0, heavy[2]: 1.0}
        result = assign_shards(heavy, 2, weights, module_affinity=True)
        self.assertEqual(sorted(t for b in result for t in b), sorted(heavy))
        self.assertEqual(len([b for b in result if b]), 2)

    def test_missing_timing_entries_fall_back_to_the_default_estimate(self):
        ids = _ids("tests.e2e.test_app", "C", "known", "unknown")
        timings = {"tests.e2e.test_app.C.known": 12.0}
        self.assertEqual(estimate_seconds("tests.e2e.test_app.C.known", timings), 12.0)
        self.assertEqual(
            estimate_seconds("tests.e2e.test_app.C.unknown", timings),
            DEFAULT_TEST_SECONDS,
        )
        buckets = assign_shards(ids, 2, timings)
        self.assertEqual(sorted(t for b in buckets for t in b), sorted(ids))

    def test_corrupt_timing_values_are_ignored_not_trusted(self):
        for bad in (None, "12", True, -5, 0, float("nan"), float("inf")):
            self.assertEqual(
                estimate_seconds("a.B.c", {"a.B.c": bad}), DEFAULT_TEST_SECONDS, bad
            )

    def test_committed_baseline_prefix_weights_unknown_exact_ids(self):
        timings = {
            "tests.e2e.test_offline.*": 8.0,
            "tests.e2e.test_offline.Heavy*": 12.0,
        }
        self.assertEqual(
            estimate_seconds("tests.e2e.test_offline.HeavyCase.test_x", timings),
            12.0,
        )
        self.assertEqual(
            estimate_seconds("tests.e2e.test_offline.OtherCase.test_x", timings),
            8.0,
        )

    def test_local_exact_timing_overrides_baseline_prefix(self):
        baseline = {"tests.e2e.test_offline.*": 8.0}
        local = {"tests.e2e.test_offline.Case.test_x": 2.5}
        merged = merge_timing_sources(baseline, local)
        self.assertEqual(
            estimate_seconds("tests.e2e.test_offline.Case.test_x", merged),
            2.5,
        )

    def test_merge_timing_sources_keeps_unrelated_baseline_prefixes(self):
        baseline = {
            "tests.e2e.test_offline.*": 8.0,
            "tests.e2e.test_app.*": 4.0,
        }
        local = {"tests.e2e.test_offline.Case.test_x": 2.5}
        merged = merge_timing_sources(baseline, local)
        self.assertEqual(estimate_seconds("tests.e2e.test_app.Other.test_y", merged), 4.0)
        self.assertEqual(
            estimate_seconds("tests.e2e.test_offline.Case.test_x", merged), 2.5
        )

    def test_slowest_report_excludes_baseline_even_when_merged_for_scheduling(self):
        baseline = {"tests.e2e.test_offline.*": 8.0}
        local = {"tests.e2e.test_offline.Case.test_old": 3.0}
        observed = {"tests.e2e.test_offline.Case.test_new": 11.0}
        scheduling = merge_timing_sources(baseline, {**local, **observed})
        self.assertIn("tests.e2e.test_offline.*", scheduling)
        report = slowest_report_timings(local, observed, targeted=False)
        self.assertEqual(report, observed)
        lines = format_slowest(report)
        self.assertTrue(any("test_new" in line for line in lines))
        self.assertFalse(any("test_offline.*" in line for line in lines))

    def test_slowest_report_targeted_keeps_prior_local_exact_ids(self):
        local = {"tests.e2e.test_offline.Case.test_old": 3.0}
        observed = {"tests.e2e.test_offline.Case.test_new": 5.0}
        report = slowest_report_timings(local, observed, targeted=True)
        self.assertEqual(
            report,
            {
                "tests.e2e.test_offline.Case.test_old": 3.0,
                "tests.e2e.test_offline.Case.test_new": 5.0,
            },
        )

    def test_format_slowest_strips_baseline_prefix_keys(self):
        lines = format_slowest(
            {
                "tests.e2e.test_offline.*": 99.0,
                "tests.e2e.test_offline.Case.test_x": 4.0,
            },
            limit=5,
        )
        self.assertEqual(lines, ["   4.00s  tests.e2e.test_offline.Case.test_x"])

    def test_is_baseline_prefix_key(self):
        self.assertTrue(is_baseline_prefix_key("tests.e2e.test_app.*"))
        self.assertTrue(is_baseline_prefix_key("tests.e2e.test_app.Heavy.*"))
        self.assertFalse(is_baseline_prefix_key("tests.e2e.test_app.Heavy.test_x"))
        self.assertFalse(is_baseline_prefix_key("*"))
        self.assertFalse(is_baseline_prefix_key(None))

    def test_combine_measurement_timings_medians_and_drops_prefixes(self):
        a = {
            "tests.e2e.test_app.C.test_a": 2.0,
            "tests.e2e.test_app.C.test_b": 4.0,
            "tests.e2e.test_app.*": 50.0,
        }
        b = {
            "tests.e2e.test_app.C.test_a": 6.0,
            "tests.e2e.test_app.C.test_b": 8.0,
        }
        combined = combine_measurement_timings(a, b)
        self.assertEqual(combined["tests.e2e.test_app.C.test_a"], 4.0)
        self.assertEqual(combined["tests.e2e.test_app.C.test_b"], 6.0)
        self.assertNotIn("tests.e2e.test_app.*", combined)

    def test_aggregate_timing_baseline_emits_module_and_outlier_class_prefixes(self):
        exact = {}
        for i in range(6):
            exact["tests.e2e.test_app.Light.test_%d" % i] = 2.0
        for i in range(3):
            exact["tests.e2e.test_app.Heavy.test_%d" % i] = 10.0
        # Prefix noise / exact-id pollution must not leak into the committed file.
        exact["tests.e2e.test_app.*"] = 99.0
        baseline = aggregate_timing_baseline(exact, class_outlier_ratio=1.5, min_class_samples=3)
        self.assertEqual(set(baseline), {
            "tests.e2e.test_app.*",
            "tests.e2e.test_app.Heavy.*",
        })
        self.assertTrue(all(is_baseline_prefix_key(k) for k in baseline))
        self.assertNotIn("tests.e2e.test_app.Light.test_0", baseline)
        # Module median of six 2s + three 10s = 2.0; Heavy class median 10 → outlier.
        self.assertEqual(baseline["tests.e2e.test_app.*"], 2.0)
        self.assertEqual(baseline["tests.e2e.test_app.Heavy.*"], 10.0)

    def test_aggregate_timing_baseline_skips_non_outlier_classes(self):
        exact = {
            "tests.e2e.m.A.test_1": 4.0,
            "tests.e2e.m.A.test_2": 4.0,
            "tests.e2e.m.A.test_3": 4.0,
            "tests.e2e.m.B.test_1": 5.0,
            "tests.e2e.m.B.test_2": 5.0,
            "tests.e2e.m.B.test_3": 5.0,
        }
        baseline = aggregate_timing_baseline(exact, class_outlier_ratio=1.5, min_class_samples=3)
        self.assertEqual(set(baseline), {"tests.e2e.m.*"})

    def test_aggregate_timing_baseline_rejects_non_finite_class_outlier_ratio(self):
        exact = {
            "tests.e2e.m.A.test_1": 2.0,
            "tests.e2e.m.A.test_2": 2.0,
            "tests.e2e.m.A.test_3": 2.0,
            "tests.e2e.m.B.test_1": 10.0,
            "tests.e2e.m.B.test_2": 10.0,
            "tests.e2e.m.B.test_3": 10.0,
        }
        for bad in (float("nan"), float("inf"), float("-inf"), 0.5, "nan"):
            with self.assertRaises(ValueError):
                aggregate_timing_baseline(
                    exact, class_outlier_ratio=bad, min_class_samples=3
                )

    def test_aggregate_timing_baseline_rejects_bad_options_before_empty_input(self):
        # Invalid options must fail even when no exact IDs remain to group.
        with self.assertRaises(ValueError):
            aggregate_timing_baseline({}, class_outlier_ratio=float("nan"))
        with self.assertRaises(ValueError):
            aggregate_timing_baseline(
                {"mod.test": 1.0},  # two-component id → no module groups
                class_outlier_ratio=float("inf"),
            )
        with self.assertRaises(ValueError):
            aggregate_timing_baseline({}, min_class_samples=0)
        with self.assertRaises(ValueError):
            aggregate_timing_baseline({}, min_class_samples=-1)

    def test_assess_measurement_coverage_reports_missing_modules_and_ids(self):
        exact = {"tests.e2e.a.C.test_x": 1.0}
        discovered = [
            "tests.e2e.a.C.test_x",
            "tests.e2e.a.C.test_y",
            "tests.e2e.b.C.test_z",
        ]
        missing_modules, missing_ids = assess_measurement_coverage(exact, discovered)
        self.assertEqual(missing_modules, ["tests.e2e.b"])
        self.assertEqual(
            missing_ids,
            ["tests.e2e.a.C.test_y", "tests.e2e.b.C.test_z"],
        )
        self.assertEqual(assess_measurement_coverage(exact, ["tests.e2e.a.C.test_x"]), ([], []))

    def test_aggregated_baseline_still_overridden_by_local_exact(self):
        exact = {
            "tests.e2e.test_offline.Case.test_%d" % i: 8.0 for i in range(4)
        }
        baseline = aggregate_timing_baseline(exact, min_class_samples=3)
        local = {"tests.e2e.test_offline.Case.test_0": 1.25}
        merged = merge_timing_sources(baseline, local)
        self.assertEqual(
            estimate_seconds("tests.e2e.test_offline.Case.test_0", merged), 1.25
        )
        self.assertEqual(
            estimate_seconds("tests.e2e.test_offline.Case.test_1", merged), 8.0
        )

    def test_committed_baseline_includes_calibrated_exact_ids_and_prefix_fallback(self):
        repo = Path(_PROJECT_DIR)
        baseline = load_timings(repo / BASELINE_TIMINGS_PATH)
        self.assertTrue(baseline, "committed timing-baseline.json should not be empty")
        prefix = [k for k in baseline if is_baseline_prefix_key(k)]
        exact = [k for k in baseline if not is_baseline_prefix_key(k)]
        self.assertTrue(prefix)
        self.assertGreaterEqual(len(exact), 800)
        for key in exact:
            self.assertTrue(key.startswith("tests.e2e."), key)
            self.assertGreaterEqual(len(key.split(".")), 5, key)
        sample = exact[0]
        self.assertEqual(
            estimate_seconds(sample, merge_timing_sources(baseline, {sample: 1.234})),
            1.234,
        )
        self.assertEqual(
            estimate_seconds("tests.e2e.test_app.FutureClass.test_new", baseline),
            baseline["tests.e2e.test_app.*"],
        )

    def test_shards_keep_each_module_contiguous(self):
        # These modules launch Chromium in setUpModule; unittest re-runs a module
        # fixture whenever the module changes, so interleaving would relaunch it.
        ids = []
        for module in ("tests.e2e.test_app", "tests.e2e.test_offline", "tests.e2e.test_playlists_offline"):
            ids.extend("%s.C.test_%02d" % (module, i) for i in range(9))
        for bucket in assign_shards(ids, 3):
            seen = []
            for test_id in bucket:
                name = module_of(test_id)
                if not seen or seen[-1] != name:
                    seen.append(name)
            self.assertEqual(len(seen), len(set(seen)), bucket)

    def test_module_of_handles_short_names(self):
        self.assertEqual(module_of("tests.e2e.test_app.Klass.test_x"), "tests.e2e.test_app")
        self.assertEqual(module_of("mod.Klass.test_x"), "mod")
        self.assertEqual(module_of("weird"), "weird")


class ExternalShardPartitionerTests(unittest.TestCase):
    """External --shard INDEX/TOTAL uses the same LPT buckets as --jobs TOTAL."""

    def test_parse_shard_accepts_1_based_index(self):
        self.assertEqual(parse_shard("1/4"), (0, 4))
        self.assertEqual(parse_shard("4/4"), (3, 4))
        self.assertIsNone(parse_shard(None))
        self.assertIsNone(parse_shard(""))
        self.assertEqual(parse_shard(None, "2/3"), (1, 3))
        self.assertEqual(parse_shard("1/4", "9/9"), (0, 4))  # CLI wins

    def test_parse_shard_rejects_invalid_specs(self):
        for bad in ("0/4", "5/4", "1", "1/0", "a/b", "1/2/3", "1/-2", "-1/4"):
            with self.assertRaises(ValueError):
                parse_shard(bad)
        with self.assertRaises(ValueError):
            parse_shard("1/%d" % (MAX_JOBS + 1))

    def test_external_shards_cover_every_test_exactly_once(self):
        ids = ["tests.e2e.test_app.C.test_%02d" % i for i in range(37)]
        timings = {t: (i % 5) + 1.5 for i, t in enumerate(ids)}
        for total in (1, 2, 3, 4, 8):
            buckets = partition_external_shards(ids, total, timings)
            self.assertEqual(len(buckets), total)
            flat = [t for bucket in buckets for t in bucket]
            self.assertEqual(len(flat), len(ids), total)
            self.assertEqual(len(set(flat)), len(ids), total)
            self.assertEqual(sorted(flat), sorted(ids), total)
            for index in range(total):
                self.assertEqual(
                    select_external_shard(ids, index, total, timings),
                    buckets[index],
                )

    def test_external_partition_matches_assign_shards(self):
        ids = _ids("tests.e2e.test_offline", "C", "a", "b", "c", "d", "e", "f")
        timings = {
            "tests.e2e.test_offline.C.a": 40.0,
            "tests.e2e.test_offline.C.b": 10.0,
            "tests.e2e.test_offline.C.c": 10.0,
            "tests.e2e.test_offline.C.d": 10.0,
            "tests.e2e.test_offline.C.e": 10.0,
            "tests.e2e.test_offline.C.f": 10.0,
        }
        self.assertEqual(
            partition_external_shards(ids, 2, timings),
            assign_shards(ids, 2, timings),
        )

    def test_external_partition_is_deterministic_for_equal_costs(self):
        ids = ["tests.e2e.test_app.C.test_%02d" % i for i in range(20)]
        first = partition_external_shards(ids, 4)
        second = partition_external_shards(list(reversed(ids)), 4)
        self.assertEqual(first, second)

    def test_select_external_shard_rejects_out_of_range_index(self):
        with self.assertRaises(ValueError):
            select_external_shard(["a.B.c"], -1, 2)
        with self.assertRaises(ValueError):
            select_external_shard(["a.B.c"], 2, 2)


class ExternalShardAggregationTests(unittest.TestCase):
    def test_all_shards_pass(self):
        ok, totals, problems, failed = aggregate_external_shard_results(
            [
                {"shard": "1/4", "reported": True, "returncode": 0},
                {"shard": "2/4", "reported": True, "returncode": 0},
            ]
        )
        self.assertTrue(ok)
        self.assertEqual(problems, [])
        self.assertEqual(failed, [])
        self.assertEqual(totals["shards"], 2)
        self.assertEqual(totals["reported"], 2)

    def test_failed_shard_surfaces_clearly(self):
        ok, totals, problems, failed = aggregate_external_shard_results(
            [
                {"shard": "1/4", "reported": True, "returncode": 0},
                {
                    "shard": "3/4",
                    "reported": True,
                    "returncode": 1,
                    "failures": 2,
                    "failed_ids": ["tests.e2e.test_app.C.test_x"],
                },
            ]
        )
        self.assertFalse(ok)
        self.assertEqual(totals["failures"], 2)
        self.assertEqual(failed, ["tests.e2e.test_app.C.test_x"])
        self.assertTrue(any("shard 3/4" in p for p in problems))

    def test_missing_shard_report_is_never_a_pass(self):
        ok, _, problems, _failed = aggregate_external_shard_results(
            [
                {"shard": "1/4", "reported": True, "returncode": 0},
                {"shard": "2/4", "reported": False, "returncode": -9},
            ]
        )
        self.assertFalse(ok)
        self.assertIn("no result", problems[0])


class AgentJobCountTests(unittest.TestCase):
    def test_agent_defaults_are_capped_at_two(self):
        # Pass an explicit large memory ceiling so this assertion models
        # "CPU-rich, memory-unlimited" rather than auto-detecting the host
        # cgroup (which can force serial on a ~4 GiB cloud agent).
        ample = 16 * AGENT_MEMORY_PER_JOB_BYTES
        self.assertEqual(agent_default_jobs(cpu_count=32, memory_limit_bytes=ample), 2)
        self.assertEqual(agent_default_jobs(cpu_count=1, memory_limit_bytes=ample), 1)

    def test_agent_memory_limit_can_force_serial(self):
        self.assertEqual(
            agent_default_jobs(
                cpu_count=8,
                memory_limit_bytes=AGENT_MEMORY_PER_JOB_BYTES + 512 * 1024 * 1024,
            ),
            1,
        )
        self.assertEqual(
            agent_default_jobs(
                cpu_count=8,
                memory_limit_bytes=2 * AGENT_MEMORY_PER_JOB_BYTES + 512 * 1024 * 1024,
            ),
            2,
        )


class NestedCgroupLimitTests(unittest.TestCase):
    def test_memory_uses_tightest_finite_ancestor(self):
        import tests.e2e.sharding as sharding

        leaf = Path("/sys/fs/cgroup/pod/agent/workload")
        mid = Path("/sys/fs/cgroup/pod/agent")
        root = Path("/sys/fs/cgroup")
        values = {
            leaf / "memory.max": "max",
            mid / "memory.max": str(4 * 1024 * 1024 * 1024),
            root / "memory.max": "max",
        }

        def fake_dirs():
            yield leaf
            yield mid
            yield root

        def fake_read(paths):
            for path in paths:
                if path in values:
                    return values[path]
            return None

        with mock.patch.object(sharding, "_cgroup_v2_self_dirs", fake_dirs):
            with mock.patch.object(sharding, "_read_first", fake_read):
                self.assertEqual(
                    detect_cgroup_memory_limit_bytes(),
                    4 * 1024 * 1024 * 1024,
                )

    def test_cpu_uses_tightest_finite_ancestor(self):
        import tests.e2e.sharding as sharding

        leaf = Path("/sys/fs/cgroup/pod/agent/workload")
        mid = Path("/sys/fs/cgroup/pod/agent")
        root = Path("/sys/fs/cgroup")
        values = {
            leaf / "cpu.max": "max 100000",
            mid / "cpu.max": "100000 100000",
            root / "cpu.max": "max 100000",
        }

        def fake_dirs():
            yield leaf
            yield mid
            yield root

        def fake_read(paths):
            for path in paths:
                if path in values:
                    return values[path]
            return None

        with mock.patch.object(sharding, "_cgroup_v2_self_dirs", fake_dirs):
            with mock.patch.object(sharding, "_read_first", fake_read):
                with mock.patch.object(
                    sharding, "_available_cpu_count", return_value=8
                ):
                    self.assertEqual(detect_cgroup_cpu_count(), 1)

    def test_v1_memory_uses_tightest_nested_controller_path(self):
        import tests.e2e.sharding as sharding

        leaf = Path("/sys/fs/cgroup/memory/docker/job")
        mid = Path("/sys/fs/cgroup/memory/docker")
        root = Path("/sys/fs/cgroup/memory")
        values = {
            leaf / "memory.limit_in_bytes": str(4 * 1024 * 1024 * 1024),
            mid / "memory.limit_in_bytes": str(1 << 63),  # v1 unlimited sentinel
            root / "memory.limit_in_bytes": str(1 << 63),
        }

        def fake_v2():
            return iter(())

        def fake_v1(controller, *mount_names):
            self.assertEqual(controller, "memory")
            yield leaf
            yield mid
            yield root

        def fake_read(paths):
            for path in paths:
                if path in values:
                    return values[path]
            return None

        with mock.patch.object(sharding, "_cgroup_v2_self_dirs", fake_v2):
            with mock.patch.object(sharding, "_cgroup_v1_self_dirs", fake_v1):
                with mock.patch.object(sharding, "_read_first", fake_read):
                    self.assertEqual(
                        detect_cgroup_memory_limit_bytes(),
                        4 * 1024 * 1024 * 1024,
                    )

    def test_v1_cpu_uses_tightest_nested_controller_path(self):
        import tests.e2e.sharding as sharding

        leaf = Path("/sys/fs/cgroup/cpu/docker/job")
        mid = Path("/sys/fs/cgroup/cpu/docker")
        root = Path("/sys/fs/cgroup/cpu")
        values = {
            leaf / "cpu.cfs_quota_us": "100000",
            leaf / "cpu.cfs_period_us": "100000",
            mid / "cpu.cfs_quota_us": "-1",
            mid / "cpu.cfs_period_us": "100000",
            root / "cpu.cfs_quota_us": "-1",
            root / "cpu.cfs_period_us": "100000",
        }

        def fake_v2():
            return iter(())

        def fake_v1(controller, *mount_names):
            self.assertEqual(controller, "cpu")
            yield leaf
            yield mid
            yield root

        def fake_read(paths):
            for path in paths:
                if path in values:
                    return values[path]
            return None

        with mock.patch.object(sharding, "_cgroup_v2_self_dirs", fake_v2):
            with mock.patch.object(sharding, "_cgroup_v1_self_dirs", fake_v1):
                with mock.patch.object(sharding, "_read_first", fake_read):
                    with mock.patch.object(
                        sharding, "_available_cpu_count", return_value=8
                    ):
                        self.assertEqual(detect_cgroup_cpu_count(), 1)

    def test_affinity_caps_when_quota_is_unlimited(self):
        import tests.e2e.sharding as sharding

        def fake_v2():
            return iter(())

        def fake_v1(controller, *mount_names):
            return iter(())

        with mock.patch.object(sharding, "_cgroup_v2_self_dirs", fake_v2):
            with mock.patch.object(sharding, "_cgroup_v1_self_dirs", fake_v1):
                with mock.patch.object(sharding, "_read_first", return_value=None):
                    with mock.patch.object(
                        sharding, "_available_cpu_count", return_value=1
                    ):
                        self.assertEqual(detect_cgroup_cpu_count(), 1)
                        self.assertEqual(agent_default_jobs(), 1)

    def test_available_cpu_count_prefers_sched_affinity(self):
        import tests.e2e.sharding as sharding

        with mock.patch.object(
            sharding.os, "sched_getaffinity", return_value={0}, create=True
        ):
            with mock.patch.object(sharding.os, "cpu_count", return_value=32):
                self.assertEqual(sharding._available_cpu_count(), 1)


class JobCountTests(unittest.TestCase):
    def test_cli_wins_over_env_and_env_wins_over_default(self):
        self.assertEqual(parse_jobs("3", "4", default=1), 3)
        self.assertEqual(parse_jobs(None, "4", default=1), 4)
        self.assertEqual(parse_jobs(None, None, default=1), 1)
        self.assertEqual(parse_jobs(None, "", default=1), 1)

    def test_bad_worker_counts_rejected(self):
        for bad in ("0", "-2", "banana", "2.5", str(MAX_JOBS + 1)):
            with self.assertRaises(ValueError, msg=bad):
                parse_jobs(bad, None)


class TimingHistoryTests(unittest.TestCase):
    def test_absent_corrupt_and_foreign_files_are_harmless(self):
        with tempfile.TemporaryDirectory(prefix="prks-timings-") as raw:
            root = Path(raw)
            self.assertEqual(load_timings(root / "missing.json"), {})
            (root / "corrupt.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(load_timings(root / "corrupt.json"), {})
            (root / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
            self.assertEqual(load_timings(root / "list.json"), {})
            (root / "mixed.json").write_text(
                json.dumps({"a.B.c": 1.5, "a.B.d": "slow", "a.B.e": -1, "a.B.f": None}),
                encoding="utf-8",
            )
            self.assertEqual(load_timings(root / "mixed.json"), {"a.B.c": 1.5})

    def test_history_round_trips_and_replaces_atomically(self):
        with tempfile.TemporaryDirectory(prefix="prks-timings-") as raw:
            path = Path(raw) / "nested" / "e2e-timings.json"
            self.assertTrue(save_timings(path, {"a.B.c": 2.5}))
            self.assertEqual(load_timings(path), {"a.B.c": 2.5})
            self.assertTrue(save_timings(path, {"a.B.c": 3.5}))
            self.assertEqual(load_timings(path), {"a.B.c": 3.5})

    def test_merge_updates_known_tests_and_drops_renamed_ones(self):
        previous = {"a.B.old": 9.0, "a.B.keep": 1.0}
        merged = merge_timings(
            previous, {"a.B.keep": 2.0, "a.B.new": 4.0}, ["a.B.keep", "a.B.new"]
        )
        self.assertEqual(merged, {"a.B.keep": 2.0, "a.B.new": 4.0})

    def test_merge_without_known_ids_preserves_untouched_history(self):
        merged = merge_timings({"a.B.old": 9.0}, {"a.B.new": 1.0}, None)
        self.assertEqual(merged, {"a.B.new": 1.0, "a.B.old": 9.0})

    def test_merge_rejects_nonsense_measurements(self):
        merged = merge_timings({}, {"a.B.c": "slow", "a.B.d": 0, "a.B.e": 1.2345}, None)
        self.assertEqual(merged, {"a.B.e": 1.234})

    def test_slowest_report_is_ordered_and_capped(self):
        lines = format_slowest({"a.B.x": 1.0, "a.B.y": 12.5, "a.B.z": 4.0}, limit=2)
        self.assertEqual(len(lines), 2)
        self.assertIn("a.B.y", lines[0])
        self.assertIn("12.50s", lines[0])
        self.assertIn("a.B.z", lines[1])
        self.assertEqual(format_slowest({}, limit=5), [])


class PortWindowTests(unittest.TestCase):
    def test_worker_windows_are_disjoint(self):
        windows = [worker_port_range(i, 4) for i in range(4)]
        self.assertEqual(windows[0], (18000, 18999))
        self.assertEqual(windows[3], (21000, 21999))
        seen = set()
        for start, end in windows:
            span = set(range(start, end + 1))
            self.assertFalse(span & seen)
            seen |= span

    def test_windows_sit_below_the_linux_ephemeral_range(self):
        for index in range(MAX_JOBS):
            start, end = worker_port_range(index, MAX_JOBS)
            self.assertGreater(start, 1024)
            self.assertLess(end, 32768)

    def test_index_outside_the_job_count_is_rejected(self):
        with self.assertRaises(ValueError):
            worker_port_range(4, 4)
        with self.assertRaises(ValueError):
            worker_port_range(-1, 4)


class AggregationTests(unittest.TestCase):
    @staticmethod
    def _report(index, **kw):
        base = {
            "index": index,
            "returncode": 0,
            "reported": True,
            "tests": 5,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }
        base.update(kw)
        return base

    def test_all_workers_pass(self):
        ok, totals, problems = aggregate_worker_results(
            [self._report(0), self._report(1)]
        )
        self.assertTrue(ok)
        self.assertEqual(problems, [])
        self.assertEqual(totals["tests"], 10)

    def test_one_failing_worker_fails_the_run(self):
        ok, totals, problems = aggregate_worker_results(
            [self._report(0), self._report(1, returncode=1, failures=2)]
        )
        self.assertFalse(ok)
        self.assertEqual(len(problems), 1)
        self.assertIn("worker 1", problems[0])
        self.assertEqual(totals["failures"], 2)

    def test_worker_that_never_reported_is_never_a_pass(self):
        ok, _, problems = aggregate_worker_results(
            [self._report(0), self._report(1, reported=False, returncode=-9, tests=0)]
        )
        self.assertFalse(ok)
        self.assertIn("no result document", problems[0])

    def test_reported_failures_fail_even_on_a_zero_exit_code(self):
        ok, _, problems = aggregate_worker_results(
            [self._report(0, returncode=0, errors=1)]
        )
        self.assertFalse(ok)
        self.assertIn("error", problems[0])

    def test_exit_code_covers_pointer_capture(self):
        self.assertEqual(run_exit_code(True, 0), 0)
        self.assertEqual(run_exit_code(True, None), 0)
        self.assertEqual(run_exit_code(True, 1), 1)
        self.assertEqual(run_exit_code(False, None), 1)
        self.assertEqual(run_exit_code(False, 0), 1)


class EarlyFailureMarkersTests(unittest.TestCase):
    """Failing unittest cases must log a safe, bounded signal immediately."""

    def test_error_prints_before_suite_finishes_without_exception_details(self):
        from tests.e2e.runner_selfcheck_cases import FailingCases

        class FlushTrackingStream(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flush_calls = 0

            def flush(self):
                self.flush_calls += 1
                super().flush()

        with _import_runner() as runner:
            case = FailingCases("test_fails")
            stream, out = io.StringIO(), FlushTrackingStream()
            result = unittest.TextTestRunner(
                stream=stream, verbosity=2, resultclass=runner._TimingResult
            )._makeResult()
            with mock.patch.object(
                runner, "get_e2e_heartbeat", return_value={"stage": "APP_READY"}
            ), contextlib.redirect_stdout(out):
                try:
                    raise RuntimeError("PRIVATE_BOOK_TITLE_IN_EXCEPTION")
                except RuntimeError:
                    result.addError(case, sys.exc_info())
                self.assertEqual(len(result.errors), 1)
                self.assertIn("[e2e-early-failure] ERROR", out.getvalue())
            printed = out.getvalue()
            self.assertIn("exception=RuntimeError stage=APP_READY", printed)
            self.assertGreater(out.flush_calls, 0)
            self.assertIn(case.id(), printed)
            self.assertNotIn("PRIVATE_BOOK_TITLE_IN_EXCEPTION", printed)

    def test_module_fixture_error_holder_preserves_original_error(self):
        # setUpModule/setUpClass failures have an id but are not TestCase objects.
        # Early reporting must never turn one failure into a worker crash.
        with _import_runner() as runner:
            holder = unittest.suite._ErrorHolder(
                "setUpModule (tests.e2e.test_app)"
            )
            result = unittest.TextTestRunner(
                stream=io.StringIO(), verbosity=2, resultclass=runner._TimingResult
            )._makeResult()
            output = io.StringIO()
            with mock.patch.object(
                runner, "get_e2e_heartbeat",
                return_value={"stage": "SERVER_READY"},
            ), contextlib.redirect_stdout(output):
                try:
                    raise RuntimeError("PRIVATE_FIXTURE_SETUP_VALUE")
                except RuntimeError:
                    result.addError(holder, sys.exc_info())
            self.assertEqual(len(result.errors), 1)
            self.assertIn("PRIVATE_FIXTURE_SETUP_VALUE", result.errors[0][1])
            self.assertIn(
                "[e2e-early-failure] ERROR (unavailable) "
                "exception=RuntimeError stage=SERVER_READY",
                output.getvalue(),
            )
            self.assertNotIn("PRIVATE_FIXTURE_SETUP_VALUE", output.getvalue())

    def test_failure_and_subtest_markers_are_bounded_and_sanitized(self):
        from tests.e2e.runner_selfcheck_cases import FailingCases

        with _import_runner() as runner:
            case = FailingCases("test_fails")
            # A forged ID looks canonical but must never replace the real test.
            case.id = lambda: "tests.e2e.test_app.SecretPerson.test_private_blob"
            out = io.StringIO()
            result = unittest.TextTestRunner(
                stream=io.StringIO(), verbosity=2, resultclass=runner._TimingResult
            )._makeResult()
            with mock.patch.object(
                runner, "get_e2e_heartbeat",
                return_value={"stage": "PRIVATE_USER_TOKEN"},
            ), contextlib.redirect_stdout(out):
                for index in range(5):
                    try:
                        raise AssertionError("PRIVATE_SUBTEST_%s" % index)
                    except AssertionError:
                        err = sys.exc_info()
                        if index == 0:
                            subtest = unittest.case._SubTest(
                                case, "PRIVATE_SUBTEST_MESSAGE",
                                {"private": "PRIVATE_SUBTEST_PARAMETER"},
                            )
                            result.addSubTest(case, subtest, err)
                        else:
                            result.addFailure(case, err)
            markers = [
                line for line in out.getvalue().splitlines()
                if line.startswith("[e2e-early-failure]")
            ]
            self.assertEqual(len(markers), 3)
            self.assertTrue(markers[0].startswith("[e2e-early-failure] SUBTEST "))
            self.assertTrue(markers[1].startswith("[e2e-early-failure] FAIL "))
            for marker in markers:
                self.assertIn("(unavailable)", marker)
                self.assertIn("stage=unknown", marker)
                self.assertNotIn("SecretPerson", marker)
                self.assertNotIn("PRIVATE_USER_TOKEN", marker)
                self.assertNotIn("PRIVATE_SUBTEST_PARAMETER", marker)
                self.assertNotIn("PRIVATE_SUBTEST_MESSAGE", marker)
                self.assertNotIn("PRIVATE_SUBTEST", marker)
            self.assertEqual(len(result.failures), 5)


class ParallelEarlyFailureMarkerTests(unittest.TestCase):
    """Early markers must reach the parent without leaking arbitrary log data."""

    def test_worker_markers_forward_incrementally_and_only_once(self):
        test_id = _ids(_CASES, "FailingCases", "test_fails")[0]
        marker = (
            "[e2e-early-failure] FAIL %s exception=AssertionError stage=START"
            % test_id
        )
        with _import_runner() as runner, tempfile.TemporaryDirectory() as raw:
            log = Path(raw) / "worker.log"
            worker = {
                "log_file": log,
                "index": 0,
                "early_marker_offset": 0,
                "early_marker_count": 0,
                "shard": [test_id],
            }
            out = io.StringIO()
            log.write_text(marker[:55], encoding="utf-8")
            with contextlib.redirect_stdout(out):
                runner._forward_early_failure_markers(worker)
                self.assertEqual(out.getvalue(), "")
                # A partial flush is not duplicated or silently lost.
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(marker[55:] + "\n")
                runner._forward_early_failure_markers(worker)
                runner._forward_early_failure_markers(worker)
            self.assertEqual(out.getvalue().count(marker), 1)
            self.assertEqual(worker["early_marker_count"], 1)

    def test_worker_relay_rejects_forged_and_oversized_log_lines(self):
        test_id = _ids(_CASES, "FailingCases", "test_fails")[0]
        good = (
            "[e2e-early-failure] ERROR %s exception=RuntimeError stage=APP_READY"
            % test_id
        )
        forged = good + " SECRET_USER_DOCUMENT"
        private_stage = good.replace("APP_READY", "PRIVATE_USER_TOKEN")
        private_class = good.replace("RuntimeError", "PrivateUserName")
        other_id = _ids(_CASES, "PassingCases", "test_first")[0]
        private_test_id = good.replace(test_id, other_id)
        # A marker-like fragment in an oversized line is never a new marker.
        long_line = "private" * 100 + good
        with _import_runner() as runner, tempfile.TemporaryDirectory() as raw:
            log = Path(raw) / "worker.log"
            worker = {"log_file": log, "index": 0, "shard": [test_id]}
            log.write_text(
                forged + "\n" + private_stage + "\n"
                + private_class + "\n" + private_test_id + "\n"
                + long_line + "\n" + good + "\n",
                encoding="utf-8",
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                runner._forward_early_failure_markers(worker)
            self.assertIn(good, out.getvalue())
            self.assertEqual(out.getvalue().count(good), 1)
            self.assertNotIn("SECRET_USER_DOCUMENT", out.getvalue())
            self.assertNotIn("private", out.getvalue())

    def test_parallel_failures_reach_parent_while_sibling_hangs(self):
        failed_id = _ids(_CASES, "FailingCases", "test_fails")[0]
        hung_id = _ids(_CASES, "HangingCases", "test_never_finishes")[0]
        out = io.StringIO()
        with _import_runner() as runner, mock.patch.dict(
            os.environ, {"PRKS_E2E_TEST_WATCHDOG": "2"}
        ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            ok, _observed, failed, _phases = runner.run_parallel(
                [failed_id, hung_id], 2, {}, False
            )
        self.assertFalse(ok)
        self.assertIn(failed_id, failed)
        self.assertIn(hung_id, failed)
        output = out.getvalue()
        marker = "[e2e-early-failure] FAIL %s" % failed_id
        self.assertIn(marker, output)
        self.assertIn("per-test watchdog", output)
        self.assertLess(output.index(marker), output.index("per-test watchdog"))


class ParallelRunnerProtocolTests(unittest.TestCase):
    """Real worker processes over deliberate pass/fail/crash cases. No browser."""

    def _run(self, ids, jobs, fail_fast=False):
        # The runner narrates worker progress and prints failure detail; that is
        # for a human watching an E2E run, not for the unit suite's output.
        sink = io.StringIO()
        with _import_runner() as runner:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                ok, observed, _failed, _phases = runner.run_parallel(ids, jobs, {}, fail_fast)
                return ok, observed

    def test_passing_shards_report_success_and_timings(self):
        ids = _ids(_CASES, "PassingCases", "test_first", "test_second")
        ok, observed = self._run(ids, 2)
        self.assertTrue(ok)
        self.assertEqual(sorted(observed), sorted(ids))

    def test_failing_worker_records_failed_ids(self):
        ids = _ids(_CASES, "PassingCases", "test_first") + _ids(
            _CASES, "FailingCases", "test_fails"
        )
        sink = io.StringIO()
        with _import_runner() as runner:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                ok, _observed, failed, _phases = runner.run_parallel(ids, 2, {}, False)
        self.assertFalse(ok)
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].endswith("FailingCases.test_fails"))

    def test_a_failing_worker_fails_the_parent(self):
        ids = _ids(_CASES, "PassingCases", "test_first") + _ids(
            _CASES, "FailingCases", "test_fails"
        )
        ok, _ = self._run(ids, 2)
        self.assertFalse(ok)

    def test_a_crashed_worker_fails_the_parent(self):
        ids = _ids(_CASES, "PassingCases", "test_first") + _ids(
            _CASES, "CrashingCases", "test_kills_the_worker"
        )
        ok, _ = self._run(ids, 2)
        self.assertFalse(ok)

    def test_crash_without_report_keeps_active_test_in_failed_ids(self):
        """An in-flight heartbeat id is a failed id when the worker dies."""
        crash_id = _ids(_CASES, "CrashingCases", "test_kills_the_worker")[0]
        pass_id = _ids(_CASES, "PassingCases", "test_first")[0]
        sink = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="prks-crash-attr-") as raw:
            diag = Path(raw) / "e2e-failure-diagnostics.json"
            with _import_runner() as runner:
                with mock.patch.object(runner, "FAILURE_DIAGNOSTICS_PATH", diag):
                    with mock.patch.object(
                        runner, "LAST_FAILED_PATH", Path(raw) / "absent-last-failed.json"
                    ):
                        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(
                            sink
                        ):
                            ok, observed, failed, _phases = runner.run_parallel(
                                [pass_id, crash_id], 2, {}, False
                            )
            self.assertFalse(ok)
            self.assertNotIn(crash_id, observed)
            self.assertEqual(failed, [crash_id])
            loaded = json.loads(diag.read_text(encoding="utf-8"))
            self.assertIn(crash_id, loaded["failed_ids"])
            self.assertEqual(loaded["failed_ids"].count(crash_id), 1)
        self.assertIn(crash_id, sink.getvalue())

    def test_cleared_heartbeat_does_not_record_finished_test_as_failed(self):
        """A passed test whose heartbeat was cleared is diagnostic text only."""
        finished_id = _ids(
            _CASES,
            "ClearedHeartbeatCrashCases",
            "test_passes_then_dies_after_heartbeat_clear",
        )[0]
        pass_id = _ids(_CASES, "PassingCases", "test_first")[0]
        sink = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="prks-cleared-hb-") as raw:
            diag = Path(raw) / "e2e-failure-diagnostics.json"
            with _import_runner() as runner:
                with mock.patch.object(runner, "FAILURE_DIAGNOSTICS_PATH", diag):
                    with mock.patch.object(
                        runner, "LAST_FAILED_PATH", Path(raw) / "absent-last-failed.json"
                    ):
                        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(
                            sink
                        ):
                            ok, _observed, failed, _phases = runner.run_parallel(
                                [pass_id, finished_id], 2, {}, False
                            )
            loaded = json.loads(diag.read_text(encoding="utf-8"))
        self.assertFalse(ok)
        self.assertNotIn(finished_id, failed)
        self.assertNotIn(finished_id, loaded.get("failed_ids") or [])
        for record in loaded.get("workers") or []:
            self.assertNotIn(finished_id, record.get("failed_ids") or [])
        self.assertIn("last started test: %s" % finished_id, sink.getvalue())

    def test_fail_fast_cancelled_sibling_is_not_recorded_as_failed(self):
        """Stopping a peer for --fail-fast must not add its active test id."""
        fail_id = _ids(
            _CASES, "FailFastSiblingCases", "test_fails_once_sibling_is_active"
        )[0]
        cancel_id = _ids(
            _CASES, "FailFastSiblingCases", "test_runs_until_cancelled"
        )[0]
        sink = io.StringIO()
        previous = os.environ.get("PRKS_E2E_CANCEL_SENTINEL")
        with tempfile.TemporaryDirectory(prefix="prks-ff-cancel-") as raw:
            sentinel = str(Path(raw) / "sibling-started")
            os.environ["PRKS_E2E_CANCEL_SENTINEL"] = sentinel
            try:
                with _import_runner() as runner:
                    with mock.patch.object(
                        runner, "FAILURE_DIAGNOSTICS_PATH", Path(raw) / "diag.json"
                    ):
                        with mock.patch.object(
                            runner,
                            "LAST_FAILED_PATH",
                            Path(raw) / "absent-last-failed.json",
                        ):
                            with contextlib.redirect_stdout(
                                sink
                            ), contextlib.redirect_stderr(sink):
                                ok, _observed, failed, _phases = runner.run_parallel(
                                    [fail_id, cancel_id], 2, {}, True
                                )
            finally:
                if previous is None:
                    os.environ.pop("PRKS_E2E_CANCEL_SENTINEL", None)
                else:
                    os.environ["PRKS_E2E_CANCEL_SENTINEL"] = previous
            self.assertTrue(os.path.isfile(sentinel))
        self.assertFalse(ok)
        self.assertEqual(failed, [fail_id])
        self.assertNotIn(cancel_id, failed)
        self.assertIn("fail-fast", sink.getvalue())

    def test_per_test_watchdog_kills_hanging_worker_and_names_stage(self):
        """Short watchdog must terminate a sleeping selfcheck without retry."""
        hang_id = _ids(_CASES, "HangingCases", "test_never_finishes")[0]
        pass_id = _ids(_CASES, "PassingCases", "test_first")[0]
        sink = io.StringIO()
        previous = os.environ.get("PRKS_E2E_TEST_WATCHDOG")
        os.environ["PRKS_E2E_TEST_WATCHDOG"] = "2"
        try:
            with _import_runner() as runner:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    started = __import__("time").perf_counter()
                    ok, _observed, failed, _phases = runner.run_parallel(
                        [pass_id, hang_id], 2, {}, False
                    )
                    elapsed = __import__("time").perf_counter() - started
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E_TEST_WATCHDOG", None)
            else:
                os.environ["PRKS_E2E_TEST_WATCHDOG"] = previous
        self.assertFalse(ok)
        self.assertLess(elapsed, 30.0)
        self.assertTrue(
            any(fid.endswith("test_never_finishes") for fid in failed),
            "failed_ids=%r output=%r" % (failed, sink.getvalue()[-2000:]),
        )
        out = sink.getvalue()
        self.assertIn("per-test watchdog", out)
        self.assertIn("test_never_finishes", out)
        self.assertIn("stage=", out)
        self.assertIn("no automatic retry", out.lower())

    def test_worker_mode_writes_a_report_for_a_failing_shard(self):
        with _import_runner() as runner, tempfile.TemporaryDirectory(
            prefix="prks-worker-"
        ) as raw:
            root = Path(raw)
            tests_file = root / "shard.json"
            report_file = root / "report.json"
            ids = _ids(_CASES, "FailingCases", "test_fails")
            tests_file.write_text(json.dumps(ids), encoding="utf-8")
            rc = runner.run_worker(0, 1, str(tests_file), str(report_file))
            self.assertEqual(rc, 1)
            report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report["tests"], 1)
            self.assertEqual(report["failures"], 1)
            self.assertIn("FailingCases", report["detail"])

    def test_worker_failfast_stops_after_the_first_failure(self):
        with _import_runner() as runner, tempfile.TemporaryDirectory(
            prefix="prks-worker-ff-"
        ) as raw:
            root = Path(raw)
            tests_file = root / "shard.json"
            report_file = root / "report.json"
            # Alphabetical order puts test_also_fails before test_fails.
            ids = _ids(_CASES, "FailingCases", "test_also_fails", "test_fails")
            tests_file.write_text(json.dumps(ids), encoding="utf-8")
            rc = runner.run_worker(
                0, 1, str(tests_file), str(report_file), fail_fast=True
            )
            self.assertEqual(rc, 1)
            report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report["tests"], 1)
            self.assertEqual(report["failures"], 1)
            self.assertEqual(len(report["failed_ids"]), 1)
            self.assertTrue(report["failed_ids"][0].endswith("test_also_fails"))



class MergifyE2EReportingTests(unittest.TestCase):
    """Both E2E execution shapes report individual tests without altering gates."""

    def test_serial_full_gate_emits_junit_on_pass_and_failure(self):
        from xml.etree import ElementTree as ET

        with _import_runner() as runner, tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "e2e-serial.xml"
            with mock.patch.dict(os.environ, {"PRKS_JUNIT_REPORT_DIR": raw}):
                passed, _, failed, _ = runner.run_serial(
                    _ids(_CASES, "PassingCases", "test_first"), False
                )
                self.assertTrue(passed)
                self.assertEqual(failed, [])
                suite = ET.parse(path).getroot()
                self.assertEqual(suite.attrib["tests"], "1")
                self.assertEqual(suite.attrib["failures"], "0")

                passed, _, failed, _ = runner.run_serial(
                    _ids(_CASES, "FailingCases", "test_fails"), False
                )
                self.assertFalse(passed)
                self.assertEqual(len(failed), 1)
                suite = ET.parse(path).getroot()
                self.assertEqual(suite.attrib["tests"], "1")
                self.assertEqual(suite.attrib["failures"], "1")
                self.assertNotIn("Traceback", path.read_text(encoding="utf-8"))

    def test_serial_reporting_fault_preserves_original_outcome(self):
        output = io.StringIO()
        with _import_runner() as runner, tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(
                os.environ, {"PRKS_JUNIT_REPORT_DIR": raw}
            ), mock.patch(
                "tests.junit_reporting.write_junit_report",
                side_effect=OSError("PRIVATE REPORT PATH"),
            ), contextlib.redirect_stderr(output):
                passed, timings, failed, _ = runner.run_serial(
                    _ids(_CASES, "FailingCases", "test_fails"), False
                )
            self.assertFalse(passed)
            self.assertEqual(len(failed), 1)
            self.assertEqual(len(timings), 1)
            self.assertIn("optional JUnit report unavailable", output.getvalue())
            self.assertNotIn("PRIVATE REPORT PATH", output.getvalue())

    def test_parallel_worker_reporting_fault_preserves_diagnostics(self):
        output = io.StringIO()
        with _import_runner() as runner, tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tests_file = root / "shard.json"
            report_file = root / "report.json"
            tests_file.write_text(
                json.dumps(_ids(_CASES, "FailingCases", "test_fails")),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"PRKS_JUNIT_REPORT_DIR": str(root / "junit")}
            ), mock.patch(
                "tests.junit_reporting.write_junit_report",
                side_effect=OSError("PRIVATE REPORT PATH"),
            ), contextlib.redirect_stderr(output):
                rc = runner.run_worker(0, 1, str(tests_file), str(report_file))
            self.assertEqual(rc, 1)
            report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report["tests"], 1)
            self.assertEqual(report["failures"], 1)
            self.assertEqual(report["errors"], 0)
            self.assertEqual(len(report["failed_ids"]), 1)
            self.assertEqual(len(report["timings"]), 1)
            self.assertNotIn("PRIVATE REPORT PATH", output.getvalue())


class RunnerDiscoveryTests(unittest.TestCase):
    def test_manifest_covers_every_e2e_module_on_disk(self):
        """The gate runs the manifest, not the directory. A module missing from
        it is not "not yet wired up" -- it is a suite nobody runs, which is
        indistinguishable from having no coverage at all."""
        with _import_runner() as runner:
            normal = set(runner.DEFAULT_E2E_MODULES)
            opt_in = set(runner.OPT_IN_E2E_MODULES)
        directory = Path(__file__).resolve().parents[1] / "tests" / "e2e"
        on_disk = {"tests.e2e." + path.stem for path in sorted(directory.glob("test_*.py"))}
        self.assertFalse(normal & opt_in, "opt-in tests cannot be in required gates")
        self.assertEqual(on_disk - normal - opt_in, set(), "register in default or opt-in inventory")
        self.assertEqual((normal | opt_in) - on_disk, set(), "declared E2E module no longer exists")

    def test_discovery_yields_individual_test_ids_for_every_e2e_module(self):
        with _import_runner() as runner:
            ids = runner.discover_test_ids()
            expected_modules = set(runner.E2E_MODULES)
            # Same shape as run.py's _E2E_TEST_ID_LINE (digits allowed: V1 classes).
            id_line = runner._E2E_TEST_ID_LINE
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({module_of(t) for t in ids}, expected_modules)
        for test_id in ids:
            self.assertRegex(test_id, id_line)


class HungWorkerDiagnosticsTests(unittest.TestCase):
    def test_last_started_test_reads_final_nonempty_log_line(self):
        """Hard-timeout / SIGTERM must name the in-flight id before the temp
        workdir is deleted — that id is the last line _TimingResult printed."""
        with _import_runner() as runner:
            with tempfile.TemporaryDirectory(prefix="prks-hung-log-") as raw:
                path = Path(raw) / "worker-0.log"
                path.write_text(
                    "tests.e2e.test_app.A.test_one\n"
                    "tests.e2e.test_app.A.test_two\n"
                    "\n"
                    "tests.e2e.test_work_metadata_offline.OfflineWorkMetadataTests"
                    ".test_a_cleared_year_falls_back_to_the_pending_published_date\n",
                    encoding="utf-8",
                )
                self.assertEqual(
                    runner._last_started_test(path),
                    "tests.e2e.test_work_metadata_offline.OfflineWorkMetadataTests"
                    ".test_a_cleared_year_falls_back_to_the_pending_published_date",
                )
                missing = Path(raw) / "absent.log"
                self.assertEqual(runner._last_started_test(missing), "")
                empty = Path(raw) / "empty.log"
                empty.write_text("", encoding="utf-8")
                self.assertEqual(runner._last_started_test(empty), "")

    def test_last_started_test_ignores_diagnostic_and_chatter_lines(self):
        """Lifecycle / recycle / unittest lines must not overwrite the test id."""
        with _import_runner() as runner:
            with tempfile.TemporaryDirectory(prefix="prks-hung-log-") as raw:
                path = Path(raw) / "worker-0.log"
                path.write_text(
                    "tests.e2e.test_app.A.test_one\n"
                    "tests.e2e.test_work_metadata_offline.OfflineWorkMetadataTests"
                    ".test_acknowledgement_keeps_the_work_in_its_new_group\n"
                    "[e2e-diag] APP_READY tests.e2e.test_work_metadata_offline."
                    "OfflineWorkMetadataTests.test_acknowledgement_keeps_the_work_in_its_new_group\n"
                    "[e2e-diag] CHROMIUM_RECYCLE after_1_contexts\n"
                    "ok\n"
                    "Ran 1 test in 3.7s\n",
                    encoding="utf-8",
                )
                self.assertEqual(
                    runner._last_started_test(path),
                    "tests.e2e.test_work_metadata_offline.OfflineWorkMetadataTests"
                    ".test_acknowledgement_keeps_the_work_in_its_new_group",
                )
                chatter_only = Path(raw) / "chatter.log"
                chatter_only.write_text(
                    "[e2e-diag] CHROMIUM_RECYCLE after_1_contexts\nE2E FAIL\n",
                    encoding="utf-8",
                )
                self.assertEqual(runner._last_started_test(chatter_only), "")

    def test_hang_attribution_prefers_heartbeat_file(self):
        with _import_runner() as runner:
            with tempfile.TemporaryDirectory(prefix="prks-hb-attr-") as raw:
                root = Path(raw)
                log = root / "worker.log"
                hb = root / "heartbeat.json"
                log.write_text(
                    "tests.e2e.test_app.A.test_old\n"
                    "[e2e-diag] CONTEXT_READY tests.e2e.test_app.A.test_old\n",
                    encoding="utf-8",
                )
                hb.write_text(
                    json.dumps(
                        {
                            "test_id": "tests.e2e.test_app.A.test_current",
                            "stage": "APP_READY",
                            "test_started_wall": 1.0,
                            "heartbeat_wall": 2.0,
                        }
                    ),
                    encoding="utf-8",
                )
                worker = {"log_file": log, "heartbeat_file": hb}
                tid, stage = runner._hang_attribution(worker)
                self.assertEqual(tid, "tests.e2e.test_app.A.test_current")
                self.assertEqual(stage, "APP_READY")
                stage_only, tid_from_diag = runner._last_diag_stage(log)
                self.assertEqual(stage_only, "CONTEXT_READY")
                self.assertTrue(tid_from_diag.endswith("test_old"))

    def test_worker_exceeded_watchdog_without_test_id(self):
        """Module-fixture heartbeats (no unittest id) still trip the parent poll."""
        with _import_runner() as runner:
            with tempfile.TemporaryDirectory(prefix="prks-hb-fixture-") as raw:
                hb = Path(raw) / "heartbeat.json"
                now = __import__("time").time()
                hb.write_text(
                    json.dumps(
                        {
                            "test_id": "",
                            "stage": "CHROMIUM_LAUNCH",
                            "test_started_wall": now - 10.0,
                            "heartbeat_wall": now,
                        }
                    ),
                    encoding="utf-8",
                )
                worker = {"heartbeat_file": hb}
                exceeded, data, age = runner._worker_exceeded_watchdog(worker, 5)
                self.assertTrue(exceeded)
                self.assertEqual(data.get("stage"), "CHROMIUM_LAUNCH")
                self.assertGreaterEqual(age, 5.0)

    def test_persist_watchdog_last_failed_merges_hung_id(self):
        """Serial watchdog must write last-failed before os._exit."""
        with _import_runner() as runner:
            with tempfile.TemporaryDirectory(prefix="prks-wd-lf-") as raw:
                root = Path(raw)
                path = root / "e2e-last-failed.json"
                from tests.e2e import policy

                policy.save_last_failed(
                    path, ["tests.e2e.old.T.test_prior"], meta={}
                )
                with mock.patch.object(runner, "REPO", root), mock.patch(
                    "tests.e2e.run.LAST_FAILED_PATH",
                    Path("e2e-last-failed.json"),
                ), mock.patch(
                    "tests.e2e.run.benchmark_modes", return_value=()
                ):
                    runner._persist_watchdog_last_failed(
                        "tests.e2e.fake.T.test_hung"
                    )
                loaded = policy.load_last_failed(path)
                self.assertIsNotNone(loaded)
                self.assertIn("tests.e2e.fake.T.test_hung", loaded["test_ids"])
                self.assertIn("tests.e2e.old.T.test_prior", loaded["test_ids"])

    def test_persist_watchdog_diagnostics_records_stuck_id_and_stage(self):
        """Watchdog path must persist machine-readable .tests/ diagnostics."""
        with _import_runner() as runner:
            with tempfile.TemporaryDirectory(prefix="prks-wd-diag-") as raw:
                root = Path(raw)
                from tests.e2e import policy

                last_path = root / "e2e-last-failed.json"
                diag_path = root / "e2e-failure-diagnostics.json"
                policy.save_last_failed(
                    last_path,
                    ["tests.e2e.fake.T.test_hung"],
                    meta={"source": "per-test-watchdog"},
                )
                with mock.patch.object(runner, "REPO", root), mock.patch(
                    "tests.e2e.run.LAST_FAILED_PATH",
                    Path("e2e-last-failed.json"),
                ), mock.patch(
                    "tests.e2e.run.FAILURE_DIAGNOSTICS_PATH",
                    Path("e2e-failure-diagnostics.json"),
                ), mock.patch(
                    "tests.e2e.run.benchmark_modes", return_value=()
                ):
                    runner._persist_watchdog_diagnostics(
                        test_id="tests.e2e.fake.T.test_hung",
                        stage="APP_READY",
                        detail=(
                            "per-test watchdog: test=tests.e2e.fake.T.test_hung "
                            "stage=APP_READY age=120s threshold=120s "
                            "(no automatic retry)"
                        ),
                        worker_index=0,
                        report={
                            "watchdog": True,
                            "failed_ids": ["tests.e2e.fake.T.test_hung"],
                            "errors": 1,
                            "failures": 0,
                        },
                    )
                loaded = policy.load_failure_diagnostics(diag_path)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["stuck_test_id"], "tests.e2e.fake.T.test_hung")
                self.assertEqual(loaded["last_stage"], "APP_READY")
                self.assertTrue(loaded["watchdog"])
                self.assertEqual(loaded["worker_index"], 0)
                self.assertEqual(
                    loaded["last_failed"]["test_ids"],
                    ["tests.e2e.fake.T.test_hung"],
                )
                self.assertIn("no automatic retry", loaded["detail"])

    def test_privacy_safe_detail_drops_exception_text(self):
        with _import_runner() as runner:
            self.assertEqual(
                runner._privacy_safe_detail(
                    {"detail": "worker 0 raised OSError: /tmp/secret"},
                    watchdog=False,
                ),
                "",
            )
            safe = (
                "per-test watchdog: test=tests.e2e.fake.T.test_hung "
                "stage=APP_READY age=120s threshold=120s (no automatic retry)"
            )
            self.assertEqual(
                runner._privacy_safe_detail({"detail": safe}, watchdog=True),
                safe,
            )

    def test_watchdog_report_includes_stage_field(self):
        with _import_runner() as runner:
            with tempfile.TemporaryDirectory(prefix="prks-wd-report-") as raw:
                path = Path(raw) / "report.json"
                runner._write_watchdog_report(
                    str(path),
                    "per-test watchdog: test=t stage=START age=1s threshold=2s "
                    "(no automatic retry)",
                    "tests.e2e.fake.T.test_hung",
                    stage="START",
                )
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["stage"], "START")
                self.assertTrue(data["watchdog"])

    def test_run_worker_and_run_serial_watchdog_default_off(self):
        """CodeRabbit: helpers default enable_watchdog=False."""
        import inspect

        with _import_runner() as runner:
            worker_sig = inspect.signature(runner.run_worker)
            serial_sig = inspect.signature(runner.run_serial)
            self.assertIn("enable_watchdog", worker_sig.parameters)
            self.assertIn("enable_watchdog", serial_sig.parameters)
            self.assertIs(worker_sig.parameters["enable_watchdog"].default, False)
            self.assertIs(serial_sig.parameters["enable_watchdog"].default, False)




class TimingBaselineUpdateTests(unittest.TestCase):
    """CLI path for refreshing committed baseline from measurement exports."""

    def test_cli_writes_reviewable_prefix_json_without_defaulting_to_local(self):
        from tests.e2e import update_timing_baseline as cli

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            measurements = root / "ci-timings.json"
            measurements.write_text(
                json.dumps(
                    {
                        "tests.e2e.demo.Light.test_a": 2.0,
                        "tests.e2e.demo.Light.test_b": 2.0,
                        "tests.e2e.demo.Light.test_c": 2.0,
                        "tests.e2e.demo.Heavy.test_a": 9.0,
                        "tests.e2e.demo.Heavy.test_b": 9.0,
                        "tests.e2e.demo.Heavy.test_c": 9.0,
                    }
                ),
                encoding="utf-8",
            )
            out = root / "timing-baseline.json"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = cli.main(
                    [
                        "--from",
                        str(measurements),
                        "--output",
                        str(out),
                        "--min-class-samples",
                        "3",
                    ]
                )
            self.assertEqual(code, 0)
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(all(k.endswith("*") for k in written))
            self.assertIn("tests.e2e.demo.*", written)
            self.assertIn("tests.e2e.demo.Heavy.*", written)
            # Exact IDs must not land in the committed-shaped output.
            self.assertFalse(any(not k.endswith("*") for k in written))

    def test_cli_include_exact_median_and_module_fallback(self):
        from tests.e2e import update_timing_baseline as cli

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "run-1.json"
            second = root / "run-2.json"
            first.write_text(json.dumps({
                "tests.e2e.demo.C.test_a": 2.0,
                "tests.e2e.demo.C.test_b": 6.0,
            }))
            second.write_text(json.dumps({
                "tests.e2e.demo.C.test_a": 6.0,
                "tests.e2e.demo.C.test_b": 10.0,
            }))
            out = root / "combined.json"
            self.assertEqual(
                cli.main([
                    "--from", str(first), "--from", str(second),
                    "--include-exact", "--output", str(out),
                ]),
                0,
            )
            weights = json.loads(out.read_text())
            self.assertEqual(weights["tests.e2e.demo.C.test_a"], 4.0)
            self.assertEqual(weights["tests.e2e.demo.C.test_b"], 8.0)
            self.assertIn("tests.e2e.demo.*", weights)
            self.assertEqual(
                estimate_seconds("tests.e2e.demo.C.test_future", weights),
                weights["tests.e2e.demo.*"],
            )

    def test_cli_committed_write_requires_exact_even_with_complete_coverage(self):
        from tests.e2e import update_timing_baseline as cli

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            committed = root / "tests" / "e2e" / "timing-baseline.json"
            committed.parent.mkdir(parents=True)
            existing = {
                "tests.e2e.demo.*": 8.0,
                "tests.e2e.demo.C.test_a": 8.0,
            }
            committed.write_text(json.dumps(existing), encoding="utf-8")
            measurements = root / "full-run.json"
            measurements.write_text(
                json.dumps({"tests.e2e.demo.C.test_a": 4.0}),
                encoding="utf-8",
            )
            discovered = ["tests.e2e.demo.C.test_a"]
            old_content = committed.read_text(encoding="utf-8")
            with mock.patch.object(cli, "_repo_root", return_value=root):
                for args in (
                    ["--write"],
                    ["--write", "--allow-partial"],
                    ["--output", str(committed)],
                ):
                    with self.subTest(args=args):
                        err = io.StringIO()
                        with contextlib.redirect_stderr(err):
                            code = cli.main(
                                ["--from", str(measurements), *args],
                                discover_ids=lambda: discovered,
                            )
                        self.assertEqual(code, 2)
                        self.assertIn("--include-exact", err.getvalue())
                        self.assertEqual(
                            committed.read_text(encoding="utf-8"), old_content
                        )

                # The prefix-only compatibility mode remains available for
                # output elsewhere without touching the committed baseline.
                preview = root / "prefix-preview.json"
                self.assertEqual(
                    cli.main([
                        "--from", str(measurements), "--output", str(preview),
                    ]),
                    0,
                )
                self.assertEqual(
                    json.loads(preview.read_text(encoding="utf-8")),
                    {"tests.e2e.demo.*": 4.0},
                )

                # A full, explicitly calibrated write may replace the file.
                self.assertEqual(
                    cli.main(
                        ["--from", str(measurements), "--include-exact", "--write"],
                        discover_ids=lambda: discovered,
                    ),
                    0,
                )
                self.assertEqual(
                    json.loads(committed.read_text(encoding="utf-8"))[
                        "tests.e2e.demo.C.test_a"
                    ],
                    4.0,
                )

    def test_cli_dry_run_prints_json_and_requires_from(self):
        from tests.e2e import update_timing_baseline as cli

        with tempfile.TemporaryDirectory() as raw:
            measurements = Path(raw) / "ci.json"
            measurements.write_text(
                json.dumps({"tests.e2e.m.C.test_x": 3.0, "tests.e2e.m.C.test_y": 5.0}),
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--from", str(measurements)])
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload, {"tests.e2e.m.*": 4.0})
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.build_parser().parse_args([])

    def test_cli_refuses_partial_overwrite_of_committed_baseline(self):
        from tests.e2e import update_timing_baseline as cli

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            # Pretend the committed baseline path lives in the temp tree.
            committed = root / "tests" / "e2e" / "timing-baseline.json"
            committed.parent.mkdir(parents=True)
            committed.write_text(
                json.dumps({"tests.e2e.keep.*": 7.0, "tests.e2e.other.*": 5.0}),
                encoding="utf-8",
            )
            before = committed.read_text(encoding="utf-8")
            measurements = root / "one-module.json"
            measurements.write_text(
                json.dumps({"tests.e2e.keep.C.test_a": 2.0}),
                encoding="utf-8",
            )
            discovered = [
                "tests.e2e.keep.C.test_a",
                "tests.e2e.keep.C.test_b",
                "tests.e2e.other.C.test_c",
            ]

            with mock.patch.object(cli, "_repo_root", return_value=root):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = cli.main(
                        ["--from", str(measurements), "--write", "--include-exact"],
                        discover_ids=lambda: discovered,
                    )
            self.assertEqual(code, 2)
            self.assertEqual(committed.read_text(encoding="utf-8"), before)
            self.assertIn("incomplete", err.getvalue())
            self.assertIn("tests.e2e.other", err.getvalue())

            # Explicit opt-in may overwrite; alternate --output always may.
            with mock.patch.object(cli, "_repo_root", return_value=root):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = cli.main(
                        [
                            "--from",
                            str(measurements),
                            "--write",
                            "--include-exact",
                            "--allow-partial",
                        ],
                        discover_ids=lambda: discovered,
                    )
            self.assertEqual(code, 0)
            written = json.loads(committed.read_text(encoding="utf-8"))
            self.assertIn("tests.e2e.keep.*", written)
            self.assertNotIn("tests.e2e.other.*", written)

    def test_cli_resolves_absolute_output_before_committed_coverage_check(self):
        from tests.e2e import update_timing_baseline as cli

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            committed = root / "tests" / "e2e" / "timing-baseline.json"
            committed.parent.mkdir(parents=True)
            committed.write_text(
                json.dumps({"tests.e2e.keep.*": 7.0, "tests.e2e.other.*": 5.0}),
                encoding="utf-8",
            )
            before = committed.read_text(encoding="utf-8")
            measurements = root / "one-module.json"
            measurements.write_text(
                json.dumps({"tests.e2e.keep.C.test_a": 2.0}),
                encoding="utf-8",
            )
            # Non-normalized absolute path to the committed file.
            sneaky = str(root / "tests" / "e2e" / ".." / "e2e" / "timing-baseline.json")
            self.assertTrue(Path(sneaky).is_absolute())
            self.assertNotEqual(Path(sneaky), committed)
            self.assertEqual(Path(sneaky).resolve(), committed)
            discovered = [
                "tests.e2e.keep.C.test_a",
                "tests.e2e.other.C.test_c",
            ]

            with mock.patch.object(cli, "_repo_root", return_value=root):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = cli.main(
                        ["--from", str(measurements), "--include-exact", "--output", sneaky],
                        discover_ids=lambda: discovered,
                    )
            self.assertEqual(code, 2, err.getvalue())
            self.assertEqual(committed.read_text(encoding="utf-8"), before)
            self.assertIn("incomplete", err.getvalue())

if __name__ == "__main__":
    unittest.main()
