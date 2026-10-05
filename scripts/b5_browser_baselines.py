#!/usr/bin/env python3
"""Record privacy-safe B5 browser/resource baselines (#454).

Uses `python prks_app.py --testing` only. Storage must be a temp tree, never
repo `data/` or a live production `PRKS_STORAGE`. Not a CI benchmark.

Examples:
  python scripts/b5_browser_baselines.py
  python scripts/b5_browser_baselines.py --storage /tmp/prks-b5-baselines-454 \\
      --output docs/b5-browser-baselines/browser-baselines.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO / "docs" / "b5-browser-baselines" / "browser-baselines.json"
DEFAULT_STORAGE = Path("/tmp/prks-b5-baselines-454")
VIEWPORT = {"width": 1400, "height": 900}

TITLE_ONLY = 120
TINY_PDF = 20
PDF_PAGES = 3

INIT_SCRIPT = r"""
(() => {
  const kinds = ["ResizeObserver", "IntersectionObserver", "MutationObserver"];
  window.__prksB5ObserverProbe = { live: {}, constructed: {}, disconnected: {} };
  kinds.forEach((name) => {
    const Orig = window[name];
    if (typeof Orig !== "function") return;
    window.__prksB5ObserverProbe.live[name] = 0;
    window.__prksB5ObserverProbe.constructed[name] = 0;
    window.__prksB5ObserverProbe.disconnected[name] = 0;
    function Wrapped(...args) {
      window.__prksB5ObserverProbe.constructed[name] += 1;
      window.__prksB5ObserverProbe.live[name] += 1;
      const inst = new Orig(...args);
      const origDisc = inst.disconnect.bind(inst);
      let dead = false;
      inst.disconnect = function () {
        if (!dead) {
          dead = true;
          window.__prksB5ObserverProbe.live[name] -= 1;
          window.__prksB5ObserverProbe.disconnected[name] += 1;
        }
        return origDisc();
      };
      return inst;
    }
    Wrapped.prototype = Orig.prototype;
    try { Object.setPrototypeOf(Wrapped, Orig); } catch (_e) {}
    window[name] = Wrapped;
  });
})();
"""

INSTALL_HOOKS = r"""
() => {
  window.__prksB5InitCounts = window.__prksB5InitCounts || {
    initPdfViewerForWork: 0,
    createWorkPdfRuntime: 0,
    createPrksPdfViewer: 0,
  };
  const wrap = (name) => {
    const orig = window[name];
    if (typeof orig !== "function" || orig.__prksB5Wrapped) return;
    const wrapped = function (...args) {
      window.__prksB5InitCounts[name] = (window.__prksB5InitCounts[name] || 0) + 1;
      return orig.apply(this, args);
    };
    wrapped.__prksB5Wrapped = true;
    window[name] = wrapped;
  };
  wrap("initPdfViewerForWork");
  wrap("createWorkPdfRuntime");
  wrap("createPrksPdfViewer");
  if (!window.__prksB5Stamps) {
    window.__prksB5Stamps = new WeakMap();
    window.__prksB5StampN = 0;
  }
  window.__prksB5Stamp = (obj) => {
    if (!obj || (typeof obj !== "object" && typeof obj !== "function")) return null;
    if (!window.__prksB5Stamps.has(obj)) {
      window.__prksB5StampN += 1;
      window.__prksB5Stamps.set(obj, "obj-" + window.__prksB5StampN);
    }
    return window.__prksB5Stamps.get(obj);
  };
};
"""

RESOURCE_PROBE_JS = r"""
() => {
  const stamp = window.__prksB5Stamp || (() => null);
  const thumbSet = window.__prksWorkThumbObserved;
  const thumbs = thumbSet ? Array.from(thumbSet) : [];
  const ctx = typeof prksGetFocusedTabContext === "function" ? prksGetFocusedTabContext() : null;
  const dbg = ctx && typeof ctx.debugSnapshot === "function" ? ctx.debugSnapshot() : null;
  const pdf = ctx && typeof ctx.getResource === "function" ? ctx.getResource("pdf") : null;
  const graph = ctx && typeof ctx.getResource === "function" ? ctx.getResource("researchGraph") : null;
  const graphDbg = typeof prksGetResearchGraphDebug === "function" ? prksGetResearchGraphDebug() : null;
  const obs = window.__prksB5ObserverProbe || null;
  const ws = typeof prksWorkspaceSnapshot === "function" ? prksWorkspaceSnapshot() : null;
  const tabId = ctx ? ctx.tabId : null;
  const parked = document.querySelector("#prks-tab-warm-parking [data-prks-role='pdf-viewer']");
  const mainPdf = document.querySelector(".prks-tile--main [data-prks-role='pdf-viewer']");
  return {
    hash: String(location.hash || ""),
    tabId: tabId,
    mainTabId: ws && ws.mainTabId,
    focusedTabId: ws && ws.focusedTabId,
    tabCount: ws && ws.tabs ? ws.tabs.length : null,
    ctxStamp: stamp(ctx),
    ctxRootStamp: ctx ? stamp(ctx.root) : null,
    ctxMounted: !!(ctx && ctx.mounted),
    ctxSuspended: !!(ctx && ctx.suspended),
    ctxGeneration: ctx ? ctx.generation : null,
    pdfRuntimeStamp: stamp(pdf),
    pdfViewerStamp: pdf ? stamp(pdf.viewer) : null,
    pdfViewerSetupToken: pdf && typeof pdf.viewerSetupToken === "number" ? pdf.viewerSetupToken : null,
    graphRuntimeStamp: stamp(graph),
    graphLiveCount: Number(window.__prksResearchGraphLiveCount || 0),
    graphResizeObserverLive: !!(graphDbg && graphDbg.resizeObserverLive),
    graphChromeListenerCount: graphDbg && typeof graphDbg.chromeListenerCount === "number"
      ? graphDbg.chromeListenerCount : null,
    cleanupCount: dbg ? dbg.cleanupCount : null,
    timerCount: dbg ? dbg.timerCount : null,
    resourceNames: dbg ? dbg.resourceNames : [],
    thumbObserverPresent: !!window.__prksWorkThumbObserver,
    thumbTrackedTargets: thumbs.length,
    thumbTrackedConnected: thumbs.filter((n) => n && n.isConnected).length,
    thumbTrackedDisconnected: thumbs.filter((n) => !n || !n.isConnected).length,
    thumbObservingAttr: document.querySelectorAll("img[data-prks-thumb-observing]").length,
    lazyThumbImgs: document.querySelectorAll("img[data-prks-thumb-lazy]").length,
    workCards: document.querySelectorAll(".work-card").length,
    domNodes: document.getElementsByTagName("*").length,
    canvases: document.querySelectorAll("canvas").length,
    pdfHosts: document.querySelectorAll("[data-prks-role='pdf-viewer']").length,
    pdfHostsMain: document.querySelectorAll(".prks-tile--main [data-prks-role='pdf-viewer']").length,
    pdfHostsParked: document.querySelectorAll("#prks-tab-warm-parking [data-prks-role='pdf-viewer']").length,
    easyMde: document.querySelectorAll(".EasyMDEContainer").length,
    initCounts: Object.assign({}, window.__prksB5InitCounts || {}),
    observers: obs,
    pdfVisibleMain: !!(mainPdf && mainPdf.getClientRects().length),
    pdfParked: !!parked,
    coordinator: typeof prksRequestCoordinatorSnapshot === "function"
      ? prksRequestCoordinatorSnapshot() : null,
  };
}
"""


def _cpu_model() -> str:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return platform.processor() or "unknown"
    for line in text.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[-1].strip()
    return platform.processor() or "unknown"


def _git_sha() -> str:
    out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True)
    return out.strip()


def _find_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _http_json(method: str, url: str, payload=None, timeout: float = 60.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("HTTP %s %s -> %s %s" % (method, url, exc.code, body[:240])) from exc


def _tiny_pdf_bytes(title: str, pages: int = 1) -> bytes:
    import pymupdf as fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=400, height=560)
        page.insert_text((36, 64), title, fontsize=14, fontname="helv")
        page.insert_text((36, 92), "Synthetic page %s" % (i + 1), fontsize=11, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def seed_library(base: str) -> dict:
    folders = _http_json("GET", base + "/api/folders") or []
    works = _http_json("GET", base + "/api/works") or []
    if any((f.get("title") or "") == "Synthetic Library" for f in folders) and len(works) >= 140:
        lib = next(f for f in folders if (f.get("title") or "") == "Synthetic Library")
        batch = None
        for child in lib.get("children") or []:
            if (child.get("title") or "") == "Large Batch":
                batch = child
                break
        a = next((w for w in works if (w.get("title") or "") == "Synthetic Work A"), None)
        b = next((w for w in works if (w.get("title") or "") == "Synthetic Work B"), None)
        return {
            "reused": True,
            "library_id": lib.get("id"),
            "batch_id": batch.get("id") if batch else None,
            "work_a": a.get("id") if a else None,
            "work_b": b.get("id") if b else None,
            "work_count": len(works),
        }

    library = _http_json("POST", base + "/api/folders", {"title": "Synthetic Library", "description": ""})
    batch = _http_json(
        "POST",
        base + "/api/folders",
        {"title": "Large Batch", "description": "", "parent_id": library["id"]},
    )
    _http_json("POST", base + "/api/persons", {"first_name": "Ada", "last_name": "Synthetic"})
    _http_json("POST", base + "/api/persons", {"first_name": "Alan", "last_name": "Baseline"})
    c1 = _http_json("POST", base + "/api/concepts", {"name": "Synthetic Concept One", "description": ""})
    _http_json("POST", base + "/api/concepts", {"name": "Synthetic Concept Two", "description": ""})
    _http_json("POST", base + "/api/positions", {"name": "Synthetic Position", "description": ""})

    pdf_small = _tiny_pdf_bytes("Synthetic PDF", 1)
    for i in range(TITLE_ONLY):
        _http_json(
            "POST",
            base + "/api/works",
            {
                "title": "Synthetic Work Batch %03d" % (i + 1),
                "status": "Not Started",
                "folder_id": batch["id"],
            },
        )
    for i in range(TINY_PDF):
        _http_json(
            "POST",
            base + "/api/works",
            {
                "title": "Synthetic PDF Batch %02d" % (i + 1),
                "status": "Not Started",
                "folder_id": batch["id"],
                "file_b64": base64.b64encode(pdf_small).decode("ascii"),
                "file_name": "synthetic-batch-%02d.pdf" % (i + 1),
            },
        )

    note = "Baseline notes mention [[concept:Synthetic Concept One]]."
    work_a = _http_json(
        "POST",
        base + "/api/works",
        {
            "title": "Synthetic Work A",
            "status": "In Progress",
            "folder_id": library["id"],
            "text_content": note,
            "file_b64": base64.b64encode(_tiny_pdf_bytes("Synthetic Work A", PDF_PAGES)).decode("ascii"),
            "file_name": "synthetic-work-a.pdf",
        },
    )
    work_b = _http_json(
        "POST",
        base + "/api/works",
        {
            "title": "Synthetic Work B",
            "status": "Planned",
            "folder_id": library["id"],
            "file_b64": base64.b64encode(_tiny_pdf_bytes("Synthetic Work B", PDF_PAGES)).decode("ascii"),
            "file_name": "synthetic-work-b.pdf",
        },
    )
    works = _http_json("GET", base + "/api/works") or []
    return {
        "reused": False,
        "library_id": library["id"],
        "batch_id": batch["id"],
        "work_a": work_a["id"],
        "work_b": work_b["id"],
        "work_count": len(works),
        "concept_one": c1.get("id") if isinstance(c1, dict) else None,
    }


def _median(samples: list[float]) -> float | None:
    if not samples:
        return None
    return float(statistics.median(samples))


def _classify_path(path: str) -> str:
    if path.startswith("/api/works/") and "/thumbnail" in path:
        return "work-thumbnail"
    if path.startswith("/api/works"):
        return "work"
    if path.startswith("/api/pdfs"):
        return "pdf"
    if path.startswith("/api/folders"):
        return "folder"
    if path.startswith("/api/search"):
        return "search"
    if path.startswith("/api/research-graph"):
        return "research-graph"
    if path.startswith("/api/"):
        return "api-other"
    return "other"


class RequestTap:
    def __init__(self, page):
        self.counts: dict[str, int] = {}
        self.total = 0
        page.on("request", self._on_request)

    def _on_request(self, request) -> None:
        url = request.url
        path = urlparse(url).path
        kind = _classify_path(path)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.total += 1

    def snapshot(self) -> dict:
        return {"total": self.total, "byKind": dict(self.counts)}

    def delta_since(self, earlier: dict) -> dict:
        now = self.snapshot()
        kinds = set(now["byKind"]) | set(earlier.get("byKind") or {})
        by_kind = {k: now["byKind"].get(k, 0) - (earlier.get("byKind") or {}).get(k, 0) for k in sorted(kinds)}
        return {"total": now["total"] - earlier.get("total", 0), "byKind": by_kind}


def _wait_ready(page, timeout: float = 30000) -> None:
    page.wait_for_function("() => window.__prksWorkspaceReady === true", timeout=timeout)


def _time_until(page, start_js: str, predicate: str, timeout: float = 30000) -> float:
    page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
    page.evaluate(start_js)
    page.wait_for_function(predicate, timeout=timeout)
    return float(page.evaluate("() => performance.now() - window.__prksB5T0"))


def _probe(page) -> dict:
    page.evaluate(INSTALL_HOOKS)
    return page.evaluate(RESOURCE_PROBE_JS)


def _lifetime_keys(probe: dict) -> dict:
    obs = probe.get("observers") or {}
    live = obs.get("live") or {}
    return {
        "domNodes": probe.get("domNodes"),
        "canvases": probe.get("canvases"),
        "pdfHosts": probe.get("pdfHosts"),
        "easyMde": probe.get("easyMde"),
        "researchGraphLive": probe.get("graphLiveCount"),
        "graphResizeObserverLive": probe.get("graphResizeObserverLive"),
        "graphChromeListenerCount": probe.get("graphChromeListenerCount"),
        "cleanupCount": probe.get("cleanupCount"),
        "timerCount": probe.get("timerCount"),
        "thumbObserverPresent": probe.get("thumbObserverPresent"),
        "thumbTrackedTargets": probe.get("thumbTrackedTargets"),
        "thumbTrackedConnected": probe.get("thumbTrackedConnected"),
        "thumbTrackedDisconnected": probe.get("thumbTrackedDisconnected"),
        "thumbObservingAttr": probe.get("thumbObservingAttr"),
        "resizeObserverLive": live.get("ResizeObserver"),
        "intersectionObserverLive": live.get("IntersectionObserver"),
        "mutationObserverLive": live.get("MutationObserver"),
        "initPdfViewerForWork": (probe.get("initCounts") or {}).get("initPdfViewerForWork"),
        "createWorkPdfRuntime": (probe.get("initCounts") or {}).get("createWorkPdfRuntime"),
        "createPrksPdfViewer": (probe.get("initCounts") or {}).get("createPrksPdfViewer"),
    }


def _delta(pre: dict, post: dict) -> dict:
    out = {}
    for key in pre:
        a, b = pre.get(key), post.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[key] = b - a
        elif a == b:
            out[key] = 0
        else:
            out[key] = {"pre": a, "post": b}
    return out


def _sanitize_ids(blob, aliases: dict):
    if isinstance(blob, dict):
        return {k: _sanitize_ids(v, aliases) for k, v in blob.items()}
    if isinstance(blob, list):
        return [_sanitize_ids(v, aliases) for v in blob]
    if isinstance(blob, str):
        out = blob
        for raw, alias in aliases.items():
            if raw and raw in out:
                out = out.replace(raw, alias)
        return out
    return blob


def _wait_http(base: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/", timeout=2) as res:
                if res.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.2)
    raise RuntimeError("server did not become ready: %s" % last)


def measure(page, base: str, seed: dict, tap: RequestTap) -> dict:
    aliases = {
        seed["work_a"]: "WORK_A",
        seed["work_b"]: "WORK_B",
        seed["library_id"]: "FOLDER_LIB",
        seed["batch_id"]: "FOLDER_BATCH",
    }
    work_a = "#/works/" + seed["work_a"]
    work_b = "#/works/" + seed["work_b"]
    batch = "#/folders/" + seed["batch_id"]
    lib = "#/folders/" + seed["library_id"]
    cards_pred = "() => document.querySelectorAll('.work-card').length >= 100"
    pdf_main_pred = (
        "() => !!document.querySelector('.prks-tile--main [data-prks-role=\"pdf-viewer\"] .prks-pdf-page')"
    )
    graph_pred = "() => { const d = prksGetResearchGraphDebug && prksGetResearchGraphDebug(); return !!(d && d.cy); }"

    scenarios = {}

    nav = page.evaluate(
        """() => {
          const t = performance.getEntriesByType('navigation')[0];
          const paints = performance.getEntriesByType('paint');
          const by = {};
          paints.forEach((p) => { by[p.name] = p.startTime; });
          return t ? {
            durationMs: t.duration,
            domContentLoadedMs: t.domContentLoadedEventEnd,
            transferSize: t.transferSize,
            fpMs: by['first-paint'] || null,
            fcpMs: by['first-contentful-paint'] || null,
          } : null;
        }"""
    )
    scenarios["initialLoad"] = {"method": "Navigation Timing on /", "result": nav}

    def timed_hash(name, hash_path, pred, n=5, warmup=1):
        samples = []
        for i in range(warmup + n):
            page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
            page.evaluate("h => prksNavigate(h)", hash_path)
            page.wait_for_function(pred, timeout=30000)
            ms = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
            if i >= warmup:
                samples.append(ms)
        scenarios[name] = {
            "method": "prksNavigate + performance.now until settle",
            "warmupDropped": warmup,
            "samplesMs": [round(s, 3) for s in samples],
            "medianMs": round(_median(samples) or 0.0, 3),
            "settle": pred,
        }

    timed_hash("routeFolderLibrary", "#/folders", "() => location.hash === '#/folders'")
    timed_hash("routeRecent", "#/recent", "() => location.hash === '#/recent'")
    timed_hash("routeProgress", "#/progress", "() => location.hash.indexOf('#/progress') === 0")
    timed_hash("routePeople", "#/people", "() => location.hash === '#/people'")
    timed_hash("routeConcepts", "#/concepts", "() => location.hash === '#/concepts'")
    timed_hash("routeGraphChrome", "#/graph", "() => location.hash === '#/graph'")
    timed_hash("routeFolderDetail", lib, "() => location.hash.indexOf('#/folders/') === 0")
    timed_hash("largeFolderCollection", batch, cards_pred)
    timed_hash("searchBatch", "#/search?q=Batch", cards_pred)
    timed_hash("researchGraphMount", "#/graph", graph_pred)

    page.evaluate("h => prksNavigate(h)", "#/folders")
    page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)

    split_ms = _time_until(
        page,
        "() => prksNavigate('#/folders', {target: 'tile'})",
        "() => document.querySelectorAll('.prks-tile').length >= 2",
        timeout=30000,
    )
    scenarios["splitOpen"] = {
        "method": "prksNavigate(folder, {target:'tile'})",
        "samplesMs": [round(split_ms, 3)],
        "medianMs": round(split_ms, 3),
    }
    side_samples = []
    for i in range(4):
        ms = _time_until(
            page,
            "() => prksNavigate('#/recent', {target: 'tile'})" if i % 2 == 0
            else "() => prksNavigate('#/people', {target: 'tile'})",
            "() => document.querySelectorAll('.prks-tile').length >= 2",
            timeout=30000,
        )
        if i > 0:
            side_samples.append(ms)
    scenarios["secondaryNavWhileSplit"] = {
        "method": "prksNavigate(..., {target:'tile'})",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in side_samples],
        "medianMs": round(_median(side_samples) or 0.0, 3),
    }

    page.evaluate(
        """() => {
          const ids = (prksWorkspaceSnapshot().tabs || []).map((t) => t.id);
          if (ids.length > 1 && typeof prksWorkspaceCloseOtherTabs === 'function') {
            return prksWorkspaceCloseOtherTabs(prksWorkspaceSnapshot().mainTabId);
          }
          return false;
        }"""
    )
    time.sleep(0.3)

    page.evaluate("h => prksNavigate(h)", "#/folders")
    page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
    page.evaluate("h => prksWorkspaceOpenTab(h, {activate: true})", "#/recent")
    page.wait_for_function("() => location.hash === '#/recent'", timeout=15000)
    tab_samples = []
    for i in range(7):
        target = "#/folders" if i % 2 == 0 else "#/recent"
        page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
        page.evaluate(
            """h => {
              const snap = prksWorkspaceSnapshot();
              const tab = (snap.tabs || []).find((t) => t.route === h);
              if (!tab) return false;
              return prksWorkspaceActivateTab(tab.id);
            }""",
            target,
        )
        page.wait_for_function("h => location.hash === h", arg=target, timeout=15000)
        ms = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
        if i > 0:
            tab_samples.append(ms)
    scenarios["tabSwitch"] = {
        "method": "workspace tab activate after two stacked tabs",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in tab_samples],
        "medianMs": round(_median(tab_samples) or 0.0, 3),
    }

    page.evaluate("h => prksNavigate(h)", "#/folders")
    page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
    page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
    page.evaluate("h => prksNavigate(h)", work_a)
    page.wait_for_function(pdf_main_pred, timeout=60000)
    pdf_open = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
    scenarios["pdfOpenCold"] = {
        "method": "first prksNavigate(Work A) until PDF page in main tile",
        "samplesMs": [round(pdf_open, 3)],
        "medianMs": round(pdf_open, 3),
        "warmup": "none (cold open)",
    }

    notes_samples = []
    for i in range(4):
        page.evaluate("h => prksNavigate(h)", "#/folders")
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
        page.evaluate("h => prksNavigate(h)", work_a)
        page.wait_for_function(
            "() => document.querySelectorAll('.EasyMDEContainer, .work-notes-pane').length > 0",
            timeout=30000,
        )
        ms = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
        if i > 0:
            notes_samples.append(ms)
    scenarios["researchNotesMount"] = {
        "method": "work route until notes/EasyMDE settle",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in notes_samples],
        "medianMs": round(_median(notes_samples) or 0.0, 3),
    }

    close_samples = []
    for i in range(4):
        page.evaluate("h => prksNavigate(h)", work_a)
        page.wait_for_function(pdf_main_pred, timeout=60000)
        page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
        page.evaluate("h => prksNavigate(h)", "#/folders")
        page.wait_for_function(
            "() => document.querySelectorAll('.prks-tile--main [data-prks-role=\"pdf-viewer\"]').length === 0",
            timeout=20000,
        )
        ms = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
        if i > 0:
            close_samples.append(ms)
    scenarios["pdfClose"] = {
        "method": "navigate away to folders (cold unmount of current route)",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in close_samples],
        "medianMs": round(_median(close_samples) or 0.0, 3),
    }

    cold_reopen = []
    for i in range(3):
        page.evaluate("h => prksNavigate(h)", "#/folders")
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
        page.evaluate("h => prksNavigate(h)", work_a)
        page.wait_for_function(pdf_main_pred, timeout=60000)
        ms = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
        cold_reopen.append(ms)
    scenarios["pdfColdReopenAfterFolders"] = {
        "method": "reopen Work A after folders — cold route replacement, not warm resume",
        "label": "cold reopen",
        "warmupDropped": 0,
        "samplesMs": [round(s, 3) for s in cold_reopen],
        "medianMs": round(_median(cold_reopen) or 0.0, 3),
    }

    ab_samples = []
    page.evaluate("h => prksNavigate(h)", work_a)
    page.wait_for_function(pdf_main_pred, timeout=60000)
    for i in range(6):
        dest = work_b if i % 2 == 0 else work_a
        page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
        page.evaluate("h => prksNavigate(h)", dest)
        page.wait_for_function(pdf_main_pred, timeout=60000)
        ms = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
        ab_samples.append(ms)
    scenarios["workAToB"] = {
        "method": "hash A↔B until PDF page in main tile",
        "warmupDropped": 0,
        "samplesMs": [round(s, 3) for s in ab_samples],
        "medianMs": round(_median(ab_samples) or 0.0, 3),
    }

    page.evaluate(
        """() => {
          const snap = prksWorkspaceSnapshot();
          if (snap.tabs && snap.tabs.length > 1 && typeof prksWorkspaceCloseOtherTabs === 'function') {
            return prksWorkspaceCloseOtherTabs(snap.mainTabId);
          }
          return false;
        }"""
    )
    time.sleep(0.2)

    page.evaluate("h => prksNavigate(h)", work_a)
    page.wait_for_function(pdf_main_pred, timeout=60000)
    page.evaluate(INSTALL_HOOKS)
    _probe(page)

    # Parked-tab counterpart so activateTab warm-parks the PDF Work.
    page.evaluate("h => prksWorkspaceOpenTab(h, {activate: false})", "#/folders")
    page.wait_for_function(
        "() => (prksWorkspaceSnapshot().tabs || []).length >= 2",
        timeout=10000,
    )

    warm_samples = []
    identity_rows = []
    for i in range(6):
        page.evaluate("h => prksNavigate(h)", work_a)
        page.wait_for_function(pdf_main_pred, timeout=60000)
        page.evaluate(INSTALL_HOOKS)
        before_park = _probe(page)
        folders_tab = page.evaluate(
            """() => {
              const snap = prksWorkspaceSnapshot();
              const tab = (snap.tabs || []).find((t) => t.route === '#/folders' || t.route.indexOf('#/folders') === 0);
              return tab ? tab.id : null;
            }"""
        )
        if not folders_tab:
            raise RuntimeError("no folders tab for warm-park counterpart")
        page.evaluate("id => prksWorkspaceActivateTab(id)", folders_tab)
        page.wait_for_function(
            "() => document.querySelector('#prks-tab-warm-parking [data-prks-role=\"pdf-viewer\"]')",
            timeout=20000,
        )
        parked = _probe(page)
        if not parked.get("pdfParked"):
            raise RuntimeError("PDF did not warm-park into #prks-tab-warm-parking")
        req0 = tap.snapshot()
        inits0 = parked.get("initCounts") or {}
        work_tab = page.evaluate(
            """ha => {
              const snap = prksWorkspaceSnapshot();
              const tab = (snap.tabs || []).find((t) => t.route === ha);
              return tab ? tab.id : null;
            }""",
            work_a,
        )
        page.evaluate("() => { window.__prksB5T0 = performance.now(); }")
        page.evaluate("id => prksWorkspaceActivateTab(id)", work_tab)
        page.wait_for_function(pdf_main_pred, timeout=20000)
        ms = float(page.evaluate("() => performance.now() - window.__prksB5T0"))
        after = _probe(page)
        req_delta = tap.delta_since(req0)
        inits1 = after.get("initCounts") or {}
        row = {
            "resumeMs": round(ms, 3),
            "sameTabContext": before_park.get("ctxStamp") == after.get("ctxStamp") and after.get("ctxStamp") is not None,
            "samePdfRuntime": before_park.get("pdfRuntimeStamp") == after.get("pdfRuntimeStamp")
            and after.get("pdfRuntimeStamp") is not None,
            "samePdfViewer": before_park.get("pdfViewerStamp") == after.get("pdfViewerStamp")
            and after.get("pdfViewerStamp") is not None,
            "viewerSetupTokenPre": before_park.get("pdfViewerSetupToken"),
            "viewerSetupTokenPost": after.get("pdfViewerSetupToken"),
            "workRequestDelta": (req_delta.get("byKind") or {}).get("work", 0),
            "pdfRequestDelta": (req_delta.get("byKind") or {}).get("pdf", 0),
            "requestDelta": req_delta,
            "viewerInitDelta": int(inits1.get("initPdfViewerForWork") or 0) - int(inits0.get("initPdfViewerForWork") or 0),
            "createRuntimeDelta": int(inits1.get("createWorkPdfRuntime") or 0) - int(inits0.get("createWorkPdfRuntime") or 0),
            "createViewerDelta": int(inits1.get("createPrksPdfViewer") or 0) - int(inits0.get("createPrksPdfViewer") or 0),
            "ctxSuspendedAfterPark": parked.get("ctxSuspended"),
        }
        if i > 0:
            warm_samples.append(ms)
            identity_rows.append(row)
        else:
            identity_rows.append({"warmup": True, **row})

    scenarios["pdfWarmResume"] = {
        "method": "warm-park via activateTab(folders) then prksResumeWarmTabContext via activateTab(Work A)",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in warm_samples],
        "medianMs": round(_median(warm_samples) or 0.0, 3),
        "identity": identity_rows,
        "invariants": {
            "sameTabContext": all(r.get("sameTabContext") for r in identity_rows if not r.get("warmup")),
            "samePdfRuntime": all(r.get("samePdfRuntime") for r in identity_rows if not r.get("warmup")),
            "samePdfViewer": all(r.get("samePdfViewer") for r in identity_rows if not r.get("warmup")),
            "workRequestDeltaZero": all(
                r.get("workRequestDelta") == 0 for r in identity_rows if not r.get("warmup")
            ),
            "pdfRequestDeltaZero": all(
                r.get("pdfRequestDelta") == 0 for r in identity_rows if not r.get("warmup")
            ),
            "viewerInitDeltaZero": all(
                r.get("viewerInitDelta") == 0 for r in identity_rows if not r.get("warmup")
            ),
        },
    }

    page.evaluate("h => prksNavigate(h)", batch)
    page.wait_for_function(cards_pred, timeout=30000)
    time.sleep(0.4)
    pre_folder = _lifetime_keys(_probe(page))
    page.evaluate("h => prksNavigate(h)", "#/graph")
    page.wait_for_function(graph_pred, timeout=30000)
    time.sleep(0.2)
    pre_graph = _lifetime_keys(_probe(page))

    for _ in range(10):
        page.evaluate("h => prksNavigate(h)", batch)
        page.wait_for_function(cards_pred, timeout=30000)
        page.evaluate("h => prksNavigate(h)", work_a)
        page.wait_for_function(pdf_main_pred, timeout=60000)
        page.evaluate("h => prksNavigate(h)", "#/graph")
        page.wait_for_function(graph_pred, timeout=30000)
        page.evaluate("h => prksNavigate(h)", "#/recent")
        page.wait_for_function("() => location.hash === '#/recent'", timeout=15000)

    page.evaluate("h => prksNavigate(h)", batch)
    page.wait_for_function(cards_pred, timeout=30000)
    time.sleep(0.4)
    post_folder = _lifetime_keys(_probe(page))
    page.evaluate("h => prksNavigate(h)", "#/graph")
    page.wait_for_function(graph_pred, timeout=30000)
    time.sleep(0.3)
    post_graph = _lifetime_keys(_probe(page))

    lifetime = {
        "cycles": 10,
        "sequence": "Large Batch folder → Work A → Graph → Recent, measured on folder then graph",
        "folderSurface": {"pre": pre_folder, "post": post_folder, "delta": _delta(pre_folder, post_folder)},
        "graphSurface": {"pre": pre_graph, "post": post_graph, "delta": _delta(pre_graph, post_graph)},
    }

    perf = _http_json("GET", base + "/api/diagnostics/performance")
    coord = page.evaluate(
        "() => typeof prksRequestCoordinatorSnapshot === 'function' ? prksRequestCoordinatorSnapshot() : null"
    )
    out = {
        "scenarios": scenarios,
        "lifetime": lifetime,
        "serverDiagnostics": perf,
        "coordinatorAfterScenarios": coord,
            "largeFolderCardCount": page.evaluate(
            "() => document.querySelectorAll('.work-card').length"
        ),
    }
    return _sanitize_ids(out, aliases)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record B5 browser baselines (#454)")
    parser.add_argument("--storage", default=str(DEFAULT_STORAGE), help="Testing PRKS_STORAGE (temp tree)")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = ephemeral)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Sanitized JSON output path")
    parser.add_argument("--keep-server", action="store_true")
    args = parser.parse_args()

    storage = Path(args.storage).resolve()
    if "data" in storage.parts and str(storage).endswith("/data") or storage.name == "data":
        print("Refusing storage path that looks like production data/", file=sys.stderr)
        return 2
    storage.mkdir(parents=True, exist_ok=True)
    port = args.port or _find_port()
    env = os.environ.copy()
    env["PRKS_TESTING"] = "1"
    env["PRKS_STORAGE"] = str(storage)
    env.pop("PRKS_FOR_PROCESSING_DIR", None)
    log_file = storage / "prks-testing.log"
    env["PRKS_LOG_FILE"] = str(log_file)

    proc = subprocess.Popen(
        [sys.executable, str(REPO / "prks_app.py"), "--testing", "--port", str(port)],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = "http://127.0.0.1:%s" % port
    try:
        _wait_http(base)
        seed = seed_library(base)
        sys.path.insert(0, str(REPO))
        from tests.e2e.install_browser import apply_playwright_browser_env, ensure_chromium_installed

        apply_playwright_browser_env()
        ensure_chromium_installed()
        from playwright.sync_api import sync_playwright

        launch_info = {"channel": None, "engine": "chromium"}
        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(headless=True, channel="chrome")
                launch_info["channel"] = "chrome"
            except Exception:
                browser = p.chromium.launch(headless=True)
                launch_info["channel"] = None
            context = browser.new_context(viewport=VIEWPORT)
            context.add_init_script(INIT_SCRIPT)
            page = context.new_page()
            tap = RequestTap(page)
            page.goto(base + "/", wait_until="domcontentloaded", timeout=60000)
            _wait_ready(page)
            page.evaluate(INSTALL_HOOKS)
            measured = measure(page, base, seed, tap)
            ua = page.evaluate("() => ({ua: navigator.userAgent, chrome: navigator.userAgentData && navigator.userAgentData.brands})")
            chrome_version = None
            try:
                chrome_version = browser.version
            except Exception:
                chrome_version = None
            browser.close()

        artifact = {
            "schema": "prks-b5-browser-baselines/v2",
            "issue": 454,
            "privacy": "synthetic titles/ids only; request paths classified without query or filenames",
            "identity": {
                "gitHead": _git_sha(),
                "app": "python prks_app.py --testing --port %s" % port,
                "storageNote": "temp PRKS_STORAGE (not committed; recreate with this script)",
                "viewport": VIEWPORT,
                "chromeChannel": launch_info["channel"],
                "playwrightBrowserVersion": chrome_version,
                "userAgent": ua,
                "os": platform.platform(),
                "python": sys.version.split()[0],
                "cpuModel": _cpu_model(),
            },
            "libraryShape": {
                "worksBrowse": seed["work_count"],
                "titleOnlyBatch": TITLE_ONLY,
                "tinyPdfBatch": TINY_PDF,
                "namedPdfWorks": ["Synthetic Work A", "Synthetic Work B"],
                "folders": "Synthetic Library → child Large Batch",
                "persons": 2,
                "concepts": 2,
                "positions": 1,
                "notes": "Work A notes with [[concept:Synthetic Concept One]]",
                "reusedExistingSeed": seed.get("reused"),
            },
            "warmupPolicy": (
                "Repeated medians drop the first iteration as warmup unless noted. "
                "PDF open is a single cold sample. Warm resume drops the first park/resume. "
                "No HTTP cache clear between samples; service worker may cache app shell. "
                "PDF bytes may be browser-cached after the cold open."
            ),
            "settleConditions": {
                "workspaceReady": "window.__prksWorkspaceReady === true",
                "pdfMain": ".prks-tile--main [data-prks-role=pdf-viewer] .prks-pdf-page",
                "graph": "prksGetResearchGraphDebug().cy truthy",
                "largeFolder": ".work-card count >= 100",
                "warmParked": "#prks-tab-warm-parking [data-prks-role=pdf-viewer]",
            },
            "seed": {
                "script": "scripts/b5_browser_baselines.py",
                "procedure": "POST synthetic folders/works/persons/concepts/positions; tiny PyMuPDF bytes via file_b64",
            },
            **measured,
        }
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", out_path)
        return 0
    finally:
        if not args.keep_server:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
