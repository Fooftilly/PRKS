import { afterEach, describe, expect, it } from 'vitest'
import type { ClientRequestSnapshot } from './client-snapshot'
import { formatClientRequestBody, formatClientRequestReport } from './format'

const client: ClientRequestSnapshot = {
  counts: {
    started: 1,
    dedupeJoins: 0,
    burstCacheHits: 0,
    retries: 0,
    aborted: 0,
  },
  current: {
    activeReads: 2,
    activeMutation: 0,
    queuedForegroundReads: 0,
    queuedBackgroundReads: 0,
    queuedMutations: 0,
  },
  peaks: { queuedMutations: 0 },
  waits: { readAverageMs: null },
}

afterEach(() => {
  delete window.PRKS_REQUEST_MAX_READS
})

describe('formatClientRequestReport', () => {
  it('uses the configured read limit in the copied report', () => {
    window.PRKS_REQUEST_MAX_READS = 8
    expect(formatClientRequestBody(client)).toContain('Reads 2/8')
    expect(formatClientRequestReport(client)).toContain('reads 2/8')
    expect(formatClientRequestReport(client)).not.toContain('/4')
  })

  it('falls back to 4 when no limit is configured', () => {
    expect(formatClientRequestReport(client)).toContain('reads 2/4')
    expect(formatClientRequestBody(client)).toContain('Reads 2/4')
  })
})
