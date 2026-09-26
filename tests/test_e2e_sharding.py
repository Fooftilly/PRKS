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

    def test_committed_baseline_file_contains_only_prefix_keys(self):
        repo = Path(_PROJECT_DIR)
        baseline = load_timings(repo / BASELINE_TIMINGS_PATH)
        self.assertTrue(baseline, "committed timing-baseline.json should not be empty")
        for key in baseline:
            self.assertTrue(is_baseline_prefix_key(key), key)

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
        self.assertTrue(any(fid.endswith("test_fails") for fid in failed))

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


class RunnerDiscoveryTests(unittest.TestCase):
    def test_manifest_covers_every_e2e_module_on_disk(self):
        """The gate runs the manifest, not the directory. A module missing from
        it is not "not yet wired up" -- it is a suite nobody runs, which is
        indistinguishable from having no coverage at all."""
        with _import_runner() as runner:
            listed = set(runner.E2E_MODULES)
        directory = Path(__file__).resolve().parents[1] / "tests" / "e2e"
        on_disk = {"tests.e2e." + path.stem for path in sorted(directory.glob("test_*.py"))}
        self.assertEqual(on_disk - listed, set(), "add these to run.py's E2E_MODULES")
        self.assertEqual(listed - on_disk, set(), "these E2E modules no longer exist")

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
                        ["--from", str(measurements), "--write"],
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
                            "--allow-partial",
                        ],
                        discover_ids=lambda: discovered,
                    )
            self.assertEqual(code, 0)
            written = json.loads(committed.read_text(encoding="utf-8"))
            self.assertIn("tests.e2e.keep.*", written)
            self.assertNotIn("tests.e2e.other.*", written)

if __name__ == "__main__":
    unittest.main()
