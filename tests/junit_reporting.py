"""Privacy-limited JUnit XML reporting for PRKS's unittest runners.

This intentionally exports only test identities, outcomes, and durations.
Failure traces, stdout, and exception messages stay in the GitHub Actions logs;
they are not sent to the external CI reporting service.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


class JUnitResultMixin:
    """Record completed unittest cases without changing pass/fail semantics."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.junit_cases = []
        self._junit_started = {}
        self._junit_outcomes = {}
        self._junit_fixture_errors = []

    def startTest(self, test):
        self._junit_started[test] = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        started = self._junit_started.pop(test, None)
        elapsed = max(0.0, time.perf_counter() - started) if started is not None else 0.0
        self.junit_cases.append(
            (test.id(), self._junit_outcomes.pop(test, "success"), elapsed)
        )

    def _record(self, test, outcome):
        # unittest passes _SubTest to addSkip; its id() contains arbitrary
        # subTest parameter reprs. Use the parent test ID exclusively.
        parent = getattr(test, "test_case", test)
        if parent in self._junit_started:
            priority = {"success": 0, "skipped": 1, "failure": 2, "error": 3}
            previous = self._junit_outcomes.get(parent, "success")
            if priority[outcome] > priority[previous]:
                self._junit_outcomes[parent] = outcome
        else:
            # setUpClass/tearDownClass _ErrorHolder never calls startTest.
            # Do not serialize a nested subtest ID even on unexpected paths.
            self._junit_fixture_errors.append((parent.id(), outcome, 0.0))

    def addError(self, test, err):
        self._record(test, "error")
        super().addError(test, err)

    def addFailure(self, test, err):
        self._record(test, "failure")
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        self._record(test, "skipped")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._record(test, "skipped")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._record(test, "failure")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            kind = "failure" if issubclass(err[0], test.failureException) else "error"
            self._record(test, kind)
        super().addSubTest(test, subtest, err)


class JUnitTextTestResult(JUnitResultMixin, unittest.TextTestResult):
    """TextTestResult with an additional opt-in JUnit export."""


def write_junit_report(result, path, *, suite_name="PRKS"):
    """Write one JUnit suite. Never include traces, logs, or runtime data."""
    cases = list(result.junit_cases) + list(result._junit_fixture_errors)
    counts = {outcome: sum(status == outcome for _, status, _ in cases)
              for outcome in ("failure", "error", "skipped")}
    root = ET.Element(
        "testsuite",
        name=suite_name,
        tests=str(len(cases)),
        failures=str(counts["failure"]),
        errors=str(counts["error"]),
        skipped=str(counts["skipped"]),
        time=f"{sum(duration for _, _, duration in cases):.3f}",
    )
    for identifier, status, duration in cases:
        classname, _, name = identifier.rpartition(".")
        node = ET.SubElement(
            root,
            "testcase",
            classname=classname,
            name=name or identifier,
            time=f"{duration:.3f}",
        )
        if status != "success":
            ET.SubElement(node, status, message=f"Test {status}; see CI logs")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(target, encoding="utf-8", xml_declaration=True)


def try_write_junit_report(result, path, *, suite_name="PRKS") -> bool:
    """Best-effort telemetry: a reporter fault must never change test status."""
    try:
        write_junit_report(result, path, suite_name=suite_name)
    except Exception as exc:  # noqa: BLE001 - optional external reporting
        # Do not log exception text: paths and other diagnostics may be private.
        print(
            f"warning: optional JUnit report unavailable ({type(exc).__name__})",
            file=sys.stderr,
        )
        return False
    return True
