#!/usr/bin/env python3
"""Run real Chromium E2E against isolated temporary PRKS storage.

Never targets data/ or a live PRKS_STORAGE tree. Installs Chromium into
.playwright-browsers/ when this Playwright revision is missing. Fails if the
Playwright package is missing instead of reporting SKIP.

`--jobs 1` (the default) runs the suite in this process in the historical
order and is the mode to reach for when debugging. `--jobs N` shards individual
test IDs across N worker processes; each worker owns its own Playwright,
Chromium, PRKS subprocesses and port window, so per-test isolation is unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ["PRKS_E2E"] = "1"

from tests.e2e.harness import (
    apply_e2e_playwright_env,
    finish_test_profile,
    python_for_subprocess,
    start_test_profile,
    stop_all_servers,
)
from tests.e2e.install_browser import ensure_chromium_installed
from tests.e2e.policy import (
    LAST_FAILED_PATH,
    PROFILE_ENV,
    SEED_CACHE_ENV,
    ChangeDiscoveryError,
    benchmark_modes,
    format_feature_catalog,
    full_gate_timeout_s,
    list_changed_paths,
    load_last_failed,
    merge_last_failed,
    report_banner,
    save_last_failed,
    select_affected,
    select_features,
    select_smoke,
)
from tests.e2e.sharding import (
    BASELINE_TIMINGS_PATH,
    TIMINGS_PATH,
    agent_default_jobs,
    agent_resource_limits,
    aggregate_worker_results,
    assign_shards,
    format_slowest,
    load_timings,
    merge_timing_sources,
    merge_timings,
    parse_jobs,
    run_exit_code,
    save_timings,
    shard_estimates,
    worker_port_range,
)

# Bare test.id() lines printed by _TimingResult.startTest (not diag/unittest chatter).
_E2E_TEST_ID_LINE = re.compile(
    r"^tests\.e2e\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.test_[A-Za-z0-9_]+$"
)

E2E_MODULES = (
    "tests.e2e.test_app",
    "tests.e2e.test_offline",
    "tests.e2e.test_browse_offline",
    "tests.e2e.test_work_browsing",
    "tests.e2e.test_local_store_durability",
    "tests.e2e.test_folders_offline",
    "tests.e2e.test_folders_durable",
    "tests.e2e.test_library_nav_folders",
    "tests.e2e.test_person_groups_offline",
    "tests.e2e.test_person_groups_durable",
    "tests.e2e.test_playlists_offline",
    "tests.e2e.test_playlists_durable",
    "tests.e2e.test_concepts_durable",
    "tests.e2e.test_positions_durable",
    "tests.e2e.test_arguments_durable",
    "tests.e2e.test_research_graph_offline",
    "tests.e2e.test_work_tags_offline",
    "tests.e2e.test_work_opens_offline",
    "tests.e2e.test_work_metadata_offline",
    "tests.e2e.test_work_source_offline",
    "tests.e2e.test_work_notes_offline",
    "tests.e2e.test_work_people_offline",
    "tests.e2e.test_person_create_offline",
    "tests.e2e.test_person_edit_offline",
    "tests.e2e.test_modal_lifecycle",
)

SLOWEST_LIMIT = 25
WORKER_POLL_S = 0.25
WORKER_STOP_TIMEOUT_S = 20.0
# Child of the full-gate deadline supervisor (Windows + POSIX).
FULL_GATE_CHILD_ENV = "PRKS_E2E_FULL_GATE_CHILD"


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort terminate a supervised child and its descendants."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=WORKER_STOP_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=WORKER_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError, AttributeError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=WORKER_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError, AttributeError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=WORKER_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pass


def _run_with_deadline(cmd, timeout_s: int, *, env=None, cwd=None) -> int:
    """Run cmd under a wall-clock deadline; return exit code or 124 on timeout.

    Cross-platform: uses subprocess wait(timeout=…) plus process-tree kill.
    Covers any work the child performs (E2E shards, pointer capture, etc.).

    The supervised child is started in its own process group/session so an
    external `timeout(1)` / Ctrl+C targeting only this supervisor must not
    orphan workers or Chromium. Any abnormal supervisor exit (deadline,
    KeyboardInterrupt, SIGINT/SIGTERM) terminates the child tree before the
    supervisor returns or dies. Normal completion still returns the child's
    real exit code; deadline expiration remains 124.
    """
    if timeout_s <= 0:
        completed = subprocess.run(cmd, cwd=cwd, env=env)
        return completed.returncode

    popen_kwargs = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, cwd=cwd, env=env, **popen_kwargs)
    previous_handlers = {}

    def _on_signal(signum, frame):
        # Tear down the separate-session child before this process exits so an
        # outer `timeout 1200` SIGTERM (or Ctrl+C) cannot leave it orphaned.
        _terminate_process_tree(proc)
        prior = previous_handlers.get(signum, signal.SIG_DFL)
        try:
            signal.signal(signum, prior)
        except (OSError, ValueError, AttributeError):
            pass
        if signum == getattr(signal, "SIGINT", None):
            raise KeyboardInterrupt
        try:
            signal.signal(signum, signal.SIG_DFL)
        except (OSError, ValueError, AttributeError):
            pass
        try:
            os.kill(os.getpid(), signum)
        except OSError:
            raise SystemExit(128 + int(signum))

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            previous_handlers[sig] = signal.signal(sig, _on_signal)
        except (OSError, ValueError, AttributeError):
            # Unsupported on this platform/thread — finally still cleans up.
            pass

    try:
        try:
            return proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            print(
                "full E2E gate exceeded %ds hard limit" % timeout_s,
                file=sys.stderr,
            )
            _terminate_process_tree(proc)
            print("E2E FAIL (full) — hard timeout", file=sys.stderr)
            return 124
        except KeyboardInterrupt:
            _terminate_process_tree(proc)
            raise
    finally:
        # Unconditional: leave no supervised orphan on any abnormal exit path.
        if proc.poll() is None:
            _terminate_process_tree(proc)
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError, AttributeError):
                pass


def _supervise_full_gate(argv, timeout_s: int) -> int:
    """Re-exec this runner as a child and enforce one wall-clock deadline.

    The child runs chromium install, serial/parallel E2E, last-failed merge,
    and pointer_capture under the same PRKS_E2E_FULL_TIMEOUT budget. Works on
    Windows and POSIX via subprocess deadline supervision.
    """
    env = os.environ.copy()
    env[FULL_GATE_CHILD_ENV] = "1"
    cmd = [
        python_for_subprocess(),
        str(REPO / "tests" / "e2e" / "run.py"),
        *(argv if argv is not None else []),
    ]
    print(
        "Full-gate hard limit: %ds (PRKS_E2E_FULL_TIMEOUT; 0 disables)"
        % timeout_s
    )
    return _run_with_deadline(cmd, timeout_s, env=env, cwd=str(REPO))


def _executed_ids_from_observation(observed, failed_ids) -> list:
    """IDs that actually completed this run (timings + any reported failures)."""
    executed = list(observed or ())
    seen = set(executed)
    for tid in failed_ids or ():
        if tid not in seen:
            executed.append(tid)
            seen.add(tid)
    return executed


class _TimingResult(unittest.TextTestResult):
    """TextTestResult that also records wall-clock duration per test ID."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = {}
        self.phase_timings = {}
        self._started_at = None

    def startTest(self, test):
        self._started_at = time.perf_counter()
        start_test_profile(test.id())
        # Workers redirect stdout to their log file; print the id so a hung
        # shard names the test it never left (TextTestRunner stream is StringIO).
        print(test.id(), flush=True)
        try:
            from tests.e2e.harness import e2e_diag

            e2e_diag("START", test.id())
        except Exception:
            pass
        super().startTest(test)

    def stopTest(self, test):
        try:
            from tests.e2e.harness import e2e_diag

            e2e_diag("STOP", test.id())
        except Exception:
            pass
        super().stopTest(test)
        if self._started_at is not None:
            self.timings[test.id()] = time.perf_counter() - self._started_at
            self._started_at = None
        phases = finish_test_profile(test.id())
        if phases:
            self.phase_timings[test.id()] = phases


def _result_factory(stream, descriptions, verbosity):
    return _TimingResult(stream, descriptions, verbosity)


def discover_test_ids(modules=E2E_MODULES):
    """Every individual unittest test ID in the E2E suite, in load order."""
    loader = unittest.TestLoader()
    ids = []
    for name in modules:
        _flatten(loader.loadTestsFromName(name), ids)
    return ids


def _flatten(suite, out):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            _flatten(item, out)
        elif isinstance(item, unittest.TestCase):
            out.append(item.id())
        else:  # pragma: no cover - _FailedTest and friends
            out.append(str(item))


def _suite_for(test_ids):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test_id in test_ids:
        suite.addTests(loader.loadTestsFromName(test_id))
    return suite


def _run_pointer_capture() -> int:
    env = os.environ.copy()
    env["PRKS_E2E"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(apply_e2e_playwright_env())
    env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    proc = subprocess.run(
        [python_for_subprocess(), str(REPO / "tests" / "browser" / "pointer_capture.py")],
        cwd=str(REPO),
        env=env,
    )
    return proc.returncode


def _print_slowest(timings, limit=SLOWEST_LIMIT):
    lines = format_slowest(timings, limit)
    if not lines:
        return
    print("")
    print("Slowest E2E tests:")
    print("")
    for line in lines:
        print(line)


def _persist_timings(observed, known_ids):
    path = REPO / TIMINGS_PATH
    merged = merge_timings(load_timings(path), observed, known_ids)
    if merged:
        save_timings(path, merged)


def _apply_runtime_modes(args) -> tuple:
    """Export CLI benchmark flags, then decide the effective benchmark modes once.

    ``--profile`` / ``--no-seed-cache`` configure the harness through
    PRKS_E2E_PROFILE / PRKS_E2E_SEED_CACHE, and those variables are equally
    supported on their own. Exporting first and asking the environment after
    keeps one canonical decision for both entry paths, so an env-only benchmark
    run cannot train ``.tests/e2e-timings.json`` (poisoning later shard
    balancing) or mutate last-failed state that ordinary runs rely on.
    """
    if args.profile:
        os.environ[PROFILE_ENV] = "1"
    if args.no_seed_cache:
        os.environ[SEED_CACHE_ENV] = "0"
    return benchmark_modes(os.environ)


# --- worker mode ---------------------------------------------------------


def _install_shutdown_handlers(index: int) -> None:
    """Tear down PRKS subprocesses when the parent terminates this worker.

    A signal does not run unittest cleanups, so without this a --fail-fast
    stop (or a Ctrl-C reaching the group) would leave the shard's live PRKS
    server and its TemporaryDirectory behind.
    """

    def _shutdown(signum, _frame):
        stopped = stop_all_servers()
        print(
            "[worker %d] signal %d: stopped %d server(s)" % (index, signum, stopped),
            file=sys.stderr,
            flush=True,
        )
        os._exit(1)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):  # pragma: no cover - non-main thread
            pass

    # A parent that is SIGKILLed cannot signal anyone, so the shard would run
    # on unsupervised with a live PRKS server. Re-parenting is the observable
    # symptom; poll for it cheaply.
    original_ppid = os.getppid()

    def _watch_parent():
        while True:
            time.sleep(1.0)
            if os.getppid() != original_ppid:
                stopped = stop_all_servers()
                print(
                    "[worker %d] parent exited: stopped %d server(s)" % (index, stopped),
                    file=sys.stderr,
                    flush=True,
                )
                os._exit(1)

    threading.Thread(target=_watch_parent, daemon=True).start()


def run_worker(index: int, jobs: int, tests_file: str, report_file: str) -> int:
    """Execute one shard and write a machine-readable report. Never raises."""
    _install_shutdown_handlers(index)
    with open(tests_file, encoding="utf-8") as handle:
        test_ids = json.load(handle)
    stream = StringIO()
    report = {
        "index": index,
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "failed_ids": [],
        "timings": {},
        "phase_timings": {},
        "output": "",
        "detail": "",
    }
    rc = 1
    try:
        apply_e2e_playwright_env()
        started = time.perf_counter()
        runner = unittest.TextTestRunner(
            stream=stream, verbosity=2, resultclass=_result_factory
        )
        result = runner.run(_suite_for(test_ids))
        report["duration"] = time.perf_counter() - started
        report["tests"] = result.testsRun
        report["failures"] = len(result.failures)
        report["errors"] = len(result.errors)
        report["skipped"] = len(result.skipped)
        report["failed_ids"] = _failed_ids_from_result(result)
        report["timings"] = {k: round(v, 3) for k, v in result.timings.items()}
        report["phase_timings"] = result.phase_timings
        report["detail"] = _failure_detail(result)
        rc = 0 if result.wasSuccessful() else 1
    except BaseException as exc:  # noqa: BLE001 - must still report, then re-raise nothing
        report["errors"] = (report["errors"] or 0) + 1
        report["detail"] = "worker %d raised %s: %s" % (index, type(exc).__name__, exc)
        rc = 1
    finally:
        report["output"] = stream.getvalue()
        try:
            with open(report_file, "w", encoding="utf-8") as handle:
                json.dump(report, handle)
        except OSError:
            rc = 1
    return rc


def _failure_detail(result) -> str:
    chunks = []
    for label, group in (("FAIL", result.failures), ("ERROR", result.errors)):
        for test, trace in group:
            chunks.append("%s: %s\n%s" % (label, test.id(), trace))
    return "\n".join(chunks)


def _failed_ids_from_result(result) -> list:
    ids = []
    for group in (result.failures, result.errors):
        for test, _trace in group:
            ids.append(test.id())
    return ids


# --- parallel parent -----------------------------------------------------


def _spawn_worker(index, jobs, shard, workdir, browsers_path):
    tests_file = workdir / ("shard-%d.json" % index)
    report_file = workdir / ("report-%d.json" % index)
    log_file = workdir / ("worker-%d.log" % index)
    tests_file.write_text(json.dumps(shard), encoding="utf-8")
    base, end = worker_port_range(index, jobs)
    span = end - base + 1
    env = os.environ.copy()
    env["PRKS_E2E"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    env["PRKS_E2E_PORT_BASE"] = str(base)
    env["PRKS_E2E_PORT_SPAN"] = str(span)
    env["PRKS_E2E_WORKER"] = str(index)
    handle = open(log_file, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            python_for_subprocess(),
            str(REPO / "tests" / "e2e" / "run.py"),
            "--worker-index",
            str(index),
            "--worker-count",
            str(jobs),
            "--tests-file",
            str(tests_file),
            "--report-file",
            str(report_file),
        ],
        cwd=str(REPO),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return {
        "index": index,
        "proc": proc,
        "handle": handle,
        "report_file": report_file,
        "log_file": log_file,
        "shard": shard,
        "started": time.perf_counter(),
    }


def _stop_worker(worker):
    proc = worker["proc"]
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=WORKER_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=WORKER_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pass


def _read_report(worker):
    try:
        with open(worker["report_file"], encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _tail(path, limit=4000):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()[-limit:]
    except OSError:
        return ""


def _last_started_test(log_file) -> str:
    """Last test id a worker printed before hanging (see _TimingResult.startTest).

    ``startTest`` prints a bare ``test.id()`` line. Diagnostic / recycle /
    unittest chatter after that must not steal attribution — only lines that
    look like E2E test ids count.
    """
    last = ""
    try:
        with open(log_file, encoding="utf-8", errors="replace") as handle:
            for ln in handle:
                s = ln.strip()
                if _E2E_TEST_ID_LINE.match(s):
                    last = s
    except OSError:
        return ""
    return last


def _print_hung_worker(worker, jobs):
    """Name the in-flight test when a shard dies without a report document."""
    last = _last_started_test(worker["log_file"])
    print("", file=sys.stderr)
    print("--- Worker %d/%d hung / no report ---" % (worker["index"] + 1, jobs), file=sys.stderr)
    if last:
        print("last started test: %s" % last, file=sys.stderr)
    else:
        print("last started test: (worker log empty or unreadable)", file=sys.stderr)
    sys.stderr.flush()


def run_parallel(test_ids, jobs, timings, fail_fast) -> tuple[bool, dict, list, dict]:
    buckets = assign_shards(test_ids, jobs, timings)
    estimates = shard_estimates(buckets, timings)
    browsers_path = apply_e2e_playwright_env()
    observed = {}
    phase_timings = {}
    reports = []
    failed_ids = []
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="prks-e2e-jobs-") as raw:
        workdir = Path(raw)
        workers = []

        def _shutdown(signum, _frame):
            # A signal does not run the finally below, so stop the shards here
            # rather than leaving four Chromiums and four PRKS servers behind.
            # Print in-flight test ids first — the temp workdir is deleted when
            # this with-block unwinds, and hard-timeout SIGTERM is the common
            # path for a single hung shard after its peer finished cleanly.
            for worker in workers:
                if worker["proc"].poll() is None:
                    _print_hung_worker(worker, jobs)
                _stop_worker(worker)
            print("[E2E] terminated by signal %d" % signum, file=sys.stderr, flush=True)
            raise KeyboardInterrupt

        previous_handlers = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                previous_handlers[sig] = signal.signal(sig, _shutdown)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass
        try:
            for index, shard in enumerate(buckets):
                if not shard:
                    print("[E2E %d/%d] no tests assigned" % (index + 1, jobs))
                    continue
                print(
                    "[E2E %d/%d] running %d tests (estimated %.0fs)"
                    % (index + 1, jobs, len(shard), estimates[index])
                )
                workers.append(_spawn_worker(index, jobs, shard, workdir, browsers_path))
            sys.stdout.flush()

            pending = list(workers)
            stopped_early = False
            while pending:
                time.sleep(WORKER_POLL_S)
                for worker in list(pending):
                    if worker["proc"].poll() is None:
                        continue
                    pending.remove(worker)
                    elapsed = time.perf_counter() - worker["started"]
                    rc = worker["proc"].returncode
                    report = _read_report(worker)
                    entry = {
                        "index": worker["index"],
                        "returncode": rc,
                        "reported": report is not None,
                        "tests": (report or {}).get("tests", 0),
                        "failures": (report or {}).get("failures", 0),
                        "errors": (report or {}).get("errors", 0),
                        "skipped": (report or {}).get("skipped", 0),
                    }
                    reports.append(entry)
                    if report:
                        observed.update(report.get("timings") or {})
                        phase_timings.update(report.get("phase_timings") or {})
                        for fid in report.get("failed_ids") or []:
                            if fid not in failed_ids:
                                failed_ids.append(fid)
                    ok = report is not None and rc == 0
                    print(
                        "[E2E %d/%d] %s — %.1fs (%d tests)"
                        % (
                            worker["index"] + 1,
                            jobs,
                            "PASS" if ok else "FAIL",
                            elapsed,
                            entry["tests"],
                        )
                    )
                    sys.stdout.flush()
                    if not ok:
                        _print_worker_failure(worker, jobs, report)
                        if fail_fast:
                            stopped_early = True
                            print(
                                "[E2E] --fail-fast: stopping %d remaining worker(s)"
                                % len(pending)
                            )
                            for other in pending:
                                _stop_worker(other)
                                reports.append(
                                    {
                                        "index": other["index"],
                                        "returncode": other["proc"].returncode,
                                        "reported": False,
                                        "tests": 0,
                                        "failures": 0,
                                        "errors": 0,
                                        "skipped": 0,
                                        "cancelled": True,
                                    }
                                )
                            pending = []
                            break
                if stopped_early:
                    break
        finally:
            for sig, handler in previous_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass
            for worker in workers:
                _stop_worker(worker)
                try:
                    worker["handle"].close()
                except OSError:
                    pass

    ok, totals, problems = aggregate_worker_results(
        [r for r in reports if not r.get("cancelled")]
    )
    if any(r.get("cancelled") for r in reports):
        ok = False
    wall = time.perf_counter() - started
    print("")
    print(
        "E2E workers=%d tests=%d failures=%d errors=%d skipped=%d in %.1fs"
        % (jobs, totals["tests"], totals["failures"], totals["errors"], totals["skipped"], wall)
    )
    for problem in problems:
        print("  %s" % problem, file=sys.stderr)
    return ok, observed, failed_ids, phase_timings


def _print_worker_failure(worker, jobs, report):
    index = worker["index"]
    print("", file=sys.stderr)
    print("--- Worker %d/%d failures ---" % (index + 1, jobs), file=sys.stderr)
    if report is None:
        print(
            "worker produced no result document; exit code %s"
            % worker["proc"].returncode,
            file=sys.stderr,
        )
        last = _last_started_test(worker["log_file"])
        if last:
            print("last started test: %s" % last, file=sys.stderr)
        print("assigned tests: %s" % ", ".join(worker["shard"][:20]), file=sys.stderr)
        if len(worker["shard"]) > 20:
            print("  (+%d more)" % (len(worker["shard"]) - 20), file=sys.stderr)
        print(_tail(worker["log_file"]), file=sys.stderr)
        return
    detail = report.get("detail") or ""
    if detail:
        print(detail, file=sys.stderr)
    else:
        print(report.get("output") or _tail(worker["log_file"]), file=sys.stderr)
    sys.stderr.flush()


# --- serial parent -------------------------------------------------------


def run_serial(test_ids, fail_fast) -> tuple[bool, dict, list, dict]:
    runner = unittest.TextTestRunner(
        verbosity=2, failfast=fail_fast, resultclass=_result_factory
    )
    started = time.perf_counter()
    try:
        result = runner.run(_suite_for(test_ids))
    except KeyboardInterrupt:
        stop_all_servers()
        raise
    wall = time.perf_counter() - started
    print("")
    print(
        "E2E workers=1 tests=%d failures=%d errors=%d skipped=%d in %.1fs"
        % (result.testsRun, len(result.failures), len(result.errors), len(result.skipped), wall)
    )
    return (
        result.wasSuccessful(),
        {k: round(v, 3) for k, v in result.timings.items()},
        _failed_ids_from_result(result),
        result.phase_timings,
    )


# --- CLI -----------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tests/e2e/run.py",
        description=(
            "Real Chromium E2E against isolated temporary PRKS storage. "
            "Default is the full suite on one worker (deterministic). "
            "Use --smoke / --feature / --affected / --last-failed / --dev / --agent "
            "for targeted development feedback. "
            "--jobs N shards individual test IDs across N processes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tests/e2e/run.py --smoke --jobs 2\n"
            "  python tests/e2e/run.py --feature graph --jobs 2 --no-pointer-capture\n"
            "  python tests/e2e/run.py --dev --feature tabs\n"
            "  python tests/e2e/run.py --affected\n"
            "  python tests/e2e/run.py --affected --base origin/master\n"
            "  python tests/e2e/run.py --last-failed\n"
            "  python tests/e2e/run.py --jobs 4   # full regression gate "
            "(runner hard-limits at 1200s)\n"
            "  python tests/e2e/run.py --jobs 1 tests.e2e.test_app.AppShellAndNavigationTests\n"
        ),
    )
    parser.add_argument(
        "tests",
        nargs="*",
        help="Optional unittest test IDs, classes or modules to run instead of the full suite.",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        default=None,
        help="Worker processes (default 1, or $PRKS_E2E_JOBS). The flag wins over the env var.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first failure; with --jobs N, terminate the remaining workers.",
    )
    parser.add_argument(
        "--no-pointer-capture",
        action="store_true",
        help="Skip the pointer-capture checks (they otherwise run once, after the workers).",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run the curated smoke suite (small essential shell + critical workflows).",
    )
    parser.add_argument(
        "--feature",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Run one feature/domain group (repeatable). "
            "Use --list-features for the catalog. Alias: pass 'smoke' as a feature name."
        ),
    )
    parser.add_argument(
        "--affected",
        action="store_true",
        help=(
            "Select feature groups from the git working-tree diff "
            "(default vs HEAD; override with --base)."
        ),
    )
    parser.add_argument(
        "--base",
        default=None,
        metavar="REF",
        help="Git ref for --affected comparison (default: HEAD). Example: origin/master",
    )
    parser.add_argument(
        "--last-failed",
        action="store_true",
        help="Rerun only tests that failed in the previous E2E run (.tests/e2e-last-failed.json).",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help=(
            "Developer mode: --fail-fast + --no-pointer-capture. "
            "Requires an explicit selection (--feature/--smoke/--affected/--last-failed "
            "or positional tests)."
        ),
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help=(
            "Cloud-agent mode: --fail-fast + --no-pointer-capture plus a conservative "
            "resource-aware default worker count (max 2). Requires an explicit selection."
        ),
    )
    parser.add_argument(
        "--list-tests",
        action="store_true",
        help="Print the discovered/selected test IDs and exit.",
    )
    parser.add_argument(
        "--list-features",
        action="store_true",
        help="Print the E2E feature-group catalog and exit.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help=(
            "Measure per-test E2E infrastructure phases (seed build/clone, server startup, "
            "browser context, app readiness, async waits, request routing, shutdown). "
            "Does not update .tests/e2e-timings.json or last-failed history "
            "(same for PRKS_E2E_PROFILE=1 without this flag)."
        ),
    )
    parser.add_argument(
        "--no-seed-cache",
        action="store_true",
        help=(
            "Disable worker-local immutable fixture seed snapshots for A/B benchmarking. "
            "Does not update .tests/e2e-timings.json or last-failed history "
            "(same for PRKS_E2E_SEED_CACHE=0 without this flag)."
        ),
    )
    # Internal: how the parent invokes one shard.
    parser.add_argument("--worker-index", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-count", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--tests-file", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--report-file", default=None, help=argparse.SUPPRESS)
    return parser


def _print_affected_plan(plan):
    print("Affected E2E plan:")
    print("  comparison: working tree (+ untracked production/E2E paths) vs base")
    for decision in plan["decisions"]:
        if decision["skip"]:
            print(
                "  skip  %-40s rule=%s%s"
                % (
                    decision["path"],
                    decision["rule"],
                    (" — " + decision["note"]) if decision["note"] else "",
                )
            )
        else:
            print(
                "  take  %-40s rule=%s features=%s%s"
                % (
                    decision["path"],
                    decision["rule"],
                    ",".join(decision["features"]) or "-",
                    (" — " + decision["note"]) if decision["note"] else "",
                )
            )
    if plan["features"]:
        print("  selected features: %s" % ", ".join(plan["features"]))
        print("  selected tests: %d" % len(plan["test_ids"]))
    else:
        print("  selected features: (none)")
        if plan.get("empty_reason"):
            print("  reason: %s" % plan["empty_reason"])


def _resolve_selection(args, all_ids):
    """Return (tier, test_ids, selection_note)."""
    selection_modes = sum(
        1
        for flag in (
            bool(args.smoke),
            bool(args.feature),
            bool(args.affected),
            bool(args.last_failed),
            bool(args.tests),
        )
        if flag
    )
    if selection_modes > 1:
        raise ValueError(
            "use only one of: positional tests, --smoke, --feature, --affected, --last-failed"
        )

    if args.last_failed:
        data = load_last_failed(REPO / LAST_FAILED_PATH)
        if not data or not data.get("test_ids"):
            raise ValueError(
                "no last-failed state at %s — run E2E once and let it fail first"
                % (REPO / LAST_FAILED_PATH)
            )
        known = set(all_ids)
        ids = [tid for tid in data["test_ids"] if tid in known]
        missing = [tid for tid in data["test_ids"] if tid not in known]
        note = "from %s (%d id(s))" % (LAST_FAILED_PATH, len(data["test_ids"]))
        if missing:
            note += "; dropped %d renamed/removed" % len(missing)
        if not ids:
            # Every persisted failure was renamed/removed — prune is a clean state,
            # not a permanent zero-selection error (merge_last_failed never runs).
            return "last-failed-stale", [], note
        return "last-failed", ids, note

    if args.affected:
        changed = list_changed_paths(REPO, base=args.base)
        print(
            "Changed paths vs %s (%d):"
            % (args.base or "HEAD", len(changed))
        )
        if not changed:
            print("  (none)")
        else:
            for path in changed:
                print("  %s" % path)
        plan = select_affected(all_ids, changed)
        _print_affected_plan(plan)
        note = "features=%s" % (",".join(plan["features"]) or "-")
        if plan.get("noop_ok") and not plan["test_ids"]:
            # Docs/unit/ignored-only (or empty) diffs are a successful no-op.
            return "affected-noop", [], note
        return "affected", plan["test_ids"], note

    if args.smoke:
        ids = select_smoke(all_ids)
        return "smoke", ids, "%d curated smoke tests" % len(ids)

    if args.feature:
        names = []
        for item in args.feature:
            for part in item.split(","):
                part = part.strip()
                if part:
                    names.append(part)
        ids = select_features(all_ids, names)
        return "feature", ids, "groups=%s" % ",".join(names)

    if args.tests:
        test_ids = []
        loader = unittest.TestLoader()
        for name in args.tests:
            _flatten(loader.loadTestsFromName(name), test_ids)
        return "targeted", test_ids, "explicit=%s" % " ".join(args.tests)

    return "full", list(all_ids), "complete suite"


def main(argv=None) -> int:
    """Entry point; runtime-mode env exports stay scoped to this invocation.

    _apply_runtime_modes() publishes the CLI flags through the environment so
    workers and the harness see them, which would otherwise leak into a later
    in-process main() and silently suppress that run's history persistence.
    """
    saved_modes = {name: os.environ.get(name) for name in (PROFILE_ENV, SEED_CACHE_ENV)}
    try:
        return _main(argv)
    finally:
        for name, value in saved_modes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _main(argv=None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    active_benchmark_modes = _apply_runtime_modes(args)

    if args.worker_index is not None:
        return run_worker(
            args.worker_index, args.worker_count or 1, args.tests_file, args.report_file
        )

    if args.list_features:
        print(format_feature_catalog())
        return 0

    # Resolve selection before Chromium install so docs/unit --affected and
    # --list-tests are cheap no-ops (no browser download).
    apply_e2e_playwright_env()
    all_ids = discover_test_ids()
    try:
        tier, test_ids, note = _resolve_selection(args, all_ids)
    except ChangeDiscoveryError as exc:
        # Fail closed: an unusable Git comparison is not "nothing changed".
        print("affected: %s" % exc, file=sys.stderr)
        print(
            "refusing to report zero affected tests from failed change discovery; "
            "check --base/the checkout, or select tests explicitly with "
            "--smoke / --feature.",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dev or args.agent:
        if tier == "full":
            flag = "--agent" if args.agent else "--dev"
            print(
                "%s refuses the full suite; pass --smoke, --feature, --affected, "
                "--last-failed, or positional tests" % flag,
                file=sys.stderr,
            )
            return 2
        if tier not in ("affected-noop", "last-failed-stale"):
            args.fail_fast = True
            args.no_pointer_capture = True
            tier = "agent" if args.agent else "dev"
            note = (note + "; fail-fast") if note else "fail-fast"

    if args.list_tests:
        for test_id in test_ids:
            print(test_id)
        return 0

    if tier == "affected-noop":
        print(
            "affected: no E2E-relevant changes (docs/unit/ignored only) — success no-op"
        )
        return 0

    if tier == "last-failed-stale":
        if active_benchmark_modes:
            # Clearing the file is a history mutation like any other.
            print(
                "last-failed: every persisted failure is stale, but benchmark mode "
                "(%s) leaves the state untouched" % ",".join(active_benchmark_modes)
            )
            return 0
        last_failed_path = REPO / LAST_FAILED_PATH
        cleared = True
        if last_failed_path.is_file():
            try:
                last_failed_path.unlink()
            except OSError:
                cleared = False
                print(
                    "warning: could not unlink stale last-failed state",
                    file=sys.stderr,
                )
        if cleared:
            print(
                "last-failed: no known unresolved failures remain — cleared stale state"
            )
        return 0

    if not test_ids:
        print("no E2E tests selected", file=sys.stderr)
        if tier == "affected":
            print(
                "hint: mapped features selected zero tests, or the selection is broken. "
                "Use --smoke or --feature explicitly if you still want a browser run.",
                file=sys.stderr,
            )
        return 1

    # Full gate: one wall-clock deadline covers chromium install, shards, and
    # pointer_capture. Parent re-execs as a supervised child (Windows + POSIX).
    if tier == "full":
        timeout_s = full_gate_timeout_s()
        if timeout_s > 0 and not os.environ.get(FULL_GATE_CHILD_ENV):
            return _supervise_full_gate(
                argv if argv is not None else sys.argv[1:], timeout_s
            )

    try:
        ensure_chromium_installed()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    apply_e2e_playwright_env()

    print(report_banner(tier, len(test_ids), note))
    if tier != "full":
        print(
            "NOTE: a PASS here is %s coverage — not equivalent to the full E2E gate."
            % tier
        )

    try:
        # Serial-by-default so debugging stays deterministic. Cloud-agent mode
        # chooses a conservative width from effective cgroup CPU/memory limits;
        # explicit --jobs / PRKS_E2E_JOBS still wins.
        default_jobs = agent_default_jobs() if args.agent else 1
        jobs = parse_jobs(args.jobs, os.environ.get("PRKS_E2E_JOBS"), default=default_jobs)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    jobs = min(jobs, len(test_ids))
    if args.agent:
        limits = agent_resource_limits()
        mem = limits["memory_limit_bytes"]
        mem_text = "unlimited/unknown" if mem is None else "%.1f GiB" % (mem / (1024 ** 3))
        print(
            "Agent resources: cpu=%s memory=%s workers=%d"
            % (limits["cpu_count"], mem_text, jobs)
        )

    baseline_timings = load_timings(REPO / BASELINE_TIMINGS_PATH)
    local_timings = load_timings(REPO / TIMINGS_PATH)
    timings = merge_timing_sources(baseline_timings, local_timings)
    if jobs == 1:
        ok, observed, failed_ids, phase_timings = run_serial(test_ids, args.fail_fast)
    else:
        ok, observed, failed_ids, phase_timings = run_parallel(
            test_ids, jobs, timings, args.fail_fast
        )

    if "profile" in active_benchmark_modes and phase_timings:
        totals = {}
        for phases in phase_timings.values():
            for name, seconds in phases.items():
                totals[name] = totals.get(name, 0.0) + float(seconds)
        print("")
        print("E2E infrastructure profile (sum across selected tests):")
        for name, seconds in sorted(totals.items(), key=lambda item: (-item[1], item[0])):
            print("  %-18s %8.2fs" % (name, seconds))
        print("")
        print("Slowest profiled tests:")
        ranked = sorted(
            (
                (sum(float(v) for v in phases.values()), test_id, phases)
                for test_id, phases in phase_timings.items()
            ),
            reverse=True,
        )[:20]
        for infra_seconds, test_id, phases in ranked:
            detail = ", ".join(
                "%s=%.2f" % (name, float(value))
                for name, value in sorted(
                    phases.items(), key=lambda item: (-float(item[1]), item[0])
                )
            )
            print("  %7.2fs  %s  [%s]" % (infra_seconds, test_id, detail))

    targeted = tier != "full"
    # Benchmark modes still print profile / slowest for this run, but must not
    # train LPT history or mutate last-failed — those files drive ordinary gates.
    persist_history = not active_benchmark_modes
    if active_benchmark_modes:
        print(
            "benchmark mode (%s): not persisting timing or last-failed history"
            % ",".join(active_benchmark_modes)
        )
    if persist_history:
        # Persist only machine-local observations; the committed bootstrap
        # baseline remains immutable input and must never be copied into .tests.
        _persist_timings(observed, test_ids if not targeted else None)
    # Baseline prefix weights are scheduling hints, not measured test timings;
    # keep them out of the human "slowest tests" report.
    _print_slowest({**local_timings, **observed} if targeted else observed)

    # Persist unresolved failures from actual completions only — never treat
    # the pre-run selection as executed (fail-fast / cancelled / crashed).
    if persist_history:
        executed_ids = _executed_ids_from_observation(observed, failed_ids)
        previous = load_last_failed(REPO / LAST_FAILED_PATH)
        previous_ids = (previous or {}).get("test_ids") or []
        unresolved = merge_last_failed(
            previous_ids, executed_ids, failed_ids, known_ids=all_ids
        )
        last_failed_path = REPO / LAST_FAILED_PATH
        if unresolved:
            written = save_last_failed(
                last_failed_path,
                unresolved,
                meta={
                    "tier": tier,
                    "note": note,
                    "executed": len(executed_ids),
                    "selected": len(test_ids),
                    "failed_this_run": len(failed_ids),
                },
            )
            if written:
                print("Wrote last-failed (%d) → %s" % (len(unresolved), LAST_FAILED_PATH))
            else:
                # Atomic write never committed: any previous state is still valid.
                print(
                    "warning: could not write last-failed state → %s" % LAST_FAILED_PATH,
                    file=sys.stderr,
                )
        elif last_failed_path.is_file():
            try:
                last_failed_path.unlink()
            except OSError:
                pass
            if previous_ids:
                print("Cleared last-failed (all previously failed tests resolved)")

    pointer = None
    if ok and not args.no_pointer_capture:
        # Once per run, in the parent, after every shard has passed -- never
        # once per worker. Under the full-gate supervisor this still runs inside
        # the same deadline-bounded child as the shards above.
        pointer = _run_pointer_capture()
        if pointer != 0:
            print("pointer_capture.py failed", file=sys.stderr)
    elif not ok and not args.no_pointer_capture:
        print("skipping pointer_capture.py because E2E tests failed", file=sys.stderr)

    code = run_exit_code(ok, pointer)
    if code == 0:
        print("E2E PASS (%s)%s" % (tier, "" if tier == "full" else " — not a full gate"))
    else:
        print("E2E FAIL (%s)" % tier)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
