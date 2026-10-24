"""Performance-diagnostics HTTP response DTOs.

Shape only. The in-process registry in ``backend.performance`` still owns
measurement. These models do not store diagnostics, and they are not a
durable or offline protocol.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PerformanceRequestTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(..., ge=0)
    slow: int = Field(..., ge=0)
    response_bytes: int = Field(..., ge=0)


class PerformanceRouteStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    route: str
    count: int = Field(..., ge=0)
    status_4xx: int = Field(..., ge=0)
    status_5xx: int = Field(..., ge=0)
    slow_count: int = Field(..., ge=0)
    avg_ms: float
    p50_ms: Optional[float]
    p95_ms: Optional[float]
    max_ms: float
    avg_db_ms: float
    db_calls: int = Field(..., ge=0)
    db_calls_avg: float
    measured_db_share_percent: Optional[float]
    avg_response_bytes: Optional[int]


class PerformanceSpanStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(..., ge=0)
    avg_ms: float
    p50_ms: Optional[float]
    p95_ms: Optional[float]
    max_ms: float


class PerformanceCounters(BaseModel):
    """Exact counter map the registry always emits (zeros included)."""

    model_config = ConfigDict(extra="forbid")

    thumbnail_cache_hits: int = Field(..., ge=0)
    thumbnail_cache_misses: int = Field(..., ge=0)
    pdf_file_stat_rows: int = Field(..., ge=0)
    pdf_file_stat_files: int = Field(..., ge=0)
    db_read: int = Field(..., ge=0)
    db_write: int = Field(..., ge=0)


class PerformanceSnapshot(BaseModel):
    """GET /api/diagnostics/performance body.

    Runtime aggregate metadata. No research content, query strings, or names.
    """

    model_config = ConfigDict(extra="forbid")

    process_started_at: float
    uptime_seconds: int = Field(..., ge=0)
    measured_for_seconds: int = Field(..., ge=0)
    slow_threshold_ms: float
    requests: PerformanceRequestTotals
    routes: list[PerformanceRouteStat]
    spans: dict[str, PerformanceSpanStat]
    counters: PerformanceCounters


class PerformanceDiagnosticsResetRequest(BaseModel):
    """POST /api/diagnostics/performance/reset request.

    Empty object only. The JSON body exists so the request passes the body
    gate; additional fields are not part of the contract.
    """

    model_config = ConfigDict(extra="forbid")


class PerformanceDiagnosticsReset(BaseModel):
    """POST /api/diagnostics/performance/reset body."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["reset"]
