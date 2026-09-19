#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import unittest
from pathlib import Path


def apply_isolated_test_env(project_dir: str) -> None:
    os.environ["PRKS_TESTING"] = "1"
    os.environ["PRKS_STORAGE"] = os.path.join(project_dir, "data_testing")
    os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
    os.environ.pop("PRKS_LOG_FILE", None)


def parse_mode(argv=None):
    """Return 'unit', 'e2e', 'all', or 'ux-tour'. Default is unit (no Chromium)."""
    parser = argparse.ArgumentParser(
        prog="run_tests.py",
        description=(
            "PRKS test runner. Default is the Python/API/structural/Node suite. "
            "Use --e2e for real Chromium against a real PRKS server, --all for both, "
            "or --ux-tour for the opt-in, artifact-producing UX Interaction Tour."
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
    group.add_argument(
        "--ux-tour",
        "-ux-tour",
        action="store_true",
        help=(
            "Run the UX Interaction Tour only (isolated temp storage; never data/). "
            "Not included in the default, --e2e, or --all runs -- it is a separate, "
            "opt-in, artifact-producing review suite. Set PRKS_UX_RECORD=1 to retain "
            "every scenario's video/trace/screenshots and produce a review ZIP."
        ),
    )
    args = parser.parse_args(argv)
    if args.e2e:
        return "e2e"
    if args.all:
        return "all"
    if args.ux_tour:
        return "ux-tour"
    return "unit"


def dependency_preflight(project_dir: str, mode: str) -> int:
    """Fail before the suite when accepted dependency pins diverge.

    unit/all: repo consistency + runtime Python pins (no Playwright required).
    e2e: runtime + Playwright pins.
    """
    from backend.dependency_gate import (
        format_gate_report,
        run_repo_gate,
        run_runtime_gate,
        run_test_gate,
    )

    root = Path(project_dir)
    if mode in ("unit", "all"):
        print("Dependency preflight (repo + runtime)...")
        repo_result = run_repo_gate(repo_root=root)
        if not repo_result.ok:
            print(format_gate_report(repo_result), file=sys.stderr)
            return 1
        runtime_result = run_runtime_gate(repo_root=root)
        if not runtime_result.ok:
            print(format_gate_report(runtime_result), file=sys.stderr)
            return 1
    if mode in ("e2e", "all"):
        print("Dependency preflight (test env: runtime + Playwright)...")
        test_result = run_test_gate(repo_root=root)
        if not test_result.ok:
            print(format_gate_report(test_result), file=sys.stderr)
            return 1
    print("Dependency preflight: OK")
    return 0


def run_unit_tests(project_dir: str) -> int:
    apply_isolated_test_env(project_dir)
    print("Discovering and running tests...")
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(project_dir, "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def run_e2e_tests(project_dir: str) -> int:
    """Run the bounded parallel full E2E gate (same contract as scripts/e2e full).

    Uses --jobs 4 unless PRKS_E2E_JOBS is already set (run.py honors the env).
    The runner itself enforces PRKS_E2E_FULL_TIMEOUT (default 1200s).
    """
    from tests.e2e.harness import python_for_subprocess

    script = os.path.join(project_dir, "tests", "e2e", "run.py")
    cmd = [python_for_subprocess(), script]
    if not os.environ.get("PRKS_E2E_JOBS"):
        cmd.extend(["--jobs", "4"])
    return subprocess.call(cmd, cwd=project_dir)


def run_ux_tour(project_dir: str) -> int:
    from tests.e2e.harness import python_for_subprocess

    script = os.path.join(project_dir, "tests", "ux_tour", "run.py")
    return subprocess.call([python_for_subprocess(), script], cwd=project_dir)


def main(argv=None) -> int:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    mode = parse_mode(sys.argv[1:] if argv is None else argv)
    if mode == "ux-tour":
        return run_ux_tour(project_dir)

    preflight_rc = dependency_preflight(project_dir, mode)
    if preflight_rc != 0:
        return preflight_rc

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
