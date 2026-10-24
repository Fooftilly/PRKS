#!/usr/bin/env python3
"""Run the SQLite/PostgreSQL backend parity leg and report coverage (#625, PG-3).

Runs every test module under ``tests/`` that uses ``parametrize_backends``
(``tests/backend_parity.py``) on both legs in one process, then prints:

- the facade surface (``PRKSDatabase`` methods the HTTP adapter calls) and how
  many of them are ported or unported (:mod:`backend.pg_facade.registry`);
- per leg, how many tests passed, failed, errored and were skipped, with
  ``postgres`` skips caused by unported methods counted apart;
- which unported methods skipped which number of ``postgres`` tests.

Skipped ``postgres`` tests are not parity evidence and are never counted as
passes. The run fails when any test fails or errors on either leg, when the
registry is invalid, or when a ported method was not reached by at least one
passing ``postgres`` test (a family is ported only when its parametrized tests
pass on PostgreSQL, ``docs/postgresql-architecture.md`` §13.1).

Needs ``PRKS_TEST_DATABASE_URL`` and psycopg: run it as
``scripts/test-postgres --parity``. ``--json PATH`` also writes the report as
JSON; under GitHub Actions the summary goes to the job summary too.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from collections import Counter
from collections.abc import Iterable
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

MARKER = "parametrize_backends"
OTHER = "other"


def selected_modules(project_dir: str = _PROJECT_DIR) -> list[str]:
    """``tests.test_*`` modules that opt into the parity fixture."""
    tests_dir = os.path.join(project_dir, "tests")
    found: list[str] = []
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        with open(os.path.join(tests_dir, name), encoding="utf-8") as handle:
            if f"@{MARKER}" in handle.read():
                found.append(f"tests.{name[:-3]}")
    return found


def _leg_of(test: unittest.TestCase) -> str:
    return getattr(test, "parity_backend", OTHER)


class ParityResult(unittest.TextTestResult):
    """Classifies every test by leg and outcome; keeps the postgres parity logs."""

    def __init__(self, stream: Any, descriptions: bool, verbosity: int) -> None:
        super().__init__(stream, descriptions, verbosity)
        self.outcomes: dict[str, Counter[str]] = {}
        self.unported_skips: Counter[str] = Counter()
        self.ported_evidence: set[str] = set()

    def _count(self, test: unittest.TestCase, outcome: str) -> None:
        self.outcomes.setdefault(_leg_of(test), Counter())[outcome] += 1

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        log = test.__dict__.get("_parity_log")
        if _leg_of(test) == "postgres" and log is not None and not log.ported:
            # Reached no library method at all (path helpers and the like):
            # it passes on any backend and is no parity evidence.
            self._count(test, "passed_no_library")
            return
        self._count(test, "passed")
        if _leg_of(test) == "postgres" and log is not None:
            self.ported_evidence |= log.ported

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:
        super().addFailure(test, err)
        self._count(test, "failed")

    def addError(self, test: Any, err: Any) -> None:
        super().addError(test, err)
        # setUpClass/module fixture errors arrive as _ErrorHolder, not a TestCase.
        if isinstance(test, unittest.TestCase):
            self._count(test, "errors")
        else:
            self.outcomes.setdefault(OTHER, Counter())["errors"] += 1

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        from tests.backend_parity import SKIP_PREFIX

        log = test.__dict__.get("_parity_log")
        if _leg_of(test) == "postgres" and log is not None and log.unported and reason.startswith(SKIP_PREFIX):
            self._count(test, "skipped_unported")
            self.unported_skips.update(log.unported)
        else:
            self._count(test, "skipped_other")

    def addExpectedFailure(self, test: unittest.TestCase, err: Any) -> None:
        super().addExpectedFailure(test, err)
        self._count(test, "expected_failures")

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self._count(test, "unexpected_successes")

    def addSubTest(self, test: unittest.TestCase, subtest: unittest.TestCase, err: Any) -> None:
        if err is not None:
            log = test.__dict__.get("_parity_log")
            if _leg_of(test) == "postgres" and log is not None and log.unported:
                # The refusal surfaced inside subTest(); the guard reports the
                # whole test as skipped (unported), so this is not a failure.
                return
            failed = issubclass(err[0], test.failureException)
            self._count(test, "failed" if failed else "errors")
        super().addSubTest(test, subtest, err)


OUTCOMES = (
    "passed",
    "passed_no_library",
    "failed",
    "errors",
    "skipped_unported",
    "skipped_other",
    "expected_failures",
    "unexpected_successes",
)


def build_report(
    result: ParityResult,
    modules: Iterable[str],
    *,
    surface: frozenset[str],
    ported: dict[str, str],
    problems: list[str],
) -> dict[str, Any]:
    legs = {}
    for leg in ("sqlite", "postgres", OTHER):
        counts = result.outcomes.get(leg, Counter())
        if leg == OTHER and not counts:
            continue
        row = {name: counts.get(name, 0) for name in OUTCOMES}
        row["tests"] = sum(row.values())
        legs[leg] = row
    ported_names = frozenset(ported)
    without_evidence = sorted(ported_names - result.ported_evidence)
    return {
        "modules": list(modules),
        "surface": len(surface),
        "ported": len(surface & ported_names),
        "unported": len(surface - ported_names),
        "ported_outside_surface": sorted(ported_names - surface),
        "ported_methods": {name: ported[name] for name in sorted(ported_names)},
        "unported_methods": sorted(surface - ported_names),
        "unported_skips": dict(sorted(result.unported_skips.items())),
        "ported_without_passing_postgres_test": without_evidence,
        "registry_problems": problems,
        # Backstop: any outcome the counters above missed still fails the run.
        "unittest_successful": result.wasSuccessful(),
        "legs": legs,
    }


def report_ok(report: dict[str, Any]) -> bool:
    if report["registry_problems"] or report["ported_without_passing_postgres_test"]:
        return False
    if not report["unittest_successful"]:
        return False
    for row in report["legs"].values():
        if row["failed"] or row["errors"] or row["unexpected_successes"]:
            return False
    postgres = report["legs"].get("postgres")
    return postgres is not None and postgres["tests"] > 0


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "Backend parity (SQLite + PostgreSQL), #625",
        "",
        f"Facade surface: {report['surface']} PRKSDatabase methods the backend calls on db",
        f"  ported:   {report['ported']}",
        f"  unported: {report['unported']}",
        f"  ported outside the surface: {len(report['ported_outside_surface'])}",
        "",
        "Selected modules: " + (", ".join(report["modules"]) or "(none)"),
        "",
        "| leg | tests | passed | passed (no library call) | failed | errors"
        " | skipped (unported) | skipped (other) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for leg, row in report["legs"].items():
        lines.append(
            f"| {leg} | {row['tests']} | {row['passed']} | {row['passed_no_library']}"
            f" | {row['failed']} | {row['errors']}"
            f" | {row['skipped_unported']} | {row['skipped_other']} |"
        )
    lines.append("")
    lines.extend(_detail_lines(report))
    lines.append("")
    lines.append("Result: " + ("OK" if report_ok(report) else "FAILED"))
    return "\n".join(lines)


def _detail_lines(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.append(
        "Only postgres passes that reached ported methods are parity evidence. "
        "Skipped postgres tests touched a method that is not ported yet; "
        "'no library call' passes never reached the library."
    )
    if report["unported_skips"]:
        lines.append("Unported methods that skipped postgres tests (tests skipped):")
        for name, count in report["unported_skips"].items():
            lines.append(f"  {name}: {count}")
    if report["ported_methods"]:
        lines.append("Ported methods:")
        for name, owner in report["ported_methods"].items():
            lines.append(f"  {name} ({owner})")
    if report["ported_without_passing_postgres_test"]:
        lines.append("FAIL: ported methods no passing postgres test reached:")
        lines.extend(f"  {name}" for name in report["ported_without_passing_postgres_test"])
    if report["registry_problems"]:
        lines.append("FAIL: registry problems:")
        lines.extend(f"  {problem}" for problem in report["registry_problems"])
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the SQLite/PostgreSQL backend parity leg and report coverage."
    )
    parser.add_argument("--json", metavar="PATH", help="also write the report as JSON")
    parser.add_argument("-q", "--quiet", action="store_true", help="one line per test off")
    args = parser.parse_args(argv)

    if not os.environ.get("PRKS_TEST_DATABASE_URL", "").strip():
        print(
            "run_backend_parity: PRKS_TEST_DATABASE_URL is not set; "
            "run it through scripts/test-postgres --parity",
            file=sys.stderr,
        )
        return 2

    from run_tests import apply_isolated_test_env

    apply_isolated_test_env(_PROJECT_DIR)
    from tests.backend_parity import ENV_PARITY_BACKENDS, POSTGRES, SQLITE

    # Before any selected module is imported: the decorator reads it.
    os.environ[ENV_PARITY_BACKENDS] = f"{SQLITE},{POSTGRES}"

    from backend.pg_facade import PostgresLibrary, facade_surface, ported_methods, validate_registry

    problems = validate_registry(PostgresLibrary)
    ported = {} if problems else ported_methods(PostgresLibrary)
    surface = facade_surface()

    modules = selected_modules()
    suite = unittest.TestLoader().loadTestsFromNames(modules)
    runner = unittest.TextTestRunner(
        stream=sys.stdout, verbosity=1 if args.quiet else 2, resultclass=ParityResult
    )
    result = runner.run(suite)
    assert isinstance(result, ParityResult)

    report = build_report(result, modules, surface=surface, ported=ported, problems=problems)
    text = format_report(report)
    print()
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0 if report_ok(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
