import { PrksApiError, isAbortError } from '../api/http'

/** Initial attempt plus two retries. Matches the legacy safe-GET retry count. */
export const PRKS_QUERY_MAX_RETRIES = 2

const RETRYABLE_STATUS = new Set([502, 503, 504])

/**
 * TanStack calls this with the failure count so far, starting at 0.
 * Returning true schedules another attempt.
 */
export function prksQueryShouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= PRKS_QUERY_MAX_RETRIES) return false
  if (isAbortError(error)) return false
  if (error instanceof PrksApiError) return RETRYABLE_STATUS.has(error.status)
  return error instanceof TypeError
}

export function prksQueryRetryDelay(failureCount: number): number {
  return Math.min(1000 * 2 ** failureCount, 4000)
}
