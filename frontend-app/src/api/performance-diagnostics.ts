/**
 * Typed client for GET/POST /api/diagnostics/performance.
 * Transport types come from docs/api/openapi-performance-diagnostics.json.
 * Runtime parsers still check response shape. This module does not own query
 * caching, invalidation, or reset behavior.
 */
import { PrksApiError, prksApiRequest } from './http'
import type {
  PerformanceCounters,
  PerformanceDiagnosticsReset,
  PerformanceRequestTotals,
  PerformanceRouteStat,
  PerformanceSnapshot,
  PerformanceSpanStat,
} from './generated/performance-diagnostics'

export type {
  PerformanceCounters,
  PerformanceDiagnosticsReset,
  PerformanceRequestTotals,
  PerformanceRouteStat,
  PerformanceSnapshot,
  PerformanceSpanStat,
}

export const PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION = '0.1.0'

export const PERFORMANCE_SNAPSHOT_KEYS = [
  'process_started_at',
  'uptime_seconds',
  'measured_for_seconds',
  'slow_threshold_ms',
  'requests',
  'routes',
  'spans',
  'counters',
] as const

export const PERFORMANCE_REQUEST_KEYS = ['total', 'slow', 'response_bytes'] as const

export const PERFORMANCE_ROUTE_KEYS = [
  'method',
  'route',
  'count',
  'status_4xx',
  'status_5xx',
  'slow_count',
  'avg_ms',
  'p50_ms',
  'p95_ms',
  'max_ms',
  'avg_db_ms',
  'db_calls',
  'db_calls_avg',
  'measured_db_share_percent',
  'avg_response_bytes',
] as const

export const PERFORMANCE_SPAN_KEYS = ['count', 'avg_ms', 'p50_ms', 'p95_ms', 'max_ms'] as const

export const PERFORMANCE_COUNTER_KEYS = [
  'thumbnail_cache_hits',
  'thumbnail_cache_misses',
  'pdf_file_stat_rows',
  'pdf_file_stat_files',
  'db_read',
  'db_write',
] as const

export const PERFORMANCE_RESET_KEYS = ['status'] as const

const LOAD_ERROR = 'Could not load performance diagnostics.'
const RESET_ERROR = 'Could not reset performance diagnostics.'

function invalid(message: string): PrksApiError {
  return new PrksApiError(message, 200, 'invalid_response')
}

function asRecord(value: unknown, message: string): Record<string, unknown> {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) throw invalid(message)
  return value as Record<string, unknown>
}

function requireKeys(record: Record<string, unknown>, keys: readonly string[], message: string): void {
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) throw invalid(message)
  }
}

function requireNumber(value: unknown, message: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw invalid(message)
  return value
}

function requireNullableNumber(value: unknown, message: string): number | null {
  if (value == null) return null
  return requireNumber(value, message)
}

function requireString(value: unknown, message: string): string {
  if (typeof value !== 'string') throw invalid(message)
  return value
}

function parseRequests(value: unknown): PerformanceRequestTotals {
  const record = asRecord(value, LOAD_ERROR)
  requireKeys(record, PERFORMANCE_REQUEST_KEYS, LOAD_ERROR)
  return {
    total: requireNumber(record.total, LOAD_ERROR),
    slow: requireNumber(record.slow, LOAD_ERROR),
    response_bytes: requireNumber(record.response_bytes, LOAD_ERROR),
  }
}

function parseRoute(value: unknown): PerformanceRouteStat {
  const record = asRecord(value, LOAD_ERROR)
  requireKeys(record, PERFORMANCE_ROUTE_KEYS, LOAD_ERROR)
  return {
    method: requireString(record.method, LOAD_ERROR),
    route: requireString(record.route, LOAD_ERROR),
    count: requireNumber(record.count, LOAD_ERROR),
    status_4xx: requireNumber(record.status_4xx, LOAD_ERROR),
    status_5xx: requireNumber(record.status_5xx, LOAD_ERROR),
    slow_count: requireNumber(record.slow_count, LOAD_ERROR),
    avg_ms: requireNumber(record.avg_ms, LOAD_ERROR),
    p50_ms: requireNullableNumber(record.p50_ms, LOAD_ERROR),
    p95_ms: requireNullableNumber(record.p95_ms, LOAD_ERROR),
    max_ms: requireNumber(record.max_ms, LOAD_ERROR),
    avg_db_ms: requireNumber(record.avg_db_ms, LOAD_ERROR),
    db_calls: requireNumber(record.db_calls, LOAD_ERROR),
    db_calls_avg: requireNumber(record.db_calls_avg, LOAD_ERROR),
    measured_db_share_percent: requireNullableNumber(record.measured_db_share_percent, LOAD_ERROR),
    avg_response_bytes: requireNullableNumber(record.avg_response_bytes, LOAD_ERROR),
  }
}

function parseSpan(value: unknown): PerformanceSpanStat {
  const record = asRecord(value, LOAD_ERROR)
  requireKeys(record, PERFORMANCE_SPAN_KEYS, LOAD_ERROR)
  return {
    count: requireNumber(record.count, LOAD_ERROR),
    avg_ms: requireNumber(record.avg_ms, LOAD_ERROR),
    p50_ms: requireNullableNumber(record.p50_ms, LOAD_ERROR),
    p95_ms: requireNullableNumber(record.p95_ms, LOAD_ERROR),
    max_ms: requireNumber(record.max_ms, LOAD_ERROR),
  }
}

function parseCounters(value: unknown): PerformanceCounters {
  const record = asRecord(value, LOAD_ERROR)
  requireKeys(record, PERFORMANCE_COUNTER_KEYS, LOAD_ERROR)
  return {
    thumbnail_cache_hits: requireNumber(record.thumbnail_cache_hits, LOAD_ERROR),
    thumbnail_cache_misses: requireNumber(record.thumbnail_cache_misses, LOAD_ERROR),
    pdf_file_stat_rows: requireNumber(record.pdf_file_stat_rows, LOAD_ERROR),
    pdf_file_stat_files: requireNumber(record.pdf_file_stat_files, LOAD_ERROR),
    db_read: requireNumber(record.db_read, LOAD_ERROR),
    db_write: requireNumber(record.db_write, LOAD_ERROR),
  }
}

export function parsePerformanceSnapshot(payload: unknown): PerformanceSnapshot {
  const record = asRecord(payload, LOAD_ERROR)
  requireKeys(record, PERFORMANCE_SNAPSHOT_KEYS, LOAD_ERROR)
  if (!Array.isArray(record.routes)) throw invalid(LOAD_ERROR)
  const spansRecord = asRecord(record.spans, LOAD_ERROR)
  const spans: Record<string, PerformanceSpanStat> = {}
  for (const [name, value] of Object.entries(spansRecord)) {
    spans[name] = parseSpan(value)
  }
  return {
    process_started_at: requireNumber(record.process_started_at, LOAD_ERROR),
    uptime_seconds: requireNumber(record.uptime_seconds, LOAD_ERROR),
    measured_for_seconds: requireNumber(record.measured_for_seconds, LOAD_ERROR),
    slow_threshold_ms: requireNumber(record.slow_threshold_ms, LOAD_ERROR),
    requests: parseRequests(record.requests),
    routes: record.routes.map((row) => parseRoute(row)),
    spans,
    counters: parseCounters(record.counters),
  }
}

export function parsePerformanceDiagnosticsReset(payload: unknown): PerformanceDiagnosticsReset {
  const record = asRecord(payload, RESET_ERROR)
  requireKeys(record, PERFORMANCE_RESET_KEYS, RESET_ERROR)
  if (record.status !== 'reset') throw new PrksApiError(RESET_ERROR, 200, 'invalid_response')
  return { status: 'reset' }
}

export async function getPerformanceDiagnostics(signal?: AbortSignal): Promise<PerformanceSnapshot> {
  const payload = await prksApiRequest('/api/diagnostics/performance', { signal })
  return parsePerformanceSnapshot(payload)
}

export async function resetPerformanceDiagnostics(signal?: AbortSignal): Promise<PerformanceDiagnosticsReset> {
  const payload = await prksApiRequest('/api/diagnostics/performance/reset', {
    method: 'POST',
    body: '{}',
    signal,
  })
  return parsePerformanceDiagnosticsReset(payload)
}

export function performanceDiagnosticsErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof PrksApiError && error.message) return error.message
  return fallback
}
