"""UX Interaction Tour harness.

A thin, artifact-producing wrapper around the existing isolated real-app E2E
infrastructure (tests.e2e.harness.AppServer / PageCollector / require_chromium).
It does not replace or change that harness's normal E2E behavior -- it adds a
parallel, opt-in path that records video, a Playwright trace, checkpoint
screenshots, and a machine-readable action log for each scenario, then rolls
those into a per-run manifest.json and REPORT.md.

Never targets repo data/ or a live PRKS_STORAGE tree -- every scenario gets a
fresh AppServer (fresh TemporaryDirectory + fresh database) and a fresh
browser context, exactly like ordinary E2E.
"""
from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from tests.e2e.harness import AppServer, PageCollector

REPO = Path(__file__).resolve().parents[2]
ARTIFACTS_ROOT = REPO / "artifacts" / "ux-tour"
VIEWPORT = {"width": 1600, "height": 900}


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def record_mode_enabled() -> bool:
    """Deterministic: only the literal value '1' turns recording on."""
    return os.environ.get("PRKS_UX_RECORD") == "1"


def git_commit() -> str | None:
    """Short commit hash, or None when Git metadata isn't available (e.g. a ZIP
    snapshot of the repo with no .git directory)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    commit = out.stdout.strip()
    return commit or None


def playwright_version() -> str | None:
    try:
        from tests.e2e.install_browser import installed_playwright_version

        return installed_playwright_version()
    except Exception:
        return None


def chromium_revision() -> str | None:
    try:
        from tests.e2e.install_browser import playwright_chromium_revision

        return playwright_chromium_revision()
    except Exception:
        return None


class TourCollector(PageCollector):
    """PageCollector plus: unexpected HTTP 4xx is also a tour failure.

    A UX tour is meant to represent successful user workflows, so an
    application-level 4xx it didn't explicitly expect is itself a signal
    something in the sequence went wrong (wrong id, stale state, a control
    that submitted before a precondition was met, etc). Call `expect_status()`
    before a request that intentionally exercises validation.

    This is a TourCollector subclass, not a change to PageCollector itself --
    ordinary E2E semantics (which does not fail on 4xx) are untouched.
    """

    def __init__(self, page, origin: str):
        super().__init__(page, origin)
        self.http_4xx: list[str] = []
        self._expected_path_substrings: list[str] = []

    def expect_status(self, path_substring: str) -> None:
        """Mark responses whose path contains `path_substring` as not a failure,
        even if their status is >= 400 (e.g. deliberately exercising validation)."""
        self._expected_path_substrings.append(path_substring)

    def _on_response(self, res):
        super()._on_response(res)
        status = res.status
        if not (400 <= status < 500):
            return
        parsed = urlparse(res.url)
        path = parsed.path or ""
        if any(sub in path for sub in self._expected_path_substrings):
            return
        self.http_4xx.append("%s %s -> %s" % (res.request.method, path, status))

    def assert_clean(self) -> None:
        super().assert_clean()
        if self.http_4xx:
            raise AssertionError("unexpected HTTP 4xx:\n" + "\n".join(self.http_4xx[:5]))

    def as_dict(self) -> dict:
        return {
            "pageerrors": list(self.pageerrors),
            "console_errors": list(self.console_errors),
            "failed_requests": list(self.failed_requests),
            "http_5xx": list(self.http_5xx),
            "http_4xx": list(self.http_4xx),
            "external": list(self.external),
        }

    def error_count(self) -> int:
        d = self.as_dict()
        return sum(len(v) for v in d.values())


class TourArtifacts:
    """One scenario's recording: checkpoints, a step log, and the failure report.

    Video and trace files are written directly into `self.dir` by Playwright
    (via open_tour_page); this class owns everything else plus the manifest
    entry assembled at teardown.
    """

    def __init__(self, run_dir: Path, name: str):
        self.name = name
        self.dir = run_dir / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir = self.dir / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_names: list[str] = []
        self._steps: list[dict] = []
        self._events_path = self.dir / "events.jsonl"
        self._events_fh = open(self._events_path, "w", encoding="utf-8")
        self._t0 = time.time()
        self.failure: dict | None = None
        self.result: dict | None = None

    def step(self, action: str) -> int:
        """Log one action into events.jsonl. Returns the step number."""
        entry = {"step": len(self._steps) + 1, "action": action}
        self._steps.append(entry)
        self._events_fh.write(json.dumps(entry) + "\n")
        self._events_fh.flush()
        return entry["step"]

    def checkpoint(self, page, name: str) -> str:
        """Numbered checkpoint screenshot. Use at stable, meaningful states only.

        `animations="disabled"` makes Playwright finish any running finite CSS
        transition/animation before capturing, so a checkpoint always shows the settled end
        state (e.g. a drawer fully open, not mid-slide) rather than whatever frame happened
        to be on screen the instant this was called -- the video already covers the motion
        itself. Failure screenshots (`record_failure`) deliberately do NOT use this: they
        must show the exact instantaneous state at the moment of failure."""
        idx = len(self._checkpoint_names) + 1
        fname = "%02d-%s.png" % (idx, name)
        try:
            page.screenshot(path=str(self.checkpoints_dir / fname), animations="disabled")
        except Exception as exc:  # noqa: BLE001 -- artifact capture must not hide real errors
            self.step("[checkpoint capture failed] %s: %s" % (name, exc))
            return fname
        self._checkpoint_names.append(fname)
        self._events_fh.write(json.dumps({"checkpoint": fname}) + "\n")
        self._events_fh.flush()
        return fname

    def record_failure(self, exc: BaseException, page=None) -> None:
        """Best-effort failure context. Never lets a capture error hide the
        original test failure -- every sub-capture is independently guarded."""
        info: dict = {
            "error": "%s: %s" % (type(exc).__name__, exc),
            "last_step": self._steps[-1] if self._steps else None,
            "url": None,
            "workspace": None,
            "active_element": None,
        }
        if page is not None:
            try:
                info["url"] = page.url
            except Exception:
                pass
            try:
                page.screenshot(path=str(self.checkpoints_dir / "99-failure.png"))
                self._checkpoint_names.append("99-failure.png")
            except Exception:
                pass
            try:
                info["workspace"] = page.evaluate(
                    "() => (window.prksWorkspaceSnapshot ? window.prksWorkspaceSnapshot() : null)"
                )
            except Exception:
                pass
            try:
                info["active_element"] = page.evaluate(
                    "() => { const el = document.activeElement;"
                    " return el ? (el.id || el.tagName || null) : null; }"
                )
            except Exception:
                pass
        self.failure = info
        try:
            self._events_fh.write(json.dumps({"failure": info}, default=str) + "\n")
            self._events_fh.flush()
        except Exception:
            pass

    def write_server_log(self, text: str) -> None:
        (self.dir / "server.log").write_text(text or "", encoding="utf-8")

    def write_browser_events(self, collector: TourCollector) -> int:
        data = collector.as_dict()
        (self.dir / "browser-events.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return sum(len(v) for v in data.values())

    def finalize(self, status: str, error_count: int) -> dict:
        try:
            self._events_fh.close()
        except Exception:
            pass
        duration = round(time.time() - self._t0, 2)
        self.result = {
            "name": self.name,
            "status": status,
            "duration": duration,
            "checkpoints": list(self._checkpoint_names),
            "video": "video.webm" if (self.dir / "video.webm").exists() else None,
            "trace": "trace.zip" if (self.dir / "trace.zip").exists() else None,
            "error_count": error_count,
            "failure": self.failure,
        }
        return self.result

    def cleanup(self) -> None:
        """Drop this scenario's heavy artifacts (video/trace/screenshots/logs).
        Called only for a successful scenario when recording wasn't requested."""
        shutil.rmtree(self.dir, ignore_errors=True)


def apply_retention(entry: dict, *, retained: bool) -> None:
    """Mutates a scenario's manifest entry in place so it never claims artifacts are
    available when `open_tour_page` has actually deleted them (a passing scenario in
    default, non-recording mode). `retained=False` nulls out video/trace and clears
    checkpoint filenames so the manifest can't point at files that no longer exist;
    REPORT.md's compact PASS line never reads any of these fields, so this has no
    effect on it."""
    entry["artifacts_retained"] = bool(retained)
    if not retained:
        entry["video"] = None
        entry["trace"] = None
        entry["checkpoints"] = []


@contextlib.contextmanager
def open_tour_page(
    browser, run_dir: Path, name: str, *, seed_fn, record_all: bool, results: list, extra_env: dict | None = None
):
    """Open one UX-tour scenario: fresh AppServer + fresh browser context, video +
    trace recording, and full artifact teardown -- regardless of pass or fail.

    `extra_env` is forwarded to AppServer (e.g. to deny a specific best-effort
    outbound call deterministically, such as YouTube oEmbed, without touching
    application code -- see tests.e2e.harness.AppServer).

    Yields (page, collector, tour). Appends the scenario's manifest entry to
    `results` from its own teardown (so it's recorded even when the `with` body
    raises), then re-raises so the calling unittest method still fails normally.
    """
    tour = TourArtifacts(run_dir, name)
    server = AppServer(seed_fn=seed_fn, extra_env=extra_env)
    context = None
    page = None
    collector = None
    status = "FAIL"
    try:
        server.start()
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(tour.dir),
            record_video_size=VIEWPORT,
            service_workers="block",
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        collector = TourCollector(page, server.origin)
        page.goto(server.origin + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        page.wait_for_selector("#page-content")
        page.wait_for_selector(".prks-folder-library")
        page.locator('#sidebar a.nav-link[href="#/folders"]').click()
        page.wait_for_function("() => location.hash === '#/folders'")
        yield page, collector, tour
        collector.assert_clean()
        status = "PASS"
    except BaseException as exc:  # noqa: BLE001 -- record context, then always re-raise
        tour.record_failure(exc, page)
        raise
    finally:
        if context is not None:
            trace_path = tour.dir / "trace.zip"
            try:
                context.tracing.stop(path=str(trace_path))
            except Exception:
                pass
            video_obj = None
            try:
                video_obj = page.video if page is not None else None
            except Exception:
                video_obj = None
            try:
                context.close()
            except Exception:
                pass
            if video_obj is not None:
                try:
                    produced = Path(video_obj.path())
                    target = tour.dir / "video.webm"
                    if produced.exists() and produced.resolve() != target.resolve():
                        produced.replace(target)
                except Exception:
                    pass
        error_count = tour.write_browser_events(collector) if collector is not None else 0
        server_log = server.captured_output()
        server.stop()
        tour.write_server_log(server_log)
        entry = tour.finalize(status, error_count)
        results.append(entry)
        retain = not (status == "PASS" and not record_all)
        if not retain:
            tour.cleanup()
        apply_retention(entry, retained=retain)


def build_manifest(run_id: str, results: list, *, record_all: bool) -> dict:
    return {
        "run_id": run_id,
        "timestamp": run_id,
        "commit": git_commit(),
        "python_version": platform.python_version(),
        "playwright_version": playwright_version(),
        "chromium_revision": chromium_revision(),
        "viewport": VIEWPORT,
        "record_all": bool(record_all),
        "scenarios": results,
    }


def _fmt_duration(seconds: float) -> str:
    return "%.1fs" % seconds


def build_report_md(manifest: dict) -> str:
    lines = ["# PRKS UX Tour", ""]
    commit = manifest.get("commit") or "(unknown)"
    lines.append("Commit: %s" % commit)
    vp = manifest.get("viewport") or {}
    lines.append("Viewport: %s×%s" % (vp.get("width"), vp.get("height")))
    lines.append("Recording: %s" % ("all scenarios" if manifest.get("record_all") else "failures only"))
    lines.append("")
    for entry in manifest.get("scenarios", []):
        status = entry.get("status", "FAIL")
        name = entry.get("name", "?")
        duration = _fmt_duration(entry.get("duration") or 0.0)
        lines.append("%s %s — %s" % (status, name, duration))
        if status != "PASS":
            failure = entry.get("failure") or {}
            last_step = failure.get("last_step")
            if last_step:
                lines.append("    Last step: %s" % last_step.get("action"))
            if failure.get("url"):
                lines.append("    URL: %s" % failure["url"])
            if entry.get("checkpoints"):
                lines.append("    Screenshot: %s/checkpoints/%s" % (name, entry["checkpoints"][-1]))
            if entry.get("trace"):
                lines.append("    Trace: %s/%s" % (name, entry["trace"]))
            if failure.get("error"):
                lines.append("    Error: %s" % failure["error"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_manifest_and_report(run_dir: Path, run_id: str, results: list, *, record_all: bool) -> dict:
    manifest = build_manifest(run_id, results, record_all=record_all)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    (run_dir / "REPORT.md").write_text(build_report_md(manifest), encoding="utf-8")
    return manifest


def zip_run(run_dir: Path, run_id: str) -> Path:
    archive_path = run_dir / ("prks-ux-tour-%s.zip" % run_id)
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(run_dir.rglob("*")):
            if path == archive_path or path.is_dir():
                continue
            zf.write(path, arcname=str(path.relative_to(run_dir)))
    return archive_path


def all_passed(results: list) -> bool:
    return all(entry.get("status") == "PASS" for entry in results)
