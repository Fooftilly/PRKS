import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from './http'
import {
  PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION,
  getPerformanceDiagnostics,
  parsePerformanceSnapshot,
  resetPerformanceDiagnostics,
} from './performance-diagnostics'

export function performanceSnapshotFixture() {
  return {
    process_started_at: 1_700_000_000,
    uptime_seconds: 12,
    measured_for_seconds: 4,
    slow_threshold_ms: 250,
    requests: { total: 2, slow: 0, response_bytes: 80 },
    routes: [
      {
        method: 'GET',
        route: '/api/works',
        count: 2,
        status_4xx: 0,
        status_5xx: 0,
        slow_count: 0,
        avg_ms: 3.5,
        p50_ms: 3,
        p95_ms: 4,
        max_ms: 4,
        avg_db_ms: 1.2,
        db_calls: 2,
        db_calls_avg: 1,
        measured_db_share_percent: 40,
        avg_response_bytes: 40,
      },
    ],
    spans: {
      json_encode: { count: 1, avg_ms: 0.4, p50_ms: 0.4, p95_ms: 0.4, max_ms: 0.4 },
    },
    counters: {
      thumbnail_cache_hits: 1,
      thumbnail_cache_misses: 1,
      pdf_file_stat_rows: 0,
      pdf_file_stat_files: 0,
      db_read: 2,
      db_write: 0,
    },
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('performance diagnostics contract client', () => {
  it('names the OpenAPI document version', () => {
    expect(PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION).toBe('0.1.0')
  })

  it('parses a snapshot and rejects a missing required key', () => {
    const parsed = parsePerformanceSnapshot(performanceSnapshotFixture())
    expect(parsed.routes[0]?.route).toBe('/api/works')
    const broken = performanceSnapshotFixture()
    delete (broken as { counters?: unknown }).counters
    expect(() => parsePerformanceSnapshot(broken)).toThrow(PrksApiError)
  })

  it('loads and resets through the typed paths', async () => {
    const calls: Array<{ url: string; method: string; body?: string }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? String(init.body) : undefined })
        if ((init?.method ?? 'GET') === 'POST') {
          return new Response(JSON.stringify({ status: 'reset' }), { status: 200 })
        }
        return new Response(JSON.stringify(performanceSnapshotFixture()), { status: 200 })
      }),
    )
    const snapshot = await getPerformanceDiagnostics()
    expect(snapshot.requests.total).toBe(2)
    const reset = await resetPerformanceDiagnostics()
    expect(reset.status).toBe('reset')
    expect(calls.map((call) => call.url)).toEqual([
      '/api/diagnostics/performance',
      '/api/diagnostics/performance/reset',
    ])
    expect(calls[1]?.method).toBe('POST')
    expect(calls[1]?.body).toBe('{}')
  })
})
