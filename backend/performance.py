"""In-process, privacy-safe performance diagnostics for PRKS.

Runtime-only aggregate metadata. No persistent storage. Never records
research content, query strings, bodies, SQL, filenames, or dynamic labels.

p50/p95 use nearest-rank on the bounded recent sample (at most SAMPLE_LIMIT
durations): for n sorted samples, index = ceil(p/100 * n) - 1, clamped to
[0, n-1]. They describe that recent window, not a permanent distribution.

DB time is measured time in instrumented PRKS DB operations (execute_query)
and may not include every low-level direct SQLite call.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Iterator

from backend.log_safety import safe_log_id, safe_log_label

LOGGER = logging.getLogger("prks.perf")

SAMPLE_LIMIT = 128
MAX_ROUTES = 128
OTHER_METHOD = "*"
OTHER_ROUTE = "OTHER"

DEFAULT_SLOW_MS = 250.0
SLOW_MS_MIN = 10.0
SLOW_MS_MAX = 60_000.0

SPAN_NAMES = frozenset(
    {
        "db",
        "json_encode",
        "gzip",
        "pdf_file_stats",
        "pdf_text_search",
        "thumbnail_render",
        "thumbnail_encode",
        "portrait_fetch",
        "backup_create",
        "backup_verify",
        "restore_verify",
        "restore_commit",
        "text_index_reconcile",
        "pdf_linearize",
    }
)

COUNTER_NAMES = frozenset(
    {
        "thumbnail_cache_hits",
        "thumbnail_cache_misses",
        "pdf_file_stat_rows",
        "pdf_file_stat_files",
        "db_read",
        "db_write",
    }
)

EXCLUDED_ROUTES = frozenset(
    {
        "/api/diagnostics/performance",
        "/api/diagnostics/performance/reset",
    }
)

_SERVER_TIMING_ORDER = (
    "db",
    "json_encode",
    "gzip",
    "pdf_file_stats",
    "pdf_text_search",
    "thumbnail_render",
    "thumbnail_encode",
    "portrait_fetch",
    "pdf_linearize",
    "text_index_reconcile",
    "backup_create",
    "backup_verify",
    "restore_verify",
    "restore_commit",
)


def clock_ns() -> int:
    return time.perf_counter_ns()


def slow_threshold_ms() -> float:
    raw = (os.environ.get("PRKS_PERF_SLOW_MS") or "").strip()
    if not raw:
        return DEFAULT_SLOW_MS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SLOW_MS
    if not math.isfinite(value):
        return DEFAULT_SLOW_MS
    return max(SLOW_MS_MIN, min(SLOW_MS_MAX, value))


def log_slow_enabled() -> bool:
    value = (os.environ.get("PRKS_PERF_LOG_SLOW") or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _ns_to_ms(ns: int) -> float:
    return round(ns / 1_000_000.0, 1)


def percentile_nearest_rank(samples: list[float], p: float) -> float | None:
    """Nearest-rank percentile of a pre-sorted sample.

    index = ceil(p/100 * n) - 1, clamped to [0, n-1]. Empty → None.
    """
    if not samples:
        return None
    n = len(samples)
    if n == 1:
        return samples[0]
    rank = math.ceil((p / 100.0) * n)
    idx = min(n - 1, max(0, int(rank) - 1))
    return samples[idx]


def classify_sql_write(query: str) -> bool:
    """True when the first SQL keyword is not a read form. Never logs SQL."""
    word: list[str] = []
    for ch in query or "":
        if ch.isspace() or ch == "(":
            if word:
                break
            continue
        word.append(ch)
        if len(word) > 12:
            break
    key = "".join(word).upper()
    return key not in ("SELECT", "PRAGMA", "WITH")


@dataclass
class RequestContext:
    method: str
    route: str
    start_ns: int
    excluded: bool = False
    status: int | None = None
    response_bytes: int | None = None
    db_calls: int = 0
    db_ns: int = 0
    span_ns: dict[str, int] = field(default_factory=dict)
    token: object | None = None


@dataclass
class _RouteAgg:
    method: str
    route: str
    count: int = 0
    status_4xx: int = 0
    status_5xx: int = 0
    slow_count: int = 0
    total_ns: int = 0
    max_ns: int = 0
    total_db_ns: int = 0
    db_calls: int = 0
    total_response_bytes: int = 0
    response_byte_samples: int = 0
    samples: deque[int] = field(default_factory=lambda: deque(maxlen=SAMPLE_LIMIT))


@dataclass
class _SpanAgg:
    count: int = 0
    total_ns: int = 0
    max_ns: int = 0
    samples: deque[int] = field(default_factory=lambda: deque(maxlen=SAMPLE_LIMIT))


class PerformanceRegistry:
    def __init__(self, *, clock: Callable[[], int] | None = None):
        self._clock = clock or clock_ns
        self._lock = threading.Lock()
        self._process_started_at = time.time()
        self._process_started_mono = time.monotonic()
        self._window_started_mono = self._process_started_mono
        self._request_count = 0
        self._slow_count = 0
        self._response_bytes = 0
        self._routes: dict[tuple[str, str], _RouteAgg] = {}
        self._spans: dict[str, _SpanAgg] = {}
        self._counters: dict[str, int] = {name: 0 for name in COUNTER_NAMES}

    def now_ns(self) -> int:
        return int(self._clock())

    def reset(self) -> None:
        with self._lock:
            self._window_started_mono = time.monotonic()
            self._request_count = 0
            self._slow_count = 0
            self._response_bytes = 0
            self._routes.clear()
            self._spans.clear()
            self._counters = {name: 0 for name in COUNTER_NAMES}

    def add_span(self, name: str, duration_ns: int) -> None:
        if name not in SPAN_NAMES:
            return
        dur = max(0, int(duration_ns))
        with self._lock:
            agg = self._spans.get(name)
            if agg is None:
                agg = _SpanAgg()
                self._spans[name] = agg
            agg.count += 1
            agg.total_ns += dur
            if dur > agg.max_ns:
                agg.max_ns = dur
            agg.samples.append(dur)

    def add_counter(self, name: str, delta: int = 1) -> None:
        if name not in COUNTER_NAMES:
            return
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(delta)

    def finish(
        self,
        *,
        method: str,
        route: str,
        duration_ns: int,
        status: int | None,
        response_bytes: int | None,
        db_calls: int,
        db_ns: int,
        slow_ms: float,
    ) -> bool:
        """Record a completed request. Returns True if it counted as slow."""
        dur = max(0, int(duration_ns))
        slow = (dur / 1_000_000.0) >= float(slow_ms)
        key = self._route_key(method, route)
        with self._lock:
            self._request_count += 1
            if slow:
                self._slow_count += 1
            if response_bytes is not None and response_bytes >= 0:
                self._response_bytes += int(response_bytes)
            agg = self._routes.get(key)
            if agg is None:
                agg = _RouteAgg(method=key[0], route=key[1])
                self._routes[key] = agg
            agg.count += 1
            agg.total_ns += dur
            if dur > agg.max_ns:
                agg.max_ns = dur
            agg.samples.append(dur)
            agg.total_db_ns += max(0, int(db_ns))
            agg.db_calls += max(0, int(db_calls))
            if response_bytes is not None and response_bytes >= 0:
                agg.total_response_bytes += int(response_bytes)
                agg.response_byte_samples += 1
            if status is not None:
                if 400 <= status <= 499:
                    agg.status_4xx += 1
                elif 500 <= status <= 599:
                    agg.status_5xx += 1
            if slow:
                agg.slow_count += 1
        return slow

    def snapshot(self) -> dict:
        with self._lock:
            request_count = self._request_count
            slow_count = self._slow_count
            response_bytes = self._response_bytes
            process_started_at = self._process_started_at
            process_started_mono = self._process_started_mono
            window_started_mono = self._window_started_mono
            route_copies = [
                (
                    agg.method,
                    agg.route,
                    agg.count,
                    agg.status_4xx,
                    agg.status_5xx,
                    agg.slow_count,
                    agg.total_ns,
                    agg.max_ns,
                    agg.total_db_ns,
                    agg.db_calls,
                    agg.total_response_bytes,
                    agg.response_byte_samples,
                    list(agg.samples),
                )
                for agg in self._routes.values()
            ]
            span_copies = {
                name: (agg.count, agg.total_ns, agg.max_ns, list(agg.samples))
                for name, agg in self._spans.items()
            }
            counters = dict(self._counters)
        now_mono = time.monotonic()
        threshold = slow_threshold_ms()
        routes = []
        for row in route_copies:
            (
                method,
                route,
                count,
                status_4xx,
                status_5xx,
                route_slow,
                total_ns,
                max_ns,
                total_db_ns,
                db_calls,
                total_response_bytes,
                byte_samples,
                samples,
            ) = row
            ms_samples = sorted(_ns_to_ms(s) for s in samples)
            avg_ms = _ns_to_ms(total_ns // count) if count else 0.0
            db_share = None
            if total_ns > 0:
                db_share = round(100.0 * total_db_ns / total_ns, 1)
            avg_db_ms = _ns_to_ms(total_db_ns // count) if count else 0.0
            avg_bytes = None
            if byte_samples:
                avg_bytes = int(total_response_bytes // byte_samples)
            routes.append(
                {
                    "method": method,
                    "route": route,
                    "count": count,
                    "status_4xx": status_4xx,
                    "status_5xx": status_5xx,
                    "slow_count": route_slow,
                    "avg_ms": avg_ms,
                    "p50_ms": percentile_nearest_rank(ms_samples, 50.0),
                    "p95_ms": percentile_nearest_rank(ms_samples, 95.0),
                    "max_ms": _ns_to_ms(max_ns),
                    "avg_db_ms": avg_db_ms,
                    "db_calls": db_calls,
                    "measured_db_share_percent": db_share,
                    "avg_response_bytes": avg_bytes,
                }
            )
        routes.sort(
            key=lambda item: (
                0 if item["count"] >= 2 else 1,
                -(item["p95_ms"] if item["p95_ms"] is not None else -1.0),
                -item["count"],
                item["method"],
                item["route"],
            )
        )
        spans = {}
        for name, (count, total_ns, max_ns, samples) in span_copies.items():
            if count <= 0:
                continue
            ms_samples = sorted(_ns_to_ms(s) for s in samples)
            spans[name] = {
                "count": count,
                "avg_ms": _ns_to_ms(total_ns // count),
                "p50_ms": percentile_nearest_rank(ms_samples, 50.0),
                "p95_ms": percentile_nearest_rank(ms_samples, 95.0),
                "max_ms": _ns_to_ms(max_ns),
            }
        return {
            "process_started_at": process_started_at,
            "uptime_seconds": max(0, int(now_mono - process_started_mono)),
            "measured_for_seconds": max(0, int(now_mono - window_started_mono)),
            "slow_threshold_ms": threshold,
            "requests": {
                "total": request_count,
                "slow": slow_count,
                "response_bytes": response_bytes,
            },
            "routes": routes,
            "spans": spans,
            "counters": counters,
        }

    def _route_key(self, method: str, route: str) -> tuple[str, str]:
        method = method or "GET"
        route = route or OTHER_ROUTE
        key = (method, route)
        if key in self._routes:
            return key
        if len(self._routes) < MAX_ROUTES:
            return key
        other = (OTHER_METHOD, OTHER_ROUTE)
        if other not in self._routes and len(self._routes) >= MAX_ROUTES:
            return other
        return other


_REGISTRY = PerformanceRegistry()
_request_ctx: ContextVar[RequestContext | None] = ContextVar(
    "prks_perf_request", default=None
)


def _registry() -> PerformanceRegistry:
    return _REGISTRY


def reset_registry_for_tests(registry: PerformanceRegistry | None = None) -> PerformanceRegistry:
    """Replace the process registry. Returns the previous registry. Tests only."""
    global _REGISTRY
    previous = _REGISTRY
    _REGISTRY = registry if registry is not None else PerformanceRegistry()
    return previous


def _safe(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception:
        pass


def is_excluded_route(route: str) -> bool:
    return str(route or "") in EXCLUDED_ROUTES


def begin_request(method: str, route: str, *, excluded: bool = False) -> None:
    def _go() -> None:
        safe_method = safe_log_label(method, fallback="GET")
        safe_path = str(route or OTHER_ROUTE)
        ctx = RequestContext(
            method=safe_method,
            route=safe_path,
            start_ns=_registry().now_ns(),
            excluded=bool(excluded) or is_excluded_route(safe_path),
        )
        ctx.token = _request_ctx.set(ctx)

    _safe(_go)


def set_status(status: int) -> None:
    def _go() -> None:
        ctx = _request_ctx.get()
        if ctx is None:
            return
        ctx.status = int(status)

    _safe(_go)


def set_response_bytes(n: int) -> None:
    def _go() -> None:
        ctx = _request_ctx.get()
        if ctx is None:
            return
        ctx.response_bytes = max(0, int(n))

    _safe(_go)


def record_db_call(duration_ns: int, *, write: bool = False) -> None:
    def _go() -> None:
        dur = max(0, int(duration_ns))
        ctx = _request_ctx.get()
        if ctx is not None:
            ctx.db_calls += 1
            ctx.db_ns += dur
            ctx.span_ns["db"] = ctx.span_ns.get("db", 0) + dur
            if ctx.excluded:
                return
        _registry().add_span("db", dur)
        _registry().add_counter("db_write" if write else "db_read", 1)

    _safe(_go)


def record_span(name: str, duration_ns: int) -> None:
    def _go() -> None:
        if name not in SPAN_NAMES:
            return
        dur = max(0, int(duration_ns))
        ctx = _request_ctx.get()
        if ctx is not None:
            ctx.span_ns[name] = ctx.span_ns.get(name, 0) + dur
            if ctx.excluded:
                return
        _registry().add_span(name, dur)

    _safe(_go)


def record_counter(name: str, delta: int = 1) -> None:
    def _go() -> None:
        ctx = _request_ctx.get()
        if ctx is not None and ctx.excluded:
            return
        _registry().add_counter(name, delta)

    _safe(_go)


def server_timing_header() -> str:
    try:
        ctx = _request_ctx.get()
        if ctx is None:
            return ""
        parts: list[str] = []
        for name in _SERVER_TIMING_ORDER:
            ns = ctx.span_ns.get(name, 0)
            if ns <= 0:
                continue
            parts.append(f"{name};dur={_ns_to_ms(ns):.1f}")
        return ", ".join(parts)
    except Exception:
        return ""


def finish_request(*, request_id: str = "") -> None:
    def _go() -> None:
        ctx = _request_ctx.get()
        if ctx is None:
            return
        end_ns = _registry().now_ns()
        duration_ns = max(0, end_ns - ctx.start_ns)
        if ctx.excluded:
            return
        threshold = slow_threshold_ms()
        slow = _registry().finish(
            method=ctx.method,
            route=ctx.route,
            duration_ns=duration_ns,
            status=ctx.status,
            response_bytes=ctx.response_bytes,
            db_calls=ctx.db_calls,
            db_ns=ctx.db_ns,
            slow_ms=threshold,
        )
        if slow and log_slow_enabled():
            LOGGER.info(
                "slow_request method=%s route=%s status=%s duration_ms=%s db_ms=%s db_calls=%s request_id=%s",
                safe_log_label(ctx.method, fallback="GET"),
                ctx.route,
                ctx.status if ctx.status is not None else 0,
                _ns_to_ms(duration_ns),
                _ns_to_ms(ctx.db_ns),
                ctx.db_calls,
                safe_log_id(request_id),
            )

    _safe(_go)


def clear_request() -> None:
    def _go() -> None:
        ctx = _request_ctx.get()
        if ctx is None:
            return
        token = ctx.token
        if token is not None:
            _request_ctx.reset(token)  # type: ignore[arg-type]
        else:
            _request_ctx.set(None)

    _safe(_go)


def snapshot() -> dict:
    try:
        return _registry().snapshot()
    except Exception:
        return {
            "process_started_at": 0,
            "uptime_seconds": 0,
            "measured_for_seconds": 0,
            "slow_threshold_ms": slow_threshold_ms(),
            "requests": {"total": 0, "slow": 0, "response_bytes": 0},
            "routes": [],
            "spans": {},
            "counters": {name: 0 for name in COUNTER_NAMES},
        }


def reset() -> None:
    _safe(lambda: _registry().reset())


@contextmanager
def span(name: str) -> Iterator[None]:
    t0 = 0
    try:
        t0 = _registry().now_ns()
    except Exception:
        t0 = 0
    try:
        yield
    finally:
        if t0:
            record_span(name, _registry().now_ns() - t0)
