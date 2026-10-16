"""JUnit reporting regression tests (no real browser or external service)."""
import io
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from tests.junit_reporting import JUnitTextTestResult, write_junit_report


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
