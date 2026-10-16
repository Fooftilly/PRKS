"""JUnit reporting regression tests (no real browser or external service)."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from tests.junit_reporting import (
    JUnitTextTestResult,
    try_write_junit_report,
    write_junit_report,
)


class JUnitReportingTests(unittest.TestCase):
    def test_reports_outcomes_and_omits_sensitive_diagnostics(self):
        class Samples(unittest.TestCase):
            def test_success(self):
                self.assertTrue(True)

            def test_failure(self):
                self.fail("PRIVATE FAILURE DETAIL")

            def test_error(self):
                raise RuntimeError("PRIVATE EXCEPTION DETAIL")

            @unittest.skip("PRIVATE SKIP DETAIL")
            def test_skipped(self):
                pass

            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail("PRIVATE EXPECTED FAILURE")

            def test_subtest_failure(self):
                with self.subTest(secret="PRIVATE SUBTEST DETAIL"):
                    self.fail("PRIVATE SUBTEST FAILURE")

        result = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=JUnitTextTestResult
        ).run(unittest.defaultTestLoader.loadTestsFromTestCase(Samples))
        self.assertFalse(result.wasSuccessful())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reports" / "unit.xml"
            write_junit_report(result, path, suite_name="unit")
            document = ET.parse(path).getroot()
            self.assertEqual(document.tag, "testsuite")
            self.assertEqual(document.attrib["tests"], "6")
            self.assertEqual(document.attrib["failures"], "2")
            self.assertEqual(document.attrib["errors"], "1")
            self.assertEqual(document.attrib["skipped"], "2")
            self.assertEqual(len(document.findall("testcase")), 6)
            self.assertTrue(float(document.attrib["time"]) >= 0)
            names = {node.attrib["name"] for node in document.findall("testcase")}
            self.assertIn("test_subtest_failure", names)
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE", content)
            self.assertNotIn("Traceback", content)

    def test_class_fixture_error_is_included(self):
        class BadFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise ValueError("PRIVATE FIXTURE DETAIL")

            def test_not_run(self):
                pass

        result = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=JUnitTextTestResult
        ).run(unittest.defaultTestLoader.loadTestsFromTestCase(BadFixture))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.xml"
            write_junit_report(result, path)
            suite = ET.parse(path).getroot()
            self.assertEqual(suite.attrib["tests"], "1")
            self.assertEqual(suite.attrib["errors"], "1")
            self.assertIsNotNone(suite.find("testcase/error"))
            self.assertNotIn("PRIVATE", path.read_text(encoding="utf-8"))

    def test_subtest_skip_uses_parent_id_and_does_not_leak_parameters(self):
        class Samples(unittest.TestCase):
            def test_skipped_subtest(self):
                with self.subTest(secret="PRIVATE SUBTEST CREDENTIAL"):
                    self.skipTest("PRIVATE SKIP REASON")

        result = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=JUnitTextTestResult
        ).run(unittest.defaultTestLoader.loadTestsFromTestCase(Samples))
        self.assertTrue(result.wasSuccessful())
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "skipped.xml"
            write_junit_report(result, path)
            suite = ET.parse(path).getroot()
            self.assertEqual(suite.attrib["tests"], "1")
            self.assertEqual(suite.attrib["skipped"], "1")
            node = suite.find("testcase")
            self.assertEqual(node.attrib["name"], "test_skipped_subtest")
            self.assertNotIn("PRIVATE", path.read_text(encoding="utf-8"))

    def test_subtest_exception_is_an_error_not_a_failure(self):
        class Samples(unittest.TestCase):
            def test_skip_then_error(self):
                with self.subTest(secret="PRIVATE SKIP PARAMETER"):
                    self.skipTest("PRIVATE SKIP REASON")
                with self.subTest(secret="PRIVATE ERROR PARAMETER"):
                    raise RuntimeError("PRIVATE ERROR MESSAGE")

        result = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=JUnitTextTestResult
        ).run(unittest.defaultTestLoader.loadTestsFromTestCase(Samples))
        self.assertFalse(result.wasSuccessful())
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "error.xml"
            write_junit_report(result, path)
            suite = ET.parse(path).getroot()
            self.assertEqual(suite.attrib["tests"], "1")
            self.assertEqual(suite.attrib["errors"], "1")
            self.assertEqual(suite.attrib["failures"], "0")
            self.assertEqual(suite.attrib["skipped"], "0")
            self.assertIsNotNone(suite.find("testcase/error"))
            self.assertNotIn("PRIVATE", path.read_text(encoding="utf-8"))

    def test_optional_junit_write_failure_has_no_exception_text(self):
        sink = io.StringIO()
        with mock.patch(
            "tests.junit_reporting.write_junit_report",
            side_effect=OSError("PRIVATE FILE PATH"),
        ), mock.patch("sys.stderr", sink):
            self.assertFalse(try_write_junit_report(None, "report.xml"))
        self.assertIn("OSError", sink.getvalue())
        self.assertNotIn("PRIVATE", sink.getvalue())

    def test_unit_runner_preserves_pass_and_fail_on_report_write_error(self):
        import run_tests

        class Passing(unittest.TestCase):
            def test_ok(self):
                self.assertTrue(True)

        class Failing(unittest.TestCase):
            def test_bad(self):
                self.fail("expected synthetic failure")

        for test_class, expected_rc in ((Passing, 0), (Failing, 1)):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_class)
            with self.subTest(expected_rc=expected_rc):
                with mock.patch.dict(
                    os.environ, {"PRKS_JUNIT_REPORT": "unit.xml"}
                ), mock.patch.object(
                    run_tests, "apply_isolated_test_env"
                ), mock.patch(
                    "run_tests.unittest.TestLoader"
                ) as loader, mock.patch(
                    "tests.junit_reporting.write_junit_report",
                    side_effect=OSError("PRIVATE EXPORT FAILURE"),
                ):
                    loader.return_value.discover.return_value = suite
                    self.assertEqual(
                        run_tests.run_unit_tests("unused"), expected_rc
                    )
