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

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.e2e.sharding import (
    DEFAULT_TEST_SECONDS,
    MAX_JOBS,
    aggregate_worker_results,
    assign_shards,
    estimate_seconds,
    format_slowest,
    load_timings,
    merge_timings,
    module_of,
    parse_jobs,
    run_exit_code,
    save_timings,
    shard_estimates,
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
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({module_of(t) for t in ids}, expected_modules)
        for test_id in ids:
            self.assertRegex(test_id, r"^tests\.e2e\.[A-Za-z_]+\.[A-Za-z_]+\.test_")


if __name__ == "__main__":
    unittest.main()
