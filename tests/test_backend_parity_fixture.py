"""The SQLite/PostgreSQL parity fixture and its report (#625, PG-3).

No PostgreSQL server is needed here: the ``postgres`` twin's library is
replaced by a recorder over a fake object, which is all the skip and
selection rules depend on. ``tests/pg/test_backend_parity.py`` covers the
real facade over a harness database.
"""

from __future__ import annotations

import importlib.util
import inspect
import io
from collections import Counter
import os
import sys
import tempfile
import types
import unittest
import unittest.mock
from collections.abc import Callable
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.pg_facade import NotPortedError
from backend.storage.config import StorageConfig
from tests.backend_parity import (
    ENV_PARITY_BACKENDS,
    POSTGRES,
    SKIP_PREFIX,
    SQLITE,
    BackendParityTestCase,
    ParityLog,
    ParityRecorder,
    enabled_backends,
    parametrize_backends,
    with_refusal_log,
)

_SCRIPT = os.path.join(_PROJECT_DIR, "scripts", "run_backend_parity.py")
_SPEC = importlib.util.spec_from_file_location("prks_run_backend_parity", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_RUNNER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RUNNER)
ParityResult = _RUNNER.ParityResult
build_report = _RUNNER.build_report
format_report = _RUNNER.format_report
report_ok = _RUNNER.report_ok
selected_modules = _RUNNER.selected_modules

PORTED = frozenset({"get_saved_views"})


class _FakeLibrary:
    """Stands in for the facade: answers ported calls, refuses the rest."""

    def get_saved_views(self):
        return [{"id": "SV-1"}]

    def create_saved_view(self, name, search):
        raise AssertionError("an unported method must never be reached")


class _NestingFakeLibrary:
    """A facade whose ported method calls an unported one through ``self``."""

    def __init__(self, *, propagate: bool) -> None:
        self.propagate = propagate

    def get_saved_views(self):
        try:
            self.create_saved_view("n", {})
        except NotPortedError:
            if self.propagate:
                raise
        return [{"id": "SV-1"}]

    def __getattr__(self, name):
        if name == "create_saved_view":
            raise NotPortedError(name)
        raise AttributeError(name)


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _run(*classes: type) -> ParityResult:
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes
    )
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0, resultclass=ParityResult)
    result = runner.run(suite)
    assert isinstance(result, ParityResult)
    return result


def _fake_postgres(
    cls: type[BackendParityTestCase], make_library: Callable[[], Any] = _FakeLibrary
) -> type[BackendParityTestCase]:
    """The class's twin, with the facade replaced by a recorder over a fake."""

    def open_library(self: BackendParityTestCase, storage: Any) -> Any:
        if self.parity_backend == POSTGRES:
            library = make_library()
            library.__class__ = with_refusal_log(type(library), self.parity_log)
            return ParityRecorder(library, PORTED, self.parity_log)
        return _FakeLibrary()

    module = sys.modules[cls.__module__]
    cls.open_library = open_library  # type: ignore[method-assign]
    with unittest.mock.patch.dict(os.environ, {ENV_PARITY_BACKENDS: "sqlite,postgres"}):
        parametrize_backends(cls)
    return getattr(module, cls.__name__ + "_postgres")


class EnabledBackendsTests(unittest.TestCase):
    def test_default_is_sqlite_only(self):
        self.assertEqual(enabled_backends({}), (SQLITE,))
        self.assertEqual(enabled_backends({ENV_PARITY_BACKENDS: "  "}), (SQLITE,))

    def test_both_legs_in_canonical_order(self):
        self.assertEqual(
            enabled_backends({ENV_PARITY_BACKENDS: "postgres, sqlite"}), (SQLITE, POSTGRES)
        )

    def test_unknown_or_sqlite_less_selection_is_refused(self):
        with self.assertRaises(ValueError):
            enabled_backends({ENV_PARITY_BACKENDS: "sqlite,mysql"})
        with self.assertRaises(ValueError):
            enabled_backends({ENV_PARITY_BACKENDS: "postgres"})


class FixtureSelectionTests(unittest.TestCase):
    def setUp(self):
        self.module = _module("tests._parity_selection_probe")
        self.addCleanup(sys.modules.pop, self.module.__name__, None)

    def _define(self) -> type[BackendParityTestCase]:
        class Probe(BackendParityTestCase):
            def test_a(self):
                pass

        Probe.__module__ = self.module.__name__
        setattr(self.module, "Probe", Probe)
        return Probe

    def test_default_run_adds_no_postgres_twin(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_PARITY_BACKENDS, None)
            cls = parametrize_backends(self._define())
        self.assertIs(cls.parity_backend, SQLITE)
        self.assertFalse(hasattr(self.module, "Probe_postgres"))
        tests = unittest.defaultTestLoader.loadTestsFromModule(self.module)
        self.assertEqual(tests.countTestCases(), 1)

    def test_parity_run_adds_a_postgres_twin_with_the_same_tests(self):
        with unittest.mock.patch.dict(os.environ, {ENV_PARITY_BACKENDS: "sqlite,postgres"}):
            cls = parametrize_backends(self._define())
        twin = getattr(self.module, "Probe_postgres")
        self.assertTrue(issubclass(twin, cls))
        self.assertEqual(twin.parity_backend, POSTGRES)
        self.assertEqual(cls.parity_backend, SQLITE)
        self.assertEqual(twin.__module__, self.module.__name__)
        loader = unittest.defaultTestLoader
        self.assertEqual(loader.getTestCaseNames(twin), loader.getTestCaseNames(cls))
        self.assertEqual(loader.loadTestsFromModule(self.module).countTestCases(), 2)

    def test_only_parity_test_cases_can_be_parametrized(self):
        class Plain(unittest.TestCase):
            pass

        with self.assertRaises(TypeError):
            parametrize_backends(Plain)

    def test_sqlite_leg_opens_prksdatabase_in_its_own_root(self):
        class Probe(BackendParityTestCase):
            def runTest(self):
                pass

        probe = Probe()
        with tempfile.TemporaryDirectory() as tmp:
            library = probe.open_library(StorageConfig.for_testing(tmp))
            self.assertIsInstance(library, PRKSDatabase)
            self.assertTrue(os.path.exists(library.db_path))
            self.assertTrue(os.path.realpath(library.db_path).startswith(os.path.realpath(tmp)))

    def test_default_suite_has_no_postgres_classes(self):
        # What `python run_tests.py` discovers for the selected modules.
        env = {k: v for k, v in os.environ.items() if k != ENV_PARITY_BACKENDS}
        code = (
            "import os, sys, unittest\n"
            "suite = unittest.TestLoader().discover('tests', pattern='test_api_saved_views.py')\n"
            "def walk(s):\n"
            "    for t in s:\n"
            "        yield from (walk(t) if isinstance(t, unittest.TestSuite) else [t])\n"
            "legs = {getattr(t, 'parity_backend', None) for t in walk(suite)}\n"
            "mods = [m for m in sys.modules if m.split('.')[0] in ('psycopg', 'psycopg_pool')]\n"
            "sys.exit(0 if legs == {'sqlite'} and not mods else 1)\n"
        )
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=_PROJECT_DIR, env=env, check=False
        )
        self.assertEqual(proc.returncode, 0)

    def test_parity_runner_selects_the_opted_in_modules(self):
        modules = selected_modules()
        self.assertIn("tests.test_api_saved_views", modules)
        self.assertNotIn("tests.test_backend_parity_fixture", modules)


class SkipBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.module = _module("tests._parity_skip_probe")
        self.addCleanup(sys.modules.pop, self.module.__name__, None)

    def _twin(
        self, body: dict[str, Any], make_library: Callable[[], Any] = _FakeLibrary
    ) -> type[BackendParityTestCase]:
        def setUp(self):
            self.db = self.open_library(None)

        cls = type("Probe", (BackendParityTestCase,), {"setUp": setUp, **body})
        cls.__module__ = self.module.__name__
        setattr(self.module, "Probe", cls)
        return _fake_postgres(cls, make_library)

    def _postgres_counts(
        self, body: dict[str, Any], make_library: Callable[[], Any] = _FakeLibrary
    ) -> tuple[ParityResult, Any]:
        twin = self._twin(body, make_library)
        result = _run(twin)
        return result, result.outcomes.get(POSTGRES, {})

    def test_unported_call_skips_with_the_method_named(self):
        def test_it(self):
            self.db.create_saved_view("n", {})

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(counts["skipped_unported"], 1)
        self.assertEqual(result.skipped[0][1], SKIP_PREFIX + "create_saved_view")
        self.assertEqual(result.unported_skips["create_saved_view"], 1)

    def test_a_failure_after_an_unported_call_is_a_skip_not_a_failure(self):
        def test_it(self):
            try:
                self.db.create_saved_view("n", {})
            except NotPortedError:
                pass
            self.fail("would be a misleading failure")

        _result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(counts.get("failed", 0), 0)
        self.assertEqual(counts["skipped_unported"], 1)

    def test_a_swallowed_refusal_is_a_skip_not_a_pass(self):
        def test_it(self):
            try:
                self.db.create_saved_view("n", {})
            except Exception:
                pass

        _result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(counts.get("passed", 0), 0)
        self.assertEqual(counts["skipped_unported"], 1)

    def test_an_unported_call_in_setup_skips_the_test(self):
        def setUp(self):
            self.db = self.open_library(None)
            self.db.create_saved_view("seed", {})

        def test_it(self):
            raise AssertionError("never runs")

        _result, counts = self._postgres_counts({"setUp": setUp, "test_it": test_it})
        self.assertEqual(dict(counts), {"skipped_unported": 1})

    def test_a_ported_method_swallowing_a_nested_refusal_is_a_skip(self):
        def test_it(self):
            self.assertEqual(self.db.get_saved_views(), [{"id": "SV-1"}])

        result, counts = self._postgres_counts(
            {"test_it": test_it}, lambda: _NestingFakeLibrary(propagate=False)
        )
        self.assertEqual(dict(counts), {"skipped_unported": 1})
        self.assertEqual(result.skipped[0][1], SKIP_PREFIX + "create_saved_view")
        self.assertEqual(result.ported_evidence, set())

    def test_a_nested_refusal_the_test_catches_is_a_skip(self):
        def test_it(self):
            try:
                self.db.get_saved_views()
            except NotPortedError:
                pass

        result, counts = self._postgres_counts(
            {"test_it": test_it}, lambda: _NestingFakeLibrary(propagate=True)
        )
        self.assertEqual(dict(counts), {"skipped_unported": 1})
        self.assertEqual(result.ported_evidence, set())

    def test_ported_calls_run_real_assertions(self):
        def test_passes(self):
            self.assertEqual(self.db.get_saved_views(), [{"id": "SV-1"}])

        def test_fails(self):
            self.assertEqual(self.db.get_saved_views(), [])

        result, counts = self._postgres_counts({"test_passes": test_passes, "test_fails": test_fails})
        self.assertEqual(counts["passed"], 1)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(result.ported_evidence, {"get_saved_views"})

    def test_an_unported_call_inside_a_subtest_is_a_skip(self):
        def test_it(self):
            with self.subTest(case=1):
                self.db.create_saved_view("n", {})

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(dict(counts), {"skipped_unported": 1})
        self.assertTrue(result.wasSuccessful())

    def test_a_failing_subtest_on_ported_calls_fails_the_run(self):
        def test_it(self):
            with self.subTest(case=1):
                self.assertEqual(self.db.get_saved_views(), [])
            with self.subTest(case=2):
                self.assertEqual(self.db.get_saved_views(), [{"id": "SV-1"}])

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(counts["failed"], 1)
        self.assertFalse(result.wasSuccessful())
        report = build_report(
            result, [], surface=frozenset(), ported={}, problems=[]
        )
        self.assertFalse(report_ok(report))

    def test_an_unported_call_in_a_cleanup_skips_the_test(self):
        def test_it(self):
            self.addCleanup(self.db.create_saved_view, "n", {})
            self.assertEqual(self.db.get_saved_views(), [{"id": "SV-1"}])

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(dict(counts), {"skipped_unported": 1})
        self.assertTrue(result.wasSuccessful())

    def test_a_cleanup_after_a_skip_reports_the_test_once(self):
        def test_it(self):
            self.addCleanup(lambda: self.db.delete_saved_view("SV-1"))
            self.db.create_saved_view("n", {})

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(dict(counts), {"skipped_unported": 1})
        self.assertEqual(len(result.skipped), 1)

    def test_an_unrelated_cleanup_error_after_a_skip_still_errors(self):
        def test_it(self):
            self.addCleanup(lambda: 1 / 0)
            self.db.create_saved_view("n", {})

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(counts["skipped_unported"], 1)
        self.assertEqual(counts["errors"], 1)
        self.assertFalse(result.wasSuccessful())
        report = build_report(result, [], surface=frozenset(), ported={}, problems=[])
        self.assertFalse(report_ok(report))

    def test_looking_up_a_ported_method_is_not_evidence(self):
        def test_it(self):
            self.assertTrue(callable(self.db.get_saved_views))

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(dict(counts), {"passed_no_library": 1})
        self.assertEqual(result.ported_evidence, set())
        report = build_report(
            result,
            [],
            surface=frozenset({"get_saved_views"}),
            ported={"get_saved_views": "OrganizationMixin"},
            problems=[],
        )
        self.assertEqual(report["ported_without_passing_postgres_test"], ["get_saved_views"])
        self.assertFalse(report_ok(report))

    def test_a_real_cleanup_error_still_errors(self):
        def test_it(self):
            self.addCleanup(lambda: 1 / 0)
            self.db.get_saved_views()

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(counts["errors"], 1)
        self.assertFalse(result.wasSuccessful())

    def test_a_test_reaching_no_library_method_is_not_parity_evidence(self):
        def test_it(self):
            self.assertTrue(True)

        result, counts = self._postgres_counts({"test_it": test_it})
        self.assertEqual(dict(counts), {"passed_no_library": 1})
        self.assertEqual(result.ported_evidence, set())

    def test_sqlite_leg_is_unaffected_by_the_guard(self):
        def test_it(self):
            self.assertEqual(self.db.get_saved_views(), [{"id": "SV-1"}])

        twin = self._twin({"test_it": test_it})
        result = _run(self.module.Probe, twin)
        self.assertEqual(result.outcomes[SQLITE]["passed"], 1)
        self.assertEqual(result.outcomes[POSTGRES]["passed"], 1)

    def test_the_recorder_is_read_only_and_logs_per_test(self):
        log = ParityLog()
        recorder = ParityRecorder(_FakeLibrary(), PORTED, log)
        with self.assertRaises(AttributeError):
            recorder.anything = 1
        method = recorder.get_saved_views
        self.assertEqual(log.ported, set())
        self.assertEqual(inspect.signature(method), inspect.signature(_FakeLibrary().get_saved_views))
        method()
        with self.assertRaises(NotPortedError):
            recorder.create_saved_view("n", {})
        self.assertEqual(log.ported, {"get_saved_views"})
        self.assertEqual(log.unported, {"create_saved_view"})


class ReportTests(unittest.TestCase):
    def _report(self, result: ParityResult, ported: dict[str, str]) -> dict[str, Any]:
        return build_report(
            result,
            ["tests.x"],
            surface=frozenset({"get_saved_views", "create_saved_view"}),
            ported=ported,
            problems=[],
        )

    def _result(self, **postgres: int) -> ParityResult:
        result = ParityResult(io.StringIO(), False, 0)
        result.outcomes[SQLITE] = Counter(passed=3)
        result.outcomes[POSTGRES] = Counter(postgres)
        return result

    def test_counts_and_ok(self):
        result = self._result(passed=1, skipped_unported=2)
        result.ported_evidence = {"get_saved_views"}
        report = self._report(result, {"get_saved_views": "OrganizationMixin"})
        self.assertEqual((report["surface"], report["ported"], report["unported"]), (2, 1, 1))
        self.assertEqual(report["unported_methods"], ["create_saved_view"])
        self.assertEqual(report["legs"][POSTGRES]["passed"], 1)
        self.assertEqual(report["legs"][POSTGRES]["skipped_unported"], 2)
        self.assertTrue(report_ok(report))
        self.assertIn("Result: OK", format_report(report))

    def test_a_ported_method_without_a_passing_postgres_test_fails(self):
        result = self._result(skipped_unported=2)
        report = self._report(result, {"get_saved_views": "OrganizationMixin"})
        self.assertEqual(report["ported_without_passing_postgres_test"], ["get_saved_views"])
        self.assertFalse(report_ok(report))

    def test_any_failure_or_an_empty_postgres_leg_fails(self):
        self.assertFalse(report_ok(self._report(self._result(failed=1), {})))
        self.assertFalse(report_ok(self._report(self._result(errors=1), {})))
        empty = self._result()
        del empty.outcomes[POSTGRES]
        self.assertFalse(report_ok(self._report(empty, {})))

    def test_an_unsuccessful_unittest_result_fails_even_if_counters_look_clean(self):
        result = self._result(passed=1)
        result.errors.append((unittest.FunctionTestCase(lambda: None), "uncounted"))
        self.assertFalse(report_ok(self._report(result, {})))

    def test_registry_problems_fail(self):
        report = build_report(
            self._result(skipped_unported=1),
            [],
            surface=frozenset(),
            ported={},
            problems=["X.y without @ported"],
        )
        self.assertFalse(report_ok(report))
        self.assertIn("registry problems", format_report(report))


if __name__ == "__main__":
    unittest.main()
