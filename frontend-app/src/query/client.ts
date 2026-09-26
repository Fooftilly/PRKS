import { QueryClient } from '@tanstack/vue-query'
import { prksQueryRetryDelay, prksQueryShouldRetry } from './retry'

/**
 * One application QueryClient. Disposable server-state only.
 * No persister, no mutation queue, no offline replay.
 *
 * Focus and reconnect do not refetch: PRKS is a local app, and the first
 * migrated surface (performance diagnostics) must keep its snapshot until
 * Refresh or Reset. Individual queries can still opt in later.
 */
export function createPrksQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: prksQueryShouldRetry,
        retryDelay: prksQueryRetryDelay,
        staleTime: 30_000,
        gcTime: 5 * 60 * 1000,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        networkMode: 'online',
      },
      mutations: {
        retry: 0,
        networkMode: 'online',
      },
    },
  })
}
