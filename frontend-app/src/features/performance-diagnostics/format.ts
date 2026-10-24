import type { PerformanceSnapshot, PerformanceSpanStat } from '../../api/performance-diagnostics'
import { clientRequestMaxReads, type ClientRequestSnapshot } from './client-snapshot'

const SPAN_LABELS: ReadonlyArray<readonly [string, string]> = [
  ['pdf_file_stats', 'PDF file stats'],
  ['processing_scan', 'Processing scan'],
  ['pdf_text_search', 'PDF text search'],
  ['thumbnail_render', 'Thumbnail render'],
  ['json_encode', 'JSON encode'],
  ['gzip', 'Gzip'],
  ['portrait_fetch', 'Portrait fetch'],
  ['text_index_reconcile', 'Text index reconcile'],
  ['text_index_load_state', 'Text index load state'],
  ['text_index_source_scan', 'Text index source scan'],
  ['text_index_extract', 'Text index extract'],
  ['text_index_write', 'Text index write'],
  ['text_index_fts_verify', 'Text index FTS verify'],
  ['backup_create', 'Backup create'],
  ['restore_commit', 'Restore commit'],
  ['pdf_linearize', 'PDF linearize'],
]

export function formatPerfSeconds(value: number | null | undefined): string {
  const seconds = Math.max(0, Math.floor(Number(value) || 0))
  if (seconds < 60) return seconds + 's'
  if (seconds < 3600) return Math.floor(seconds / 60) + ' min'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return minutes ? hours + 'h ' + minutes + 'm' : hours + 'h'
}

export function formatPerfMs(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const n = Number(value)
  if (n >= 10) return String(Math.round(n))
  return n.toFixed(1)
}

export function routeLabel(row: { method: string; route: string }): string {
  return String(row.method || '') + ' ' + String(row.route || '')
}

export function dbCallsPerRequest(row: {
  count: number
  db_calls: number
  db_calls_avg: number | null
}): number {
  if (row.db_calls_avg != null) return row.db_calls_avg
  return row.count ? Number(row.db_calls || 0) / row.count : 0
}

export function formatSummary(snap: PerformanceSnapshot): string {
  const total = snap.requests?.total || 0
  const slow = snap.requests?.slow || 0
  return (
    'Measured for: ' +
    formatPerfSeconds(snap.measured_for_seconds) +
    '. API requests: ' +
    String(total) +
    '. Slow requests (>' +
    formatPerfMs(snap.slow_threshold_ms) +
    ' ms): ' +
    String(slow) +
    '.'
  )
}

export function formatSpans(spans: PerformanceSnapshot['spans'] | undefined): string {
  const parts: string[] = []
  for (const [key, label] of SPAN_LABELS) {
    const span: PerformanceSpanStat | undefined = spans?.[key]
    if (!span || !span.count) continue
    parts.push(label + ' avg ' + formatPerfMs(span.avg_ms) + ' ms')
  }
  return parts.length ? 'Subsystems: ' + parts.join('. ') + '.' : ''
}

export function formatThumbnailCache(counters: PerformanceSnapshot['counters'] | undefined): string {
  const hits = Number(counters?.thumbnail_cache_hits || 0)
  const misses = Number(counters?.thumbnail_cache_misses || 0)
  const total = hits + misses
  if (!total) return 'Thumbnail cache: no thumbnail requests yet.'
  const rate = Math.round((1000 * hits) / total) / 10
  return 'Thumbnail cache: ' + hits + ' hits / ' + misses + ' misses — ' + rate + '% hit rate.'
}

export function formatClientRequestBody(
  client: ClientRequestSnapshot | null,
  maxReads = clientRequestMaxReads(),
): string {
  if (!client) return 'Client request coordinator: no measurements yet.'
  const counts = client.counts
  const current = client.current
  const peaks = client.peaks
  const waits = client.waits
  const queuedReads = current.queuedForegroundReads + current.queuedBackgroundReads
  return (
    'Client requests: ' +
    String(counts.started) +
    '. Network requests avoided: ' +
    String(counts.dedupeJoins) +
    ' in-flight deduplicated, ' +
    String(counts.burstCacheHits) +
    ' burst-cache hits. Retries: ' +
    String(counts.retries) +
    '. Aborted obsolete reads: ' +
    String(counts.aborted) +
    '. Now: Reads ' +
    String(current.activeReads) +
    '/' +
    String(maxReads) +
    ', Mutations ' +
    String(current.activeMutation) +
    '/1, Queued reads ' +
    String(queuedReads) +
    ', Queued mutations ' +
    String(current.queuedMutations) +
    '. Peak mutation queue: ' +
    String(peaks.queuedMutations) +
    '. Average read queue wait: ' +
    formatPerfMs(waits.readAverageMs) +
    ' ms.'
  )
}

export function formatClientRequestReport(
  client: ClientRequestSnapshot | null,
  maxReads = clientRequestMaxReads(),
): string {
  if (!client) return ''
  const counts = client.counts
  const current = client.current
  const avoided = counts.dedupeJoins + counts.burstCacheHits
  const queuedReads = current.queuedForegroundReads + current.queuedBackgroundReads
  return [
    'Client request coordinator',
    'Client requests: ' + String(counts.started),
    'Network requests avoided: ' +
      String(avoided) +
      ' (deduped ' +
      String(counts.dedupeJoins) +
      ', cache ' +
      String(counts.burstCacheHits) +
      ')',
    'Retries: ' + String(counts.retries),
    'Aborted obsolete reads: ' + String(counts.aborted),
    'Now: reads ' +
      String(current.activeReads) +
      '/' +
      String(maxReads) +
      ', mutations ' +
      String(current.activeMutation) +
      '/1, queued reads ' +
      String(queuedReads) +
      ', queued mutations ' +
      String(current.queuedMutations),
    'Peak mutation queue: ' + String(client.peaks.queuedMutations),
    'Average read queue wait: ' + formatPerfMs(client.waits.readAverageMs) + ' ms',
  ].join('\n')
}

export function formatPerformanceReport(
  snap: PerformanceSnapshot | null | undefined,
  client: ClientRequestSnapshot | null,
): string {
  if (!snap) return 'PRKS performance report\nNo data.'
  const lines = [
    'PRKS performance report',
    'Measurement window: ' + formatPerfSeconds(snap.measured_for_seconds),
    'Slow threshold: ' + formatPerfMs(snap.slow_threshold_ms) + 'ms',
    'Requests: ' + String(snap.requests?.total || 0),
    'Slow: ' + String(snap.requests?.slow || 0),
    '',
  ]
  for (const row of snap.routes) {
    lines.push(
      routeLabel(row) +
        ' calls=' +
        String(row.count || 0) +
        ' avg=' +
        formatPerfMs(row.avg_ms) +
        'ms' +
        ' p95=' +
        formatPerfMs(row.p95_ms) +
        'ms' +
        ' max=' +
        formatPerfMs(row.max_ms) +
        'ms' +
        ' db_avg=' +
        formatPerfMs(row.avg_db_ms) +
        'ms' +
        ' db_share=' +
        (row.measured_db_share_percent == null ? '—' : String(row.measured_db_share_percent) + '%') +
        ' db_calls=' +
        formatPerfMs(dbCallsPerRequest(row)) +
        '/call',
    )
  }
  const spanNames = Object.keys(snap.spans || {})
  if (spanNames.length) {
    lines.push('')
    for (const name of spanNames) {
      const span = snap.spans[name]
      if (!span) continue
      lines.push(
        name +
          ' count=' +
          String(span.count || 0) +
          ' avg=' +
          formatPerfMs(span.avg_ms) +
          'ms' +
          ' p95=' +
          formatPerfMs(span.p95_ms) +
          'ms',
      )
    }
  }
  const hits = Number(snap.counters?.thumbnail_cache_hits || 0)
  const misses = Number(snap.counters?.thumbnail_cache_misses || 0)
  const total = hits + misses
  const rate = total ? Math.round((1000 * hits) / total) / 10 : null
  lines.push('')
  lines.push(
    'Thumbnail cache: ' + hits + ' hits / ' + misses + ' misses' + (rate == null ? '' : ' — ' + rate + '% hit rate'),
  )
  const clientText = formatClientRequestReport(client)
  if (clientText) {
    lines.push('')
    lines.push(clientText)
  }
  return lines.join('\n')
}
