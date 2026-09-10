"""Isolated real-app Playwright harness for PRKS E2E.

Never targets repo data/ or a live PRKS_STORAGE tree. Each AppServer owns a
TemporaryDirectory, binds 127.0.0.1, and tears down the subprocess even when
assertions fail.
"""
from __future__ import annotations

import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
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

REPO = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
READY_TIMEOUT_S = 25.0
STOP_TIMEOUT_S = 8.0


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
        # Deliberately narrow: only blob:, and only resource-load failures.
        if url.startswith("blob:") and "Failed to load resource" in msg.text:
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
        if not self._ann_posts:
            raise AssertionError("expected POST /api/works/{id}/annotations during PDF save handshake")
        if not self._save_confirms:
            raise AssertionError("expected GET /api/works/{id}/save-confirm during PDF save handshake")
        if any(s >= 400 for s in self._pdf_posts + self._ann_posts + self._save_confirms):
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
        # Optional subprocess env overrides (e.g. HTTPS_PROXY to deny a specific
        # best-effort outbound call deterministically). Never used to change how the
        # app talks to its own storage/port.
        self._extra_env = dict(extra_env or {})

    def start(self):
        if self._seed_fn is not None:
            self.ids = self._seed_fn(self.storage_root) or {}
        env = os.environ.copy()
        env["PRKS_TESTING"] = "1"
        env["PRKS_STORAGE"] = self.storage_root
        env.pop("PRKS_FOR_PROCESSING_DIR", None)
        env["PRKS_LOG_FILE"] = os.path.join(self.storage_root, "prks-errors.log")
        env["PYTHONUNBUFFERED"] = "1"
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(apply_playwright_browser_env())
        env.update(self._extra_env)
        for attempt in range(SERVER_START_ATTEMPTS):
            self._spawn(env)
            try:
                wait_http(self.origin + "/api/works")
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
        try:
            if self.proc is not None:
                _terminate(self.proc)
                if self.proc.poll() is None:
                    raise RuntimeError("PRKS E2E server process did not exit")
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
            try:
                _LIVE_SERVERS.remove(self)
            except ValueError:
                pass
            self._tmpdir.cleanup()


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
    context = browser.new_context(
        viewport={"width": 1400, "height": 900},
        service_workers=service_workers,
    )
    page = context.new_page()
    collector = PageCollector(page, origin)
    page.goto(origin + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#sidebar")
    page.wait_for_selector("#page-content")
    page.wait_for_selector(".prks-folder-library")
    # Empty location.hash is treated as Folders; click the nav link like a user.
    page.locator('#sidebar a.nav-link[href="#/folders"]').click()
    page.wait_for_function("() => location.hash === '#/folders'")
    return page, context, collector


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""
