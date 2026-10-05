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
import re
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
DEFAULT_MARKDOWN = REPO / "docs" / "b5-browser-baselines.md"
DEFAULT_STORAGE = Path("/tmp/prks-b5-baselines-454")
VIEWPORT = {"width": 1400, "height": 900}

TITLE_ONLY = 120
TINY_PDF = 20
PDF_PAGES = 3

NEUTRAL_HASH = "#/tags"

INIT_SCRIPT = r"""
(() => {
  const probe = {
    live: { ResizeObserver: 0, IntersectionObserver: 0, MutationObserver: 0, eventListener: 0 },
    constructed: { ResizeObserver: 0, IntersectionObserver: 0, MutationObserver: 0, eventListener: 0 },
    disconnected: { ResizeObserver: 0, IntersectionObserver: 0, MutationObserver: 0, eventListener: 0 },
  };
  window.__prksB5ObserverProbe = probe;
  ["ResizeObserver", "IntersectionObserver", "MutationObserver"].forEach((name) => {
    const Orig = window[name];
    if (typeof Orig !== "function") return;
    function Wrapped(...args) {
      probe.constructed[name] += 1;
      probe.live[name] += 1;
      const inst = new Orig(...args);
      const origDisc = inst.disconnect.bind(inst);
      let dead = false;
      inst.disconnect = function () {
        if (!dead) {
          dead = true;
          probe.live[name] -= 1;
          probe.disconnected[name] += 1;
        }
        return origDisc();
      };
      return inst;
    }
    Wrapped.prototype = Orig.prototype;
    try { Object.setPrototypeOf(Wrapped, Orig); } catch (_e) {}
    window[name] = Wrapped;
  });
  const origAdd = EventTarget.prototype.addEventListener;
  const origRemove = EventTarget.prototype.removeEventListener;
  EventTarget.prototype.addEventListener = function (type, listener, options) {
    probe.constructed.eventListener += 1;
    probe.live.eventListener += 1;
    return origAdd.call(this, type, listener, options);
  };
  EventTarget.prototype.removeEventListener = function (type, listener, options) {
    if (probe.live.eventListener > 0) probe.live.eventListener -= 1;
    probe.disconnected.eventListener += 1;
    return origRemove.call(this, type, listener, options);
  };
})();
"""

INSTALL_HOOKS = r"""
() => {
  window.__prksB5InitCounts = window.__prksB5InitCounts || {
    initPdfViewerForWork: 0,
    createWorkPdfRuntime: 0,
    createPrksPdfViewer: 0,
  };
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
    currentGraphResizeObserverLive: !!(graphDbg && graphDbg.resizeObserverLive),
    currentGraphChromeListenerCount: graphDbg && typeof graphDbg.chromeListenerCount === "number"
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
        workCards: document.querySelectorAll(".project-card--work-card").length,
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


def _walk_folders(folders):
    for item in folders or []:
        yield item
        children = item.get("children") or item.get("subfolders") or []
        yield from _walk_folders(children)


def _folder_by_title(folders, title: str):
    for item in _walk_folders(folders):
        if (item.get("title") or "") == title:
            return item
    return None


def seed_library(base: str) -> dict:
    folders = _http_json("GET", base + "/api/folders") or []
    works = _http_json("GET", base + "/api/works") or []
    lib = _folder_by_title(folders if isinstance(folders, list) else [], "Synthetic Library")
    batch = _folder_by_title(folders if isinstance(folders, list) else [], "Large Batch")
    a = next((w for w in works if (w.get("title") or "") == "Synthetic Work A"), None)
    b = next((w for w in works if (w.get("title") or "") == "Synthetic Work B"), None)
    if lib and batch and a and b and len(works) >= 140:
        return {
            "reused": True,
            "library_id": lib.get("id"),
            "batch_id": batch.get("id"),
            "work_a": a.get("id"),
            "work_b": b.get("id"),
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


def _classify_path(method: str, path: str) -> str:
    m = method.upper()
    if path.startswith("/api/works/") and path.endswith("/thumbnail"):
        return "work-thumbnail"
    if path.startswith("/api/works/") and path.endswith("/opened"):
        return "work-opened"
    if path.startswith("/api/works/") and path.endswith("/annotations-snapshot"):
        return "work-annotations-snapshot"
    if path.startswith("/api/pdfs"):
        return "pdf"
    if m == "GET" and path.startswith("/api/works/") and path.count("/") == 3:
        return "work-detail-get"
    if path.startswith("/api/works"):
        return "work-other"
    if path.startswith("/api/folders"):
        return "folder"
    if path.startswith("/api/search"):
        return "search"
    if path.startswith("/api/research-graph"):
        return "research-graph"
    if path.startswith("/api/"):
        return "api-other"
    return "other"


def _template_path(path: str) -> str:
    path = re.sub(r"/W-[A-Za-z0-9]+", "/:workId", path)
    path = re.sub(r"/F-[A-Za-z0-9]+", "/:folderId", path)
    path = re.sub(r"/C-[A-Za-z0-9]+", "/:conceptId", path)
    return path[:160]


class RequestTap:
    def __init__(self, page):
        self.counts: dict[str, int] = {}
        self.total = 0
        self.events: list[dict] = []
        page.on("request", self._on_request)

    def _on_request(self, request) -> None:
        path = urlparse(request.url).path
        kind = _classify_path(request.method, path)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.total += 1
        self.events.append(
            {"i": self.total, "method": request.method, "kind": kind, "path": _template_path(path)}
        )
        if len(self.events) > 400:
            self.events = self.events[-200:]

    def snapshot(self) -> dict:
        return {"total": self.total, "byKind": dict(self.counts), "eventIndex": self.total}

    def delta_since(self, earlier: dict) -> dict:
        now = self.snapshot()
        kinds = set(now["byKind"]) | set(earlier.get("byKind") or {})
        by_kind = {k: now["byKind"].get(k, 0) - (earlier.get("byKind") or {}).get(k, 0) for k in sorted(kinds)}
        start = int(earlier.get("eventIndex") or 0)
        recent = [e for e in self.events if e["i"] > start]
        return {
            "total": now["total"] - earlier.get("total", 0),
            "byKind": by_kind,
            "events": recent,
            "workDetailGet": by_kind.get("work-detail-get", 0),
            "pdfGet": by_kind.get("pdf", 0),
        }


def _wait_ready(page, timeout: float = 30000) -> None:
    page.wait_for_function("() => window.__prksWorkspaceReady === true", timeout=timeout)


def _go_neutral(page) -> None:
    page.evaluate("h => prksNavigate(h)", NEUTRAL_HASH)
    page.wait_for_function(
        """() => {
          if (location.hash !== '#/tags') return false;
          const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
          return !!(ctx && ctx.root && ctx.root.querySelector('[data-prks-tags-page]'));
        }""",
        timeout=20000,
    )


def _mark_start(page) -> dict:
    return page.evaluate(
        """() => {
          const snap = (function () {
            const gens = {};
            if (typeof prksForEachLiveTabContext === 'function') {
              prksForEachLiveTabContext((ctx) => {
                if (ctx && ctx.tabId) gens[String(ctx.tabId)] = ctx.generation;
              });
            }
            const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
            return {
              gens: gens,
              focusedTabId: focused ? focused.tabId : null,
              focusedGen: focused ? focused.generation : 0,
              hash: String(location.hash || ''),
            };
          })();
          window.__prksB5Mark = Object.assign({ t0: performance.now() }, snap);
          return window.__prksB5Mark;
        }"""
    )


def _elapsed(page) -> float:
    return float(page.evaluate("() => performance.now() - window.__prksB5Mark.t0"))


def _wait_main_route(page, hash_path: str, selector: str) -> None:
    page.wait_for_function(
        """({hashPath, selector}) => {
          const hash = String(location.hash || '');
          const wanted = String(hashPath || '');
          const hashOk = hash === wanted || (wanted.indexOf('?') === -1 && hash.indexOf(wanted + '?') === 0);
          if (!hashOk) return false;
          const mark = window.__prksB5Mark;
          const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
          if (!ctx || !ctx.root || !mark) return false;
          if (!ctx.root.querySelector(selector)) return false;
          const prev = mark.gens[String(ctx.tabId)];
          if (typeof prev === 'number') return ctx.generation > prev;
          return ctx.generation > (mark.focusedGen || 0);
        }""",
        arg={"hashPath": hash_path, "selector": selector},
        timeout=30000,
    )


def _pdf_work_ready_js() -> str:
    return """(id) => {
      const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
      if (!ctx || !ctx.root) return false;
      const work = typeof ctx.getEntity === 'function' ? ctx.getEntity('work') : null;
      const pdf = typeof ctx.getResource === 'function' ? ctx.getResource('pdf') : null;
      if (!work || String(work.id) !== String(id)) return false;
      if (!pdf || String(pdf.workId) !== String(id)) return false;
      return !!ctx.root.querySelector('[data-prks-role="pdf-viewer"] .prks-pdf-page');
    }"""


def _wait_pdf_work(page, work_id: str, timeout: float = 60000) -> None:
    page.wait_for_function(_pdf_work_ready_js(), arg=work_id, timeout=timeout)


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
        "eventListenerLive": live.get("eventListener"),
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
    work_a_id = seed["work_a"]
    work_b_id = seed["work_b"]
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

    def timed_main(name, hash_path, selector, n=5, warmup=1, after_wait=None):
        samples = []
        for i in range(warmup + n):
            _go_neutral(page)
            _mark_start(page)
            page.evaluate("h => prksNavigate(h)", hash_path)
            _wait_main_route(page, hash_path, selector)
            if after_wait:
                after_wait()
            ms = _elapsed(page)
            if i >= warmup:
                samples.append(ms)
        scenarios[name] = {
            "method": "leave to #/tags, prksNavigate, wait generation bump + route root",
            "warmupDropped": warmup,
            "samplesMs": [round(s, 3) for s in samples],
            "medianMs": round(_median(samples) or 0.0, 3),
            "settle": "generation > mark AND " + selector,
            "hash": hash_path if "works" not in hash_path else None,
        }

    def wait_cards():
        page.wait_for_function(
            """() => {
              const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
              if (!ctx || !ctx.root) return false;
              return ctx.root.querySelectorAll('.project-card--work-card').length >= 100;
            }""",
            timeout=30000,
        )

    def wait_graph_cy():
        page.wait_for_function(
            "() => { const d = prksGetResearchGraphDebug && prksGetResearchGraphDebug(); return !!(d && d.cy); }",
            timeout=30000,
        )

    timed_main("routeFolderLibrary", "#/folders", "[data-prks-folder-library-view]")
    timed_main("routeRecent", "#/recent", "[data-prks-recent-view]")
    timed_main("routeProgress", "#/progress?status=Not%20Started", "[data-prks-progress-view]")
    timed_main("routePeople", "#/people", "[data-prks-people-index-view]")
    timed_main("routeConcepts", "#/concepts", "[data-prks-concepts-index-view]")
    timed_main("routeGraphChrome", "#/graph", "[data-prks-role='graph-body']")
    timed_main("routeFolderDetail", lib, "[data-prks-folder-detail-view]")
    timed_main(
        "largeFolderCollection",
        batch,
        "[data-prks-folder-detail-view]",
        after_wait=wait_cards,
    )
    timed_main(
        "searchBatch",
        "#/search?q=Batch",
        "[data-prks-search-view]",
        after_wait=wait_cards,
    )
    timed_main(
        "researchGraphMount",
        "#/graph",
        "[data-prks-role='graph-body']",
        after_wait=wait_graph_cy,
    )

    large_cards = page.evaluate("() => document.querySelectorAll('.project-card--work-card').length")
    if scenarios.get("largeFolderCollection"):
        scenarios["largeFolderCollection"]["cardCount"] = large_cards

    _go_neutral(page)
    page.evaluate("h => prksNavigate(h)", lib)
    _wait_main_route(page, lib, "[data-prks-folder-detail-view]")
    _mark_start(page)
    page.evaluate("h => prksNavigate(h, {target: 'tile'})", lib)
    page.wait_for_function(
        "() => document.querySelectorAll('.prks-tile[data-prks-tab-id]').length >= 2",
        timeout=30000,
    )
    split_ms = _elapsed(page)
    scenarios["splitOpen"] = {
        "method": "prksNavigate(folder-detail, {target:'tile'}) from a single Main pane",
        "samplesMs": [round(split_ms, 3)],
        "medianMs": round(split_ms, 3),
    }

    side_samples = []
    for i in range(4):
        dest = "#/people" if i % 2 == 0 else "#/concepts"
        dest_name = "people" if dest == "#/people" else "concepts"
        _mark_start(page)
        page.evaluate("h => prksNavigate(h, {target: 'tile'})", dest)
        page.wait_for_function(
            """name => {
              const mark = window.__prksB5Mark;
              if (!mark || typeof prksForEachLiveTabContext !== 'function') return false;
              let found = false;
              prksForEachLiveTabContext((ctx) => {
                const route = ctx.lastResolvedRoute || ctx.route;
                if (!route || route.name !== name) return;
                const prev = mark.gens[String(ctx.tabId)];
                if (prev == null || ctx.generation > prev) found = true;
              });
              return found;
            }""",
            arg=dest_name,
            timeout=30000,
        )
        ms = _elapsed(page)
        if i > 0:
            side_samples.append(ms)
    scenarios["secondaryNavWhileSplit"] = {
        "method": "prksNavigate(people|concepts, {target:'tile'}); settle on secondary route name + generation",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in side_samples],
        "medianMs": round(_median(side_samples) or 0.0, 3),
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
    time.sleep(0.3)

    _go_neutral(page)
    page.evaluate("h => prksNavigate(h)", "#/folders")
    _wait_main_route(page, "#/folders", "[data-prks-folder-library-view]")
    page.evaluate("h => prksWorkspaceOpenTab(h, {activate: true})", "#/recent")
    page.wait_for_function(
        "() => location.hash === '#/recent' && !!document.querySelector('[data-prks-recent-view]')",
        timeout=15000,
    )
    tab_samples = []
    for i in range(7):
        target = "#/folders" if i % 2 == 0 else "#/recent"
        sel = "[data-prks-folder-library-view]" if target == "#/folders" else "[data-prks-recent-view]"
        _mark_start(page)
        page.evaluate(
            """h => {
              const snap = prksWorkspaceSnapshot();
              const tab = (snap.tabs || []).find((t) => t.route === h);
              if (!tab) return false;
              return prksWorkspaceActivateTab(tab.id);
            }""",
            target,
        )
        page.wait_for_function(
            """({hashPath, selector}) => {
              if (location.hash !== hashPath) return false;
              const mark = window.__prksB5Mark;
              const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
              if (!ctx || !ctx.root || !mark) return false;
              if (!ctx.root.querySelector(selector)) return false;
              const prev = mark.gens[String(ctx.tabId)];
              if (typeof prev === 'number') return ctx.generation > prev || (ctx.tabId !== mark.focusedTabId && ctx.suspended === false);
              return ctx.tabId !== mark.focusedTabId;
            }""",
            arg={"hashPath": target, "selector": sel},
            timeout=15000,
        )
        ms = _elapsed(page)
        if i > 0:
            tab_samples.append(ms)
    scenarios["tabSwitch"] = {
        "method": "activateTab between parked Folders and Recent; settle on destination view root",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in tab_samples],
        "medianMs": round(_median(tab_samples) or 0.0, 3),
    }

    _go_neutral(page)
    _mark_start(page)
    page.evaluate("h => prksNavigate(h)", work_a)
    _wait_pdf_work(page, work_a_id)
    pdf_open = _elapsed(page)
    scenarios["pdfOpenCold"] = {
        "method": "first prksNavigate(Work A) until focused ctx work/pdf.workId match and a page under ctx.root",
        "samplesMs": [round(pdf_open, 3)],
        "medianMs": round(pdf_open, 3),
        "warmup": "none (cold open)",
        "initCountsAfter": _probe(page).get("initCounts"),
    }

    notes_samples = []
    for i in range(4):
        _go_neutral(page)
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", work_a)
        _wait_pdf_work(page, work_a_id)
        page.wait_for_function(
            "() => document.querySelectorAll('.EasyMDEContainer, .work-notes-pane').length > 0",
            timeout=30000,
        )
        ms = _elapsed(page)
        if i > 0:
            notes_samples.append(ms)
    scenarios["researchNotesMount"] = {
        "method": "leave tags, open Work A until notes/EasyMDE settle",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in notes_samples],
        "medianMs": round(_median(notes_samples) or 0.0, 3),
    }

    close_samples = []
    for i in range(4):
        _go_neutral(page)
        page.evaluate("h => prksNavigate(h)", work_a)
        _wait_pdf_work(page, work_a_id)
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", "#/folders")
        _wait_main_route(page, "#/folders", "[data-prks-folder-library-view]")
        page.wait_for_function(
            "() => document.querySelectorAll('.prks-tile--main [data-prks-role=\"pdf-viewer\"]').length === 0",
            timeout=20000,
        )
        ms = _elapsed(page)
        if i > 0:
            close_samples.append(ms)
    scenarios["pdfClose"] = {
        "method": "navigate away to folders (cold unmount of current route)",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in close_samples],
        "medianMs": round(_median(close_samples) or 0.0, 3),
    }

    cold_reopen = []
    for _i in range(3):
        _go_neutral(page)
        page.evaluate("h => prksNavigate(h)", "#/folders")
        _wait_main_route(page, "#/folders", "[data-prks-folder-library-view]")
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", work_a)
        _wait_pdf_work(page, work_a_id)
        cold_reopen.append(_elapsed(page))
    scenarios["pdfColdReopenAfterFolders"] = {
        "method": "reopen Work A after folders — cold route replacement, not warm resume",
        "label": "cold reopen",
        "warmupDropped": 0,
        "samplesMs": [round(s, 3) for s in cold_reopen],
        "medianMs": round(_median(cold_reopen) or 0.0, 3),
    }

    ab_samples = []
    _go_neutral(page)
    page.evaluate("h => prksNavigate(h)", work_a)
    _wait_pdf_work(page, work_a_id)
    for i in range(6):
        dest_hash = work_b if i % 2 == 0 else work_a
        dest_id = work_b_id if i % 2 == 0 else work_a_id
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", dest_hash)
        _wait_pdf_work(page, dest_id)
        ab_samples.append(_elapsed(page))
    scenarios["workAToB"] = {
        "method": "hash A↔B until focused ctx work id + pdf.workId match dest and a page exists under ctx.root",
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
    _wait_pdf_work(page, work_a_id)
    page.evaluate(INSTALL_HOOKS)

    page.evaluate("h => prksWorkspaceOpenTab(h, {activate: false})", "#/folders")
    page.wait_for_function(
        "() => (prksWorkspaceSnapshot().tabs || []).length >= 2",
        timeout=10000,
    )

    def probe_work_tab(work_hash):
        return page.evaluate(
            """ha => {
              const stamp = window.__prksB5Stamp || (() => null);
              const snap = prksWorkspaceSnapshot();
              const tab = (snap.tabs || []).find((t) => t.route === ha);
              const ctx = tab && typeof prksGetTabContext === 'function' ? prksGetTabContext(tab.id) : null;
              const pdf = ctx && typeof ctx.getResource === 'function' ? ctx.getResource('pdf') : null;
              return {
                tabId: tab ? tab.id : null,
                ctxStamp: stamp(ctx),
                ctxRootStamp: ctx ? stamp(ctx.root) : null,
                ctxMounted: !!(ctx && ctx.mounted),
                ctxSuspended: !!(ctx && ctx.suspended),
                pdfRuntimeStamp: stamp(pdf),
                pdfViewerStamp: pdf ? stamp(pdf.viewer) : null,
                pdfViewerSetupToken: pdf && typeof pdf.viewerSetupToken === 'number' ? pdf.viewerSetupToken : null,
                initCounts: Object.assign({}, window.__prksB5InitCounts || {}),
              };
            }""",
            work_hash,
        )

    warm_samples = []
    identity_rows = []
    for i in range(6):
        page.evaluate("h => prksNavigate(h)", work_a)
        _wait_pdf_work(page, work_a_id)
        before_park = probe_work_tab(work_a)
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
        parked = probe_work_tab(work_a)
        if not parked.get("ctxSuspended"):
            raise RuntimeError("Work A TabContext was not suspended after folders activate")
        time.sleep(0.15)
        req0 = tap.snapshot()
        inits0 = parked.get("initCounts") or {}
        work_tab = parked.get("tabId")
        _mark_start(page)
        page.evaluate("id => prksWorkspaceActivateTab(id)", work_tab)
        _wait_pdf_work(page, work_a_id)
        ms = _elapsed(page)
        after = probe_work_tab(work_a)
        req_delta = tap.delta_since(req0)
        inits1 = after.get("initCounts") or {}
        token_pre = before_park.get("pdfViewerSetupToken")
        token_post = after.get("pdfViewerSetupToken")
        row = {
            "resumeMs": round(ms, 3),
            "sameTabContext": before_park.get("ctxStamp") == after.get("ctxStamp") and after.get("ctxStamp") is not None,
            "samePdfRuntime": before_park.get("pdfRuntimeStamp") == after.get("pdfRuntimeStamp")
            and after.get("pdfRuntimeStamp") is not None,
            "samePdfViewer": before_park.get("pdfViewerStamp") == after.get("pdfViewerStamp")
            and after.get("pdfViewerStamp") is not None,
            "viewerSetupTokenPre": token_pre,
            "viewerSetupTokenPost": token_post,
            "viewerSetupTokenUnchanged": token_pre is not None and token_pre == token_post,
            "workDetailGetDelta": req_delta.get("workDetailGet", 0),
            "pdfRequestDelta": req_delta.get("pdfGet", 0),
            "requestDelta": {
                "total": req_delta.get("total"),
                "byKind": req_delta.get("byKind"),
                "events": req_delta.get("events"),
            },
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

    measured_rows = [r for r in identity_rows if not r.get("warmup")]
    scenarios["pdfWarmResume"] = {
        "method": "warm-park via activateTab(folders) then prksResumeWarmTabContext via activateTab(Work A)",
        "warmupDropped": 1,
        "samplesMs": [round(s, 3) for s in warm_samples],
        "medianMs": round(_median(warm_samples) or 0.0, 3),
        "identity": identity_rows,
        "invariants": {
            "sameTabContext": all(r.get("sameTabContext") for r in measured_rows),
            "samePdfRuntime": all(r.get("samePdfRuntime") for r in measured_rows),
            "samePdfViewer": all(r.get("samePdfViewer") for r in measured_rows),
            "viewerSetupTokenUnchanged": all(r.get("viewerSetupTokenUnchanged") for r in measured_rows),
            "workDetailGetDeltaZero": all(r.get("workDetailGetDelta") == 0 for r in measured_rows),
            "pdfRequestDeltaZero": all(r.get("pdfRequestDelta") == 0 for r in measured_rows),
            "viewerInitDeltaZero": all(r.get("viewerInitDelta") == 0 for r in measured_rows),
            "createViewerDeltaZero": all(r.get("createViewerDelta") == 0 for r in measured_rows),
            "createRuntimeDeltaZero": all(r.get("createRuntimeDelta") == 0 for r in measured_rows),
        },
    }

    def wait_folder_surface():
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", batch)
        _wait_main_route(page, batch, "[data-prks-folder-detail-view]")
        wait_cards()
        time.sleep(0.3)

    def wait_graph_surface():
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", "#/graph")
        _wait_main_route(page, "#/graph", "[data-prks-role='graph-body']")
        wait_graph_cy()
        time.sleep(0.2)

    wait_folder_surface()
    pre_folder = _lifetime_keys(_probe(page))
    wait_graph_surface()
    pre_graph = _lifetime_keys(_probe(page))

    for _ in range(10):
        wait_folder_surface()
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", work_a)
        _wait_pdf_work(page, work_a_id)
        wait_graph_surface()
        _mark_start(page)
        page.evaluate("h => prksNavigate(h)", "#/recent")
        _wait_main_route(page, "#/recent", "[data-prks-recent-view]")

    wait_folder_surface()
    post_folder = _lifetime_keys(_probe(page))
    folder_cards = page.evaluate(
        """() => {
          const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
          const root = ctx && ctx.root ? ctx.root : document;
          return root.querySelectorAll('.project-card--work-card').length;
        }"""
    )
    wait_graph_surface()
    post_graph = _lifetime_keys(_probe(page))

    lifetime = {
        "cycles": 10,
        "sequence": "Large Batch folder → Work A → Graph → Recent; Pre/Post taken on folder then graph after a generation-bumped paint",
        "leakProbes": "Global live Resize/Intersection/MutationObserver and EventTarget listener counts; __prksResearchGraphLiveCount; lazy-thumb tracked targets. Focused-runtime debug fields are not leak evidence.",
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
        "largeFolderCardCount": folder_cards,
    }
    return _sanitize_ids(out, aliases)


def _fmt_ms(value) -> str:
    if value is None:
        return "n/a"
    try:
        return "**%.1f** ms" % float(value)
    except (TypeError, ValueError):
        return str(value)


def _scenario_row(scenarios: dict, key: str, label: str, extra: str = "") -> str:
    row = scenarios.get(key) or {}
    median = row.get("medianMs")
    samples = row.get("samplesMs") or []
    warmup = row.get("warmupDropped")
    method = row.get("method") or ""
    bits = [_fmt_ms(median)]
    if samples:
        bits.append("n=%s" % len(samples))
    if warmup:
        bits.append("warmup dropped %s" % warmup)
    if extra:
        bits.append(extra)
    if row.get("label"):
        bits.append("label: %s" % row["label"])
    result = "; ".join(bits)
    return "| %s | %s | %s |" % (label, method.replace("|", "/"), result)


def _probe_table(surface: dict) -> str:
    pre = surface.get("pre") or {}
    post = surface.get("post") or {}
    delta = surface.get("delta") or {}
    keys = list(pre.keys())
    lines = ["| Probe | Pre | Post | Δ |", "| --- | --- | --- | --- |"]
    for key in keys:
        lines.append(
            "| `%s` | %s | %s | %s |"
            % (key, pre.get(key), post.get(key), delta.get(key))
        )
    return "\n".join(lines)


def write_markdown(artifact: dict, path: Path) -> None:
    ident = artifact.get("identity") or {}
    shape = artifact.get("libraryShape") or {}
    scenarios = artifact.get("scenarios") or {}
    lifetime = artifact.get("lifetime") or {}
    perf = artifact.get("serverDiagnostics") or {}
    warm = scenarios.get("pdfWarmResume") or {}
    invariants = warm.get("invariants") or {}
    init_after = (scenarios.get("pdfOpenCold") or {}).get("initCountsAfter") or {}
    nav = (scenarios.get("initialLoad") or {}).get("result") or {}
    folder = lifetime.get("folderSurface") or {}
    graph = lifetime.get("graphSurface") or {}
    ua = ident.get("userAgent")
    ua_s = ua.get("ua") if isinstance(ua, dict) else ua
    window = perf.get("window") if isinstance(perf, dict) and "window" in perf else perf

    lines = [
        "# B5 browser performance / resource baselines (#454)",
        "",
        "Regression baseline and resource-lifetime evidence for the finish-line Vue",
        "cutover. **No pass/fail thresholds** are invented here — numbers are a",
        "comparison point for later work.",
        "",
        "Does **not** close #303 or #230. Does **not** mark B5 complete by itself.",
        "Stacked on cleanup-only #453 (`cursor/b5-final-purge-228c`).",
        "",
        "## Measurement identity",
        "",
        "| Field | Value |",
        "| --- | --- |",
        "| Measured git HEAD | `%s` |" % ident.get("gitHead"),
        "| App | `%s` |" % ident.get("app"),
        "| Storage | temp `PRKS_STORAGE` (never repo `data/` / live production tree); recreate with the harness |",
        "| Client harness | Playwright Chromium channel=`%s`, viewport %s, headless |"
        % (ident.get("chromeChannel"), ident.get("viewport")),
        "| Playwright browser version | `%s` |" % ident.get("playwrightBrowserVersion"),
        "| User agent | `%s` |" % ua_s,
        "| OS | `%s` |" % ident.get("os"),
        "| Python | `%s` |" % ident.get("python"),
        "| CPU | `%s` |" % ident.get("cpuModel"),
        "| Timing | leave `#/tags`, then `performance.now()` until focused-ctx generation bump + route root |",
        "| Server diagnostics | `GET /api/diagnostics/performance` after client scenarios |",
        "| Leak probes | global live Resize/Intersection/MutationObserver + EventTarget listener counts; `__prksResearchGraphLiveCount`; Work-card lazy-thumb tracked targets |",
        "| Privacy | Synthetic titles only (`Synthetic Work …`, `Synthetic Library`, …) |",
        "",
        "## Reproduce",
        "",
        "```bash",
        "python scripts/b5_browser_baselines.py --storage /tmp/prks-b5-baselines-454 \\",
        "  --output docs/b5-browser-baselines/browser-baselines.json \\",
        "  --markdown docs/b5-browser-baselines.md",
        "```",
        "",
        "Uses `python prks_app.py --testing` only. Seed procedure, settle predicates,",
        "warmup policy, and raw samples live in the committed JSON next to this file.",
        "",
        "## Testing-library shape",
        "",
        "| Kind | Count / note |",
        "| --- | --- |",
        "| Works (browse) | %s |" % shape.get("worksBrowse"),
        "| Title-only batch | %s in folder `Large Batch` |" % shape.get("titleOnlyBatch"),
        "| Tiny PDF batch | %s in `Large Batch` |" % shape.get("tinyPdfBatch"),
        "| Named PDF works | `%s` |" % "`, `".join(shape.get("namedPdfWorks") or []),
        "| Folders | %s |" % shape.get("folders"),
        "| Persons | %s |" % shape.get("persons"),
        "| Concepts | %s |" % shape.get("concepts"),
        "| Positions | %s |" % shape.get("positions"),
        "| Research notes | %s |" % shape.get("notes"),
        "| Seed reused | %s |" % shape.get("reusedExistingSeed"),
        "",
        "## Client scenario baselines (median ms unless noted)",
        "",
        "| Scenario | Method | Result |",
        "| --- | --- | --- |",
        "| Initial client load | Navigation Timing on `/` | duration %s; DCL %s; FP %s; FCP %s; transfer %s |"
        % (
            _fmt_ms(nav.get("durationMs")),
            _fmt_ms(nav.get("domContentLoadedMs")),
            _fmt_ms(nav.get("fpMs")),
            _fmt_ms(nav.get("fcpMs")),
            nav.get("transferSize"),
        ),
        _scenario_row(scenarios, "routeFolderLibrary", "Route → Folder Library"),
        _scenario_row(scenarios, "routeRecent", "Route → Recent"),
        _scenario_row(scenarios, "routeProgress", "Route → Progress"),
        _scenario_row(scenarios, "routePeople", "Route → People"),
        _scenario_row(scenarios, "routeConcepts", "Route → Concepts"),
        _scenario_row(scenarios, "routeGraphChrome", "Route → Graph chrome"),
        _scenario_row(scenarios, "routeFolderDetail", "Route → Folder detail"),
        _scenario_row(scenarios, "tabSwitch", "Tab switching"),
        _scenario_row(scenarios, "splitOpen", "Main/Secondary split open"),
        _scenario_row(scenarios, "secondaryNavWhileSplit", "Secondary nav while split"),
        _scenario_row(
            scenarios,
            "largeFolderCollection",
            "Large folder collection",
            extra="%s cards" % artifact.get("largeFolderCardCount"),
        ),
        _scenario_row(scenarios, "searchBatch", "Search `Batch`"),
        _scenario_row(
            scenarios,
            "pdfOpenCold",
            "PDF open (cold)",
            extra="initCounts %s" % init_after,
        ),
        _scenario_row(
            scenarios,
            "pdfColdReopenAfterFolders",
            "PDF cold reopen after folders",
        ),
        _scenario_row(
            scenarios,
            "pdfWarmResume",
            "PDF warm resume (`prksResumeWarmTabContext`)",
        ),
        _scenario_row(scenarios, "pdfClose", "PDF close (cold unmount)"),
        _scenario_row(scenarios, "workAToB", "Work A→B (dest work/pdf.workId)"),
        _scenario_row(scenarios, "researchNotesMount", "Research Notes mount"),
        _scenario_row(scenarios, "researchGraphMount", "Research Graph mount (cy)"),
        "",
        "Warm-resume invariants (measured rows, not warmup):",
        "",
        "```json",
        json.dumps(invariants, indent=2, sort_keys=True),
        "```",
        "",
        "## Repeated mount / resource lifetime",
        "",
        "%s cycles: %s"
        % (lifetime.get("cycles"), lifetime.get("sequence") or ""),
        "",
        lifetime.get("leakProbes") or "",
        "",
        "These tables record **absolute** Pre/Post counts on the named surface after",
        "a generation-bumped paint. Unchanged Δ is evidence only for the listed probes.",
        "It does **not** claim that every listener or observer in the process was",
        "released. Focused-runtime graph `debug()` fields (`resizeObserverLive`,",
        "`chromeListenerCount`) describe the *current* mount and are omitted from the",
        "leak table.",
        "",
        "### Folder surface (Large Batch)",
        "",
        _probe_table(folder),
        "",
        "### Graph surface (`#/graph`)",
        "",
        _probe_table(graph),
        "",
        "## Server diagnostics after client scenarios",
        "",
        "```json",
        json.dumps(window if window else perf, indent=2, sort_keys=True)[:4000],
        "```",
        "",
        "## Follow-ups",
        "",
        "- No tightly coupled migration defect is opened from this measurement pass.",
        "- Known unrelated Full E2E flake: #383 (private-reminder hide→re-tile).",
        "- Broader slowness is not turned into a threshold here.",
        "",
        "## Raw harness output",
        "",
        "Committed default capture: `docs/b5-browser-baselines/browser-baselines.json`.",
        "Regenerate with `scripts/b5_browser_baselines.py` (this document is emitted from that JSON).",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")

def main() -> int:
    parser = argparse.ArgumentParser(description="Record B5 browser baselines (#454)")
    parser.add_argument("--storage", default=str(DEFAULT_STORAGE), help="Testing PRKS_STORAGE (temp tree)")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = ephemeral)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Sanitized JSON output path")
    parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN), help="Markdown summary path")
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

    err_path = storage / "server-stderr.log"
    err_f = open(err_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "prks_app.py"), "--testing", "--port", str(port)],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=err_f,
    )
    base = "http://127.0.0.1:%s" % port
    try:
        try:
            _wait_http(base)
        except Exception:
            err_f.flush()
            snippet = ""
            try:
                snippet = err_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            except OSError:
                snippet = ""
            raise RuntimeError("server failed to start: %s" % snippet)
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
            "schema": "prks-b5-browser-baselines/v3",
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
                "neutralLeave": "#/tags + focused ctx [data-prks-tags-page]",
                "routeMount": "location.hash + focused ctx.generation > mark + ctx.root querySelector(route root)",
                "pdfWork": "focused ctx work.id and pdf.workId match dest; page under ctx.root",
                "largeFolder": "focused ctx .project-card--work-card count >= 100 after folder-detail root",
                "search": "[data-prks-search-view] after leave-to-tags, then same card count on focused ctx",
                "graphCy": "generation bump + [data-prks-role=graph-body] + prksGetResearchGraphDebug().cy",
                "secondaryNav": "a live ctx whose route.name is dest and generation > mark",
                "warmParked": "#prks-tab-warm-parking [data-prks-role=pdf-viewer] and Work tab ctx.suspended",
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
        md_path = Path(args.markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(artifact, md_path)
        print("wrote", out_path)
        print("wrote", md_path)
        return 0
    finally:
        if not args.keep_server:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        err_f.close()


if __name__ == "__main__":
    raise SystemExit(main())
