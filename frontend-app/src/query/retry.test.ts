import { describe, expect, it } from 'vitest'
import { PrksApiError } from '../api/http'
import { prksQueryRetryDelay, prksQueryShouldRetry } from './retry'

describe('prksQueryShouldRetry', () => {
  it('retries network and 502/503/504 twice, then stops', () => {
    const network = new TypeError('Failed to fetch')
    expect(prksQueryShouldRetry(0, network)).toBe(true)
    expect(prksQueryShouldRetry(1, new PrksApiError('down', 503, null))).toBe(true)
    expect(prksQueryShouldRetry(2, new PrksApiError('down', 504, null))).toBe(false)
    expect(prksQueryShouldRetry(0, new PrksApiError('no', 400, 'invalid_request'))).toBe(false)
    expect(prksQueryShouldRetry(0, new PrksApiError('no', 500, null))).toBe(false)
  })

  it('does not retry cancellation', () => {
    expect(prksQueryShouldRetry(0, new DOMException('Aborted', 'AbortError'))).toBe(false)
  })

  it('backs off without growing forever', () => {
    expect(prksQueryRetryDelay(0)).toBe(1000)
    expect(prksQueryRetryDelay(1)).toBe(2000)
    expect(prksQueryRetryDelay(8)).toBe(4000)
  })
})
