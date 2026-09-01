#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import unittest


def apply_isolated_test_env(project_dir: str) -> None:
    os.environ["PRKS_TESTING"] = "1"
    os.environ["PRKS_STORAGE"] = os.path.join(project_dir, "data_testing")
    os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
    os.environ.pop("PRKS_LOG_FILE", None)


def parse_mode(argv=None):
    """Return 'unit', 'e2e', or 'all'. Default is unit (no Chromium)."""
    parser = argparse.ArgumentParser(
        prog="run_tests.py",
        description=(
            "PRKS test runner. Default is the Python/API/structural/Node suite. "
            "Use --e2e for real Chromium against a real PRKS server, or --all for both."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--e2e",
        "-e2e",
        action="store_true",
        help="Run real Chromium E2E only (isolated temp storage; never data/)",
    )
    group.add_argument(
        "--all",
        "-all",
        action="store_true",
        help="Run the unit suite, then E2E",
    )
    args = parser.parse_args(argv)
    if args.e2e:
        return "e2e"
    if args.all:
        return "all"
    return "unit"


def run_unit_tests(project_dir: str) -> int:
    apply_isolated_test_env(project_dir)
    print("Discovering and running tests...")
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(project_dir, "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def run_e2e_tests(project_dir: str) -> int:
    script = os.path.join(project_dir, "tests", "e2e", "run.py")
    return subprocess.call([sys.executable, script], cwd=project_dir)


def main(argv=None) -> int:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    mode = parse_mode(sys.argv[1:] if argv is None else argv)
    unit_rc = 0
    if mode in ("unit", "all"):
        unit_rc = run_unit_tests(project_dir)
        if mode == "unit":
            return unit_rc
        if unit_rc != 0:
            print("Skipping E2E because unit tests failed.", file=sys.stderr)
            return unit_rc
    return run_e2e_tests(project_dir)


if __name__ == "__main__":
    raise SystemExit(main())
