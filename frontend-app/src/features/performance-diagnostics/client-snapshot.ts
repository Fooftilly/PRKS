export interface ClientRequestSnapshot {
  counts: {
    started: number
    dedupeJoins: number
    burstCacheHits: number
    retries: number
    aborted: number
  }
  current: {
    activeReads: number
    activeMutation: number
    queuedForegroundReads: number
    queuedBackgroundReads: number
    queuedMutations: number
  }
  peaks: { queuedMutations: number }
  waits: { readAverageMs: number | null }
}

function num(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function nullableNum(value: unknown): number | null {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function readClientRequestSnapshot(): ClientRequestSnapshot | null {
  const read = window.prksRequestCoordinatorSnapshot
  if (typeof read !== 'function') return null
  const raw = read()
  if (!raw || typeof raw !== 'object') return null
  const counts = raw.counts ?? {}
  const current = raw.current ?? {}
  const peaks = raw.peaks ?? {}
  const waits = raw.waits ?? {}
  return {
    counts: {
      started: num(counts.started),
      dedupeJoins: num(counts.dedupeJoins),
      burstCacheHits: num(counts.burstCacheHits),
      retries: num(counts.retries),
      aborted: num(counts.aborted),
    },
    current: {
      activeReads: num(current.activeReads),
      activeMutation: num(current.activeMutation),
      queuedForegroundReads: num(current.queuedForegroundReads),
      queuedBackgroundReads: num(current.queuedBackgroundReads),
      queuedMutations: num(current.queuedMutations),
    },
    peaks: { queuedMutations: num(peaks.queuedMutations) },
    waits: { readAverageMs: nullableNum(waits.readAverageMs) },
  }
}

export function resetClientRequestCoordinator(): void {
  window.prksResetRequestCoordinatorDiagnostics?.()
}

export function clientRequestMaxReads(): number {
  return typeof window.PRKS_REQUEST_MAX_READS === 'number' ? window.PRKS_REQUEST_MAX_READS : 4
}
