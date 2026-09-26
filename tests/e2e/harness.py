"""Isolated real-app Playwright harness for PRKS E2E.

Never targets repo data/ or a live PRKS_STORAGE tree. Each AppServer owns a
TemporaryDirectory, binds 127.0.0.1, and tears down the subprocess even when
assertions fail.
"""
from __future__ import annotations

import copy
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from tests.e2e.install_browser import (
    INSTALL_HINT,
    apply_playwright_browser_env,
    chromium_executable,
    ensure_chromium_installed,
    install_command,
    installed_chromium_revisions,
    playwright_chromium_revision,
)
from tests.e2e.policy import (
    PROFILE_ENV,
    SEED_CACHE_ENV,
    env_flag_enabled,
)

REPO = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
READY_TIMEOUT_S = 25.0
STOP_TIMEOUT_S = 8.0

_ACTIVE_PROFILE = None
_SEED_CACHE_TMP = None
_SEED_SNAPSHOTS = {}

# Per-test hang diagnosis: always updated (printing remains opt-in via DIAGNOSTIC).
_HEARTBEAT_LOCK = threading.Lock()
_HEARTBEAT = {
    "test_id": "",
    "stage": "",
    "test_started_mono": 0.0,
    "heartbeat_mono": 0.0,
    "test_started_wall": 0.0,
    "heartbeat_wall": 0.0,
}
_HEARTBEAT_FILE = None


def _env_enabled(name: str, default: bool = False) -> bool:
    """Shared with the runner's benchmark-mode decision (tests.e2e.policy)."""
    return env_flag_enabled(name, default)


def start_test_profile(test_id: str) -> None:
    """Begin opt-in per-test infrastructure profiling for the runner."""
    global _ACTIVE_PROFILE
    if not _env_enabled(PROFILE_ENV):
        _ACTIVE_PROFILE = None
        return
    _ACTIVE_PROFILE = {"test_id": test_id, "phases": {}}


def _profile_phase(name: str, seconds: float) -> None:
    profile = _ACTIVE_PROFILE
    if profile is None:
        return
    phases = profile["phases"]
    phases[name] = phases.get(name, 0.0) + max(0.0, float(seconds))


def finish_test_profile(test_id: str) -> dict:
    """Return and clear phase timings for test_id; empty when profiling is off."""
    global _ACTIVE_PROFILE
    profile = _ACTIVE_PROFILE
    _ACTIVE_PROFILE = None
    if not profile or profile.get("test_id") != test_id:
        return {}
    return {
        key: round(float(value), 6)
        for key, value in sorted(profile["phases"].items())
        if value > 0
    }


def seed_cache_enabled() -> bool:
    """Worker-local immutable seed snapshots are on unless explicitly disabled."""
    return _env_enabled(SEED_CACHE_ENV, default=True)


def diagnostic_enabled() -> bool:
    """Opt-in stage/heartbeat markers for hang diagnosis (``PRKS_E2E_DIAGNOSTIC=1``)."""
    return _env_enabled("PRKS_E2E_DIAGNOSTIC")


def set_heartbeat_file(path) -> None:
    """Optional JSON status file the parent polls for hung-worker attribution."""
    global _HEARTBEAT_FILE
    _HEARTBEAT_FILE = str(path) if path else None


def clear_e2e_heartbeat() -> None:
    """Clear in-flight test tracking (between tests / after STOP)."""
    with _HEARTBEAT_LOCK:
        _HEARTBEAT["test_id"] = ""
        _HEARTBEAT["stage"] = ""
        _HEARTBEAT["test_started_mono"] = 0.0
        _HEARTBEAT["heartbeat_mono"] = 0.0
        _HEARTBEAT["test_started_wall"] = 0.0
        _HEARTBEAT["heartbeat_wall"] = 0.0
        _write_heartbeat_file_locked()


def get_e2e_heartbeat() -> dict:
    """Snapshot of the current test id + stage for hang diagnosis."""
    with _HEARTBEAT_LOCK:
        return dict(_HEARTBEAT)


def _write_heartbeat_file_locked() -> None:
    path = _HEARTBEAT_FILE
    if not path:
        return
    payload = {
        "test_id": _HEARTBEAT["test_id"],
        "stage": _HEARTBEAT["stage"],
        "test_started_wall": _HEARTBEAT["test_started_wall"],
        "heartbeat_wall": _HEARTBEAT["heartbeat_wall"],
    }
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except OSError:
        pass


def e2e_heartbeat(
    stage: str,
    test_id: str = "",
    *,
    begin_test: bool = False,
    end_test: bool = False,
) -> None:
    """Update hang-diagnosis state; print only when ``PRKS_E2E_DIAGNOSTIC=1``.

    Always tracks the current unittest id and latest lifecycle stage so a
    per-test watchdog can name what a hung worker was doing. Printing stays
    opt-in so ordinary runs stay quiet.

    Only ``begin_test=True`` replaces the tracked unittest id and resets the
    hang clock for a new test. Ordinary diagnostics (including Chromium
    recycle labels) update the stage only — they must not steal the id or
    restart the timer. Module fixtures arm an activity clock without an id.
    """
    stage_s = str(stage or "").strip() or "?"
    tid_arg = str(test_id or "").strip()
    now_mono = time.monotonic()
    now_wall = time.time()
    print_tid = tid_arg
    with _HEARTBEAT_LOCK:
        if end_test:
            if not print_tid:
                print_tid = _HEARTBEAT["test_id"]
            # Drop the unittest id but keep an activity clock so setUpModule /
            # tearDownModule hangs after stopTest are still watchdog-covered.
            _HEARTBEAT["test_id"] = ""
            _HEARTBEAT["stage"] = "BETWEEN_TESTS"
            _HEARTBEAT["test_started_mono"] = now_mono
            _HEARTBEAT["test_started_wall"] = now_wall
            _HEARTBEAT["heartbeat_mono"] = now_mono
            _HEARTBEAT["heartbeat_wall"] = now_wall
        else:
            if begin_test:
                _HEARTBEAT["test_id"] = tid_arg
                _HEARTBEAT["test_started_mono"] = now_mono
                _HEARTBEAT["test_started_wall"] = now_wall
            elif _HEARTBEAT["test_started_mono"] <= 0:
                # Pre-test fixture activity (e.g. require_chromium in
                # setUpModule): arm the hang clock without inventing an id
                # from diagnostic labels like ``after_N_contexts``.
                _HEARTBEAT["test_started_mono"] = now_mono
                _HEARTBEAT["test_started_wall"] = now_wall
            _HEARTBEAT["stage"] = stage_s
            _HEARTBEAT["heartbeat_mono"] = now_mono
            _HEARTBEAT["heartbeat_wall"] = now_wall
            if not print_tid:
                print_tid = _HEARTBEAT["test_id"]
        _write_heartbeat_file_locked()
    if diagnostic_enabled():
        if print_tid:
            print("[e2e-diag] %s %s" % (stage_s, print_tid), flush=True)
        else:
            print("[e2e-diag] %s" % stage_s, flush=True)


def e2e_diag(stage: str, test_id: str = "") -> None:
    """Privacy-safe stage marker: always heartbeats; prints when diagnostic is on.

    Only stage names and unittest ids — never storage paths, titles, or bodies.
    """
    e2e_heartbeat(stage, test_id)


def chromium_recycle_every(default: int = 0) -> int:
    """How many closed BrowserContexts before relaunching Chromium.

    ``PRKS_E2E_CHROMIUM_RECYCLE_EVERY`` overrides. ``0`` disables. Modules that
    open many service-worker contexts may pass a positive module default.
    """
    raw = os.environ.get("PRKS_E2E_CHROMIUM_RECYCLE_EVERY")
    if raw is None or not str(raw).strip():
        return max(0, int(default))
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return max(0, int(default))


class ChromiumHolder:
    """Module-scoped Playwright + Chromium with optional periodic relaunch.

    Fresh BrowserContexts remain per-test. After ``recycle_every`` contexts have
    been closed, the next ``get_browser()`` restarts Chromium to shed
    service-worker / process accumulation. Recycle is lazy: the last closed
    context only sets a flag, so ``tearDownModule`` / ``close()`` never launches
    an unused browser.
    """

    def __init__(self, *, recycle_every: int | None = None):
        if recycle_every is None:
            recycle_every = chromium_recycle_every(0)
        self.recycle_every = max(0, int(recycle_every))
        self._contexts_since_launch = 0
        self._needs_recycle = False
        self.pw, self.browser = require_chromium()

    def get_browser(self):
        """Return the live browser, relaunching first when a recycle is pending."""
        if self._needs_recycle or self.browser is None:
            self.recycle()
        return self.browser

    def after_context_closed(self) -> None:
        self._contexts_since_launch += 1
        if self.recycle_every and self._contexts_since_launch >= self.recycle_every:
            self._needs_recycle = True

    def recycle(self) -> None:
        # Stage-only: never pass the recycle label as a test_id (that reset
        # the hang clock and poisoned --last-failed attribution).
        e2e_diag("CHROMIUM_RECYCLE after_%d_contexts" % self._contexts_since_launch)
        # Stop the old process, then drop refs *before* relaunch so a failed
        # require_chromium cannot leave get_browser() serving closed instances.
        # Only clear _needs_recycle after a successful relaunch — otherwise the
        # next get_browser() retries instead of returning stale handles.
        try:
            if self.browser is not None:
                try:
                    self.browser.close()
                except Exception:
                    pass
            if self.pw is not None:
                try:
                    self.pw.stop()
                except Exception:
                    pass
        finally:
            self.browser = None
            self.pw = None
        try:
            self.pw, self.browser = require_chromium()
        except Exception:
            self.browser = None
            self.pw = None
            raise
        self._contexts_since_launch = 0
        self._needs_recycle = False

    def close(self) -> None:
        """Stop Chromium without relaunching, even if a recycle was pending."""
        e2e_heartbeat("CHROMIUM_CLOSE")
        self._needs_recycle = False
        try:
            if self.browser is not None:
                try:
                    self.browser.close()
                except Exception:
                    pass
        finally:
            try:
                if self.pw is not None:
                    self.pw.stop()
            finally:
                self.browser = None
                self.pw = None


def clear_seed_cache() -> None:
    """Drop worker-local seed snapshots. Primarily used by unit tests/benchmarks."""
    global _SEED_CACHE_TMP
    _SEED_SNAPSHOTS.clear()
    if _SEED_CACHE_TMP is not None:
        try:
            _SEED_CACHE_TMP.cleanup()
        finally:
            _SEED_CACHE_TMP = None


def _seed_cache_root() -> str:
    global _SEED_CACHE_TMP
    if _SEED_CACHE_TMP is None:
        _SEED_CACHE_TMP = tempfile.TemporaryDirectory(prefix="prks-e2e-seed-cache-")
    return _SEED_CACHE_TMP.name


def _wal_checkpoint_ok(row) -> bool:
    """True when ``PRAGMA wal_checkpoint`` completed without a blocked writer.

    SQLite returns ``(busy, log, checkpointed)``. ``busy != 0`` means the
    checkpoint did not finish; that is not raised as ``sqlite3.Error``, so
    callers must inspect the row before treating the main ``.db`` as complete.
    """
    if row is None:
        return False
    try:
        busy = int(row[0])
    except (TypeError, ValueError, IndexError):
        return False
    return busy == 0


def _finalize_seed_template(template: str) -> None:
    """Checkpoint SQLite WAL into main DB files so clones are self-contained.

    Seed builders open PRKSDatabase without an explicit close. On some Python /
    SQLite timings the template can retain ``-wal``/``-shm`` companions. A
    successful checkpoint makes each clone a single consistent ``.db`` and
    avoids rare WAL-replay surprises when the server opens a fresh copy.

    Fail closed: companions are removed only when ``wal_checkpoint(TRUNCATE)``
    reports ``busy=0``. A blocked/incomplete checkpoint raises so the caller
    never inserts the template into the immutable seed cache — a live connection
    can still mutate the directory after caching, which breaks that contract.
    Connect with ``timeout=0`` so a leaked writer fails immediately instead of
    waiting on SQLite's default busy timeout.
    """
    import sqlite3

    for root, _dirs, files in os.walk(template):
        for name in files:
            if not name.endswith(".db"):
                continue
            db_path = os.path.join(root, name)
            try:
                conn = sqlite3.connect(db_path, timeout=0)
            except sqlite3.Error as exc:
                raise RuntimeError(
                    "E2E seed template SQLite connect failed during WAL finalize"
                ) from exc
            try:
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                conn.commit()
            except sqlite3.Error as exc:
                raise RuntimeError(
                    "E2E seed template WAL checkpoint failed"
                ) from exc
            finally:
                conn.close()
            if not _wal_checkpoint_ok(row):
                raise RuntimeError(
                    "E2E seed template still has an active SQLite user"
                )
            for suffix in ("-wal", "-shm"):
                companion = db_path + suffix
                try:
                    if os.path.exists(companion):
                        os.unlink(companion)
                except OSError:
                    pass


def _materialize_seed(seed_fn, destination: str):
    """Populate destination from a worker-local immutable seed snapshot.

    The first use of a seed function builds a template in a private temporary
    directory. Every AppServer still gets its own fresh storage tree; later
    tests merely copy that pristine template instead of rebuilding SQLite,
    PDFs and research indexes from scratch. Returned fixture IDs are deep
    copied so a test cannot mutate the cache's metadata.

    A failed WAL finalize does not insert into ``_SEED_SNAPSHOTS``. Each build
    attempt uses a unique temporary directory under the cache root so a leftover
    failed template (e.g. Windows refusing to delete an open SQLite DB) cannot
    poison a later retry via ``FileExistsError`` on a reused ``seed-N`` path.
    """
    if not seed_cache_enabled():
        started = time.perf_counter()
        ids = seed_fn(destination) or {}
        _profile_phase("seed_build", time.perf_counter() - started)
        return copy.deepcopy(ids), False

    cached = _SEED_SNAPSHOTS.get(seed_fn)
    hit = cached is not None
    if cached is None:
        cache_root = _seed_cache_root()
        # Unique path per attempt — never derive from len(_SEED_SNAPSHOTS).
        template = tempfile.mkdtemp(prefix="seed-", dir=cache_root)
        started = time.perf_counter()
        try:
            ids = seed_fn(template) or {}
            _finalize_seed_template(template)
        except Exception:
            try:
                shutil.rmtree(template, ignore_errors=True)
            except OSError:
                pass
            raise
        _profile_phase("seed_build", time.perf_counter() - started)
        cached = (template, copy.deepcopy(ids))
        _SEED_SNAPSHOTS[seed_fn] = cached

    template, cached_ids = cached
    started = time.perf_counter()
    # Destination is a fresh TemporaryDirectory root; copy contents into it.
    shutil.copytree(template, destination, dirs_exist_ok=True)
    _profile_phase("seed_clone", time.perf_counter() - started)
    return copy.deepcopy(cached_ids), hit


def python_for_subprocess() -> str:
    """Interpreter that can exec a .py file as argv[1].

    Some IDE wrappers set sys.executable to an AppImage. Popen of that path
    with a script argument never binds HTTP and writes no stdio.
    """
    exe = sys.executable or ""
    name = Path(exe).name.lower()
    if "python" in name and not name.endswith(".appimage"):
        return exe
    versioned = os.path.join(sys.base_prefix, "bin", "python%d.%d" % sys.version_info[:2])
    generic = os.path.join(sys.base_prefix, "bin", "python3")
    for candidate in (versioned, generic, shutil.which("python3"), shutil.which("python")):
        if not candidate:
            continue
        if Path(candidate).name.lower().endswith(".appimage"):
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return exe


def apply_e2e_playwright_env() -> Path:
    """Use the repo-local Chromium cache and never download during a test run."""
    browsers = apply_playwright_browser_env()
    os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    os.environ.setdefault("PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL", "0")
    return browsers


def _playwright_missing() -> RuntimeError:
    return RuntimeError(
        "Playwright is not installed. From the repository root:\n"
        "  python -m pip install -r requirements-dev.txt\n"
        "  %s" % install_command()
    )


def _chromium_missing(revision: str | None = None) -> RuntimeError:
    browsers = apply_playwright_browser_env()
    found = installed_chromium_revisions(browsers)
    lines = [
        "Playwright Chromium is not installed in .playwright-browsers/.",
        "Interpreter: %s" % sys.executable,
    ]
    if revision:
        lines.append("This Playwright package expects chromium-%s." % revision)
    if found:
        lines.append("Found: %s" % ", ".join("chromium-%s" % rev for rev in found))
    else:
        lines.append("Found: (empty cache)")
    lines.append("Install with this same Python:")
    lines.append("  %s" % install_command())
    return RuntimeError("\n".join(lines))


def assert_chromium_installed() -> None:
    """Fail clearly when Playwright or its Chromium revision is absent. Never downloads.

    Inspects the repo-local cache against this interpreter's Playwright package.
    Does not start the Playwright driver, so a missing browser cannot leak
    'Task was destroyed' / TargetClosedError noise.
    """
    apply_e2e_playwright_env()
    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        raise _playwright_missing() from exc
    try:
        revision = playwright_chromium_revision()
    except Exception as exc:
        raise _chromium_missing() from exc
    exe = chromium_executable(apply_playwright_browser_env(), revision)
    if exe is None:
        raise _chromium_missing(revision)


def require_chromium():
    """Return (playwright, browser). Installs Chromium into the repo cache if needed."""
    # Arm the hang clock before launch so setUpModule Chromium hangs are
    # covered even though unittest has not called startTest yet.
    e2e_heartbeat("CHROMIUM_LAUNCH")
    ensure_chromium_installed()
    apply_e2e_playwright_env()
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:
        try:
            pw.stop()
        except Exception:
            pass
        raise _chromium_missing() from exc
    return pw, browser


# Ports handed out by find_free_port() in this process. A port is discovered by
# binding and closing, and only bound for real once the server subprocess starts,
# so nothing stops this process from rediscovering a port it has already promised
# to a server that has not finished starting. Remembering them closes that gap
# within a worker; PRKS_E2E_PORT_BASE closes it across workers.
_CLAIMED_PORTS: set[int] = set()

PORT_ATTEMPTS = 40
SERVER_START_ATTEMPTS = 4

_ADDRESS_IN_USE_MARKERS = (
    "Address already in use",
    "EADDRINUSE",
    "[Errno 98]",
    "WinError 10048",
)


# Poll interval for wait_for_async (ms). Kept at 50 to match the historical
# Python-side loop so gates do not become arbitrarily tighter or looser.
_ASYNC_WAIT_POLL_MS = 50

# One browser-side evaluate: invoke the predicate, await a returned Promise,
# apply Python-like truthiness to the RESOLVED value, and sleep between polls
# without crossing the Python↔Playwright bridge each tick.
_WAIT_FOR_ASYNC_IN_PAGE = """
async ({ expression, arg, timeoutMs, pollMs }) => {
    const isPyTruthy = (v) => {
        if (v === null || v === undefined) {
            return false;
        }
        switch (typeof v) {
            case 'boolean':
                return v;
            case 'number':
                // NaN !== 0 → true (matches Python bool(float('nan'))).
                return v !== 0;
            case 'bigint':
                return v !== 0n;
            case 'string':
                return v.length > 0;
            case 'object':
                if (Array.isArray(v)) {
                    return v.length > 0;
                }
                return Object.keys(v).length > 0;
            default:
                return true;
        }
    };

    // Function-shaped predicates are compiled once. Non-function expressions
    // (e.g. `window.__ready`) are re-evaluated every poll — same as the old
    // Python `page.evaluate(expression)` loop — so a later truthy value is seen.
    const compiled = eval('(' + expression + ')');
    const predicateIsFunction = typeof compiled === 'function';
    const invoke = (pollArg) => {
        if (predicateIsFunction) {
            return compiled(pollArg);
        }
        return eval('(' + expression + ')');
    };

    const deadline = Date.now() + timeoutMs;
    let last = null;
    while (true) {
        const remaining = deadline - Date.now();
        if (remaining <= 0) {
            return { ok: false, value: last };
        }

        // Bound each predicate await by remaining timeout. A Promise that never
        // settles must not block the loop past the caller's deadline (Qodo on
        // #220 / #222). Resolved falsey values still poll as before.
        // Clear the race timer when the predicate wins so fast false polls do
        // not accumulate armed setTimeouts until the overall deadline.
        let settled = null;
        const predicatePromise = Promise.resolve(invoke(arg)).then(
            (value) => {
                settled = { kind: 'value', value: value };
                return settled;
            },
            (err) => {
                settled = { kind: 'error', err: err };
                return settled;
            }
        );
        let timer = null;
        let timedOut = false;
        try {
            timedOut = await Promise.race([
                predicatePromise.then(() => false),
                new Promise((resolve) => {
                    timer = setTimeout(() => resolve(true), remaining);
                }),
            ]);
        } finally {
            if (timer !== null) {
                clearTimeout(timer);
            }
        }
        if (!settled) {
            // Late settle/reject after we leave evaluate: avoid unhandled rejection.
            predicatePromise.catch(() => {});
            return { ok: false, value: last };
        }
        if (settled.kind === 'error') {
            throw settled.err;
        }
        last = settled.value;
        if (isPyTruthy(last)) {
            return { ok: true, value: last };
        }
        if (timedOut || Date.now() >= deadline) {
            return { ok: false, value: last };
        }
        const sleepMs = Math.min(pollMs, Math.max(0, deadline - Date.now()));
        if (sleepMs <= 0) {
            return { ok: false, value: last };
        }
        await new Promise((resolve) => setTimeout(resolve, sleepMs));
    }
}
"""


def wait_for_async(page, expression, arg=None, timeout: float = 15000, message: str = ""):
    """Wait until an ASYNCHRONOUS page predicate resolves to a truthy value.

    `page.wait_for_function` cannot express this. It does await a returned
    Promise, but it then treats the SETTLED PROMISE as the value to test for
    truthiness -- and a promise object is always truthy. So a gate written as

        page.wait_for_function("() => store.listOperations().then(r => !r.length)")

    passes on its first poll whether the queue is empty or not: it waits one
    round trip and reports success. Every durable-queue gate in this suite is
    asynchronous, so they are polled from here instead: one `page.evaluate`
    runs a browser-side loop that awaits each poll's RESOLVED value (same
    semantics as a Python `page.evaluate` loop) and only then tests truthiness.
    Each await is raced against the remaining timeout so a Promise that never
    settles fails with the usual diagnostic instead of hanging the evaluate.

    Truthiness matches Python's rules for JSON-serializable results (empty
    list/dict/string and 0/False/None are failure), so call sites that return
    non-empty sentinels keep working.

    Kept API-compatible with `wait_for_function` (`arg`, `timeout` in ms) so a
    call site converts by swapping the call, not by being rewritten.
    """
    started = time.perf_counter()
    last = None
    try:
        result = page.evaluate(
            _WAIT_FOR_ASYNC_IN_PAGE,
            {
                "expression": expression,
                "arg": arg,
                "timeoutMs": float(timeout),
                "pollMs": _ASYNC_WAIT_POLL_MS,
            },
        )
        if not isinstance(result, dict):
            raise AssertionError(
                (message or "condition never became true")
                + " after %.1fs; last value was %r\n%s"
                % (timeout / 1000.0, result, expression)
            )
        last = result.get("value")
        if result.get("ok"):
            return last
        raise AssertionError(
            (message or "condition never became true")
            + " after %.1fs; last value was %r\n%s" % (timeout / 1000.0, last, expression)
        )
    finally:
        _profile_phase("async_wait", time.perf_counter() - started)


def _port_window():
    """(start, span) when the parent runner assigned this worker a port range.

    Parallel workers each get a disjoint window below the Linux ephemeral range,
    so two workers cannot discover the same free port, and the kernel will not
    hand the same port to an unrelated socket via bind(0) either.
    """
    base = os.environ.get("PRKS_E2E_PORT_BASE")
    span = os.environ.get("PRKS_E2E_PORT_SPAN")
    if not base:
        return None
    try:
        start = int(base)
        width = int(span) if span else 1000
    except ValueError:
        return None
    if start < 1024 or width < 1 or start + width - 1 > 65535:
        return None
    return start, width


def _bindable(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((HOST, port))
        return True
    except OSError:
        return False


def find_free_port() -> int:
    window = _port_window()
    if window is None:
        for _ in range(PORT_ATTEMPTS):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((HOST, 0))
                port = sock.getsockname()[1]
            if port not in _CLAIMED_PORTS:
                _CLAIMED_PORTS.add(port)
                return port
        raise RuntimeError("could not find an unclaimed ephemeral port")
    start, width = window
    for offset in random.sample(range(width), min(width, PORT_ATTEMPTS)):
        port = start + offset
        if port in _CLAIMED_PORTS:
            continue
        if _bindable(port):
            _CLAIMED_PORTS.add(port)
            return port
    raise RuntimeError(
        "no free port in worker range %d-%d" % (start, start + width - 1)
    )


def wait_http(url: str, timeout: float = READY_TIMEOUT_S) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"Host": HOST})
            with urllib.request.urlopen(req, timeout=2) as res:
                if 200 <= res.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = err
        time.sleep(0.05)
    raise RuntimeError("server did not become ready at %s: %s" % (url, last))


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=STOP_TIMEOUT_S)


# Every AppServer that has started and not yet stopped, in this process. A
# worker terminated by the parent (--fail-fast, Ctrl-C) would otherwise leave
# its PRKS subprocess and TemporaryDirectory behind, because a signal does not
# run unittest cleanups. tests/e2e/run.py drains this on SIGTERM/SIGINT.
_LIVE_SERVERS = []


def stop_all_servers() -> int:
    """Stop every still-running AppServer. Never raises. Returns how many."""
    stopped = 0
    for server in list(_LIVE_SERVERS):
        try:
            server.stop()
            stopped += 1
        except Exception:
            pass
    return stopped


def _address_in_use(text: str) -> bool:
    """True only for a real bind conflict, so other startup failures still surface."""
    return any(marker in text for marker in _ADDRESS_IN_USE_MARKERS)


class PageCollector:
    """Fail the scenario on unexpected page errors, console errors, 5xx, and off-origin HTTP."""

    def __init__(self, page, origin: str):
        self.origin = origin.rstrip("/")
        self.pageerrors = []
        self.console_errors = []
        self.failed_requests = []
        self.http_5xx = []
        self.external = []
        self._pdf_posts = []
        self._ann_posts = []
        self._save_confirms = []
        page.on("pageerror", lambda err: self.pageerrors.append(str(err)))
        page.on("console", self._on_console)
        page.on("requestfailed", self._on_failed)
        page.on("response", self._on_response)
        page.route("**/*", self._on_route)

    def _on_console(self, msg):
        if msg.type != "error":
            return
        # "Failed to load resource: net::ERR_*" carries no URL in its text, so a
        # resource failure would otherwise be unattributable. The location is
        # what makes such a report actionable.
        url = ""
        try:
            location = msg.location or {}
            if isinstance(location, dict):
                url = location.get("url") or ""
        except Exception:
            url = ""
        # A blob: URL is revoked when the document that owns it goes away, so a
        # reload or teardown racing an in-flight blob load logs
        # ERR_FILE_NOT_FOUND for it. `_on_failed` already classifies blob:
        # request failures as teardown noise rather than signal; the very same
        # event also surfaces as a console error and gets the same treatment.
        # Only the known revoked-blob teardown error is benign.
        if (url.startswith("blob:") and "Failed to load resource" in msg.text
                and "ERR_FILE_NOT_FOUND" in msg.text):
            return
        self.console_errors.append(msg.text + (" (%s)" % url if url else ""))

    def _on_failed(self, req):
        url = req.url
        if url.startswith("blob:") or url.startswith("data:") or url.startswith("about:"):
            return
        failure = req.failure or {}
        err = failure.get("errorText") if isinstance(failure, dict) else str(failure)
        if err and "net::ERR_ABORTED" in str(err):
            return
        self.failed_requests.append("%s %s" % (req.method, url))

    def _on_response(self, res):
        url = res.url
        method = res.request.method
        status = res.status
        parsed = urlparse(url)
        path = parsed.path or ""
        if method == "POST" and path.endswith("/pdf"):
            self._pdf_posts.append(status)
        if method == "POST" and path.endswith("/annotations"):
            self._ann_posts.append(status)
        if method == "GET" and path.endswith("/save-confirm"):
            self._save_confirms.append(status)
        if status >= 500:
            self.http_5xx.append("%s %s -> %s" % (method, path, status))

    def _allowed_http_origin(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = "%s://%s" % (parsed.scheme, parsed.netloc)
        if origin == self.origin:
            return True
        allowed = urlparse(self.origin)
        try:
            req_host = parsed.hostname
            req_port = parsed.port
            allow_host = allowed.hostname
            allow_port = allowed.port
        except ValueError:
            return False
        if parsed.scheme != allowed.scheme:
            return False
        if req_port != allow_port:
            return False
        loopback = {"127.0.0.1", "localhost", "::1"}
        return req_host in loopback and allow_host in loopback

    def _on_route(self, route):
        started = time.perf_counter()
        url = route.request.url
        try:
            if url.startswith("blob:") or url.startswith("data:") or url.startswith("about:"):
                route.continue_()
                return
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https"):
                if self._allowed_http_origin(url):
                    route.continue_()
                    return
                self.external.append(url)
                route.abort()
                return
            route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                try:
                    route.abort()
                except Exception:
                    pass
        finally:
            _profile_phase("request_routing", time.perf_counter() - started)

    def reset_handshake(self) -> None:
        self._pdf_posts.clear()
        self._ann_posts.clear()
        self._save_confirms.clear()

    def wait_pdf_handshake(self, page, timeout_ms: int = 25000, since_ms: int = 0) -> None:
        page.wait_for_function(
            """(since) => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
                const st = pdf && pdf.syncState;
                return !!(
                    st
                    && st.lastSuccessAt > since
                    && !st.pendingChanges
                    && !st.inFlight
                    && st.lastConfirmedToken
                );
            }""",
            arg=since_ms,
            timeout=timeout_ms,
        )
        if not self._pdf_posts:
            raise AssertionError("expected POST /api/works/{id}/pdf during PDF save handshake")
        # Durable path materializes PDF bytes only; legacy POSTs full-list
        # annotations first, then PDF with a claimed generation. Accept either:
        # PDF+annotations, or PDF with durable ops.
        durable = page.evaluate(
            """() => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
                return !!(pdf && pdf.annotationMutationDurable);
            }"""
        )
        if not durable and not self._ann_posts:
            raise AssertionError("expected POST /api/works/{id}/annotations during PDF save handshake")
        if not durable and not self._save_confirms:
            raise AssertionError("expected GET /api/works/{id}/save-confirm during PDF save handshake")
        statuses = list(self._pdf_posts)
        if not durable:
            statuses.extend(self._ann_posts)
            statuses.extend(self._save_confirms)
        if any(s >= 400 for s in statuses):
            raise AssertionError("PDF save handshake had a failed response")

    def assert_clean(self) -> None:
        problems = []
        if self.pageerrors:
            problems.append("pageerror: " + "; ".join(self.pageerrors[:5]))
        if self.console_errors:
            problems.append("console error: " + "; ".join(self.console_errors[:5]))
        if self.failed_requests:
            problems.append("failed request: " + "; ".join(self.failed_requests[:5]))
        if self.http_5xx:
            problems.append("http 5xx: " + "; ".join(self.http_5xx[:5]))
        if self.external:
            problems.append("external network: " + "; ".join(self.external[:5]))
        if problems:
            raise AssertionError("unexpected browser failures:\n" + "\n".join(problems))


class AppServer:
    """One PRKS subprocess + temp storage. Seed before start()."""

    def __init__(self, seed_fn=None, extra_env=None):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="prks-e2e-")
        self.storage_root = self._tmpdir.name
        self.port = find_free_port()
        self.origin = "http://%s:%s" % (HOST, self.port)
        self.ids = {}
        self.proc = None
        self._stdout_path = os.path.join(self.storage_root, "server.stdout")
        self._stderr_path = os.path.join(self.storage_root, "server.stderr")
        self._stdout = None
        self._stderr = None
        self._seed_fn = seed_fn
        self.seed_cache_hit = False
        # Optional subprocess env overrides (e.g. HTTPS_PROXY to deny a specific
        # best-effort outbound call deterministically). Never used to change how the
        # app talks to its own storage/port.
        self._extra_env = dict(extra_env or {})

    def start(self):
        if self._seed_fn is not None:
            self.ids, self.seed_cache_hit = _materialize_seed(
                self._seed_fn, self.storage_root
            )
        env = os.environ.copy()
        env["PRKS_TESTING"] = "1"
        env["PRKS_STORAGE"] = self.storage_root
        env.pop("PRKS_FOR_PROCESSING_DIR", None)
        env["PRKS_LOG_FILE"] = os.path.join(self.storage_root, "prks-errors.log")
        env["PYTHONUNBUFFERED"] = "1"
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(apply_playwright_browser_env())
        env.update(self._extra_env)
        for attempt in range(SERVER_START_ATTEMPTS):
            started = time.perf_counter()
            self._spawn(env)
            try:
                wait_http(self.origin + "/api/works")
                _profile_phase("server_start", time.perf_counter() - started)
                e2e_heartbeat("SERVER_READY")
                return self
            except Exception:
                # Read the captured output *before* teardown: the log files live
                # inside storage_root, which cleanup deletes.
                out = _read_file(self._stdout_path)
                err = _read_file(self._stderr_path)
                self._release_process()
                retryable = attempt + 1 < SERVER_START_ATTEMPTS and _address_in_use(out + err)
                if retryable:
                    # Only a genuine bind conflict earns another port. Any other
                    # startup failure is reported as itself.
                    self.port = find_free_port()
                    self.origin = "http://%s:%s" % (HOST, self.port)
                    continue
                self._tmpdir.cleanup()
                raise RuntimeError(
                    "PRKS E2E server failed to start on %s\nstdout:\n%s\nstderr:\n%s"
                    % (self.origin, out[-4000:], err[-4000:])
                ) from None
        raise RuntimeError("PRKS E2E server could not obtain a free port")

    def _spawn(self, env):
        # Registered before the readiness probe, so a shutdown that lands while
        # the server is still starting still tears it down.
        if self not in _LIVE_SERVERS:
            _LIVE_SERVERS.append(self)
        self._stdout = open(self._stdout_path, "w", encoding="utf-8")
        self._stderr = open(self._stderr_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [
                python_for_subprocess(),
                str(REPO / "prks_app.py"),
                "--testing",
                "--host",
                HOST,
                "--port",
                str(self.port),
            ],
            cwd=str(REPO),
            env=env,
            stdout=self._stdout,
            stderr=self._stderr,
        )

    def _release_process(self):
        """Terminate the subprocess and close its log handles, keeping storage."""
        try:
            _LIVE_SERVERS.remove(self)
        except ValueError:
            pass
        try:
            if self.proc is not None:
                _terminate(self.proc)
        finally:
            for handle in (self._stdout, self._stderr):
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:
                        pass
            self._stdout = None
            self._stderr = None
            self.proc = None

    def captured_output(self) -> str:
        return "stdout:\n%s\nstderr:\n%s" % (
            _read_file(self._stdout_path),
            _read_file(self._stderr_path),
        )

    def stop(self):
        started = time.perf_counter()
        try:
            if self.proc is not None:
                _terminate(self.proc)
                if self.proc.poll() is None:
                    raise RuntimeError("PRKS E2E server process did not exit")
        finally:
            try:
                for handle in (self._stdout, self._stderr):
                    if handle is not None:
                        try:
                            handle.close()
                        except OSError:
                            pass
                self._stdout = None
                self._stderr = None
                self.proc = None
                try:
                    _LIVE_SERVERS.remove(self)
                except ValueError:
                    pass
                self._tmpdir.cleanup()
            finally:
                _profile_phase("server_stop", time.perf_counter() - started)


class FixtureServer:
    """tests/browser/serve.py on loopback for static fixtures."""

    def __init__(self):
        self.proc = None
        self.origin = None

    def start(self):
        self.proc = subprocess.Popen(
            [python_for_subprocess(), str(REPO / "tests" / "browser" / "serve.py")],
            cwd=str(REPO),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        origin = None
        t0 = time.time()
        assert self.proc.stdout is not None
        while time.time() - t0 < 15:
            line = self.proc.stdout.readline()
            if not line:
                break
            if "markdown_security.html" in line and "dompurify=" not in line:
                origin = line.strip().rsplit("/tests/", 1)[0]
                break
        if not origin:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            self.stop()
            raise RuntimeError("could not parse serve.py URL\n%s" % err)
        self.origin = origin
        wait_http(origin + "/tests/browser/markdown_security.html")
        return self

    def stop(self):
        if self.proc is not None:
            _terminate(self.proc)
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self.proc = None


def open_app_page(browser, origin: str, service_workers: str = "block"):
    started = time.perf_counter()
    # Prefer-reduced-motion is opt-in (`PRKS_E2E_REDUCED_MOTION=1`). Emulating
    # it by default breaks layout/scroll assertions that measure real overflow
    # and compact collapsed chrome (global CSS zeroes transition durations).
    context_kwargs = {
        "viewport": {"width": 1400, "height": 900},
        "service_workers": service_workers,
    }
    if _env_enabled("PRKS_E2E_REDUCED_MOTION"):
        context_kwargs["reduced_motion"] = "reduce"
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    collector = PageCollector(page, origin)
    _profile_phase("browser_context", time.perf_counter() - started)
    e2e_heartbeat("CONTEXT_READY")

    started = time.perf_counter()
    # Canonical home is #/folders. Load it directly: empty-hash canonicalize already
    # mounts .prks-folder-library with location.hash === '#/folders', so the old
    # Folders nav click was a pure re-navigation (~45ms median measured waste).
    # Do not invent fake init state — wait on the real shell + Folders surface.
    page.goto(origin + "/#/folders", wait_until="domcontentloaded")
    page.wait_for_selector("#sidebar")
    page.wait_for_selector(".prks-folder-library")
    page.wait_for_function("() => location.hash === '#/folders'")
    _profile_phase("app_ready", time.perf_counter() - started)
    e2e_heartbeat("APP_READY")
    return page, context, collector


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""
