import { MutationCache, QueryCache, QueryClient } from '@tanstack/vue-query'
import { noteFinalPrksTransportFailure } from '../api/http'
import { prksQueryRetryDelay, prksQueryShouldRetry } from './retry'

/**
 * One application QueryClient. Disposable server-state only.
 * No persister, no mutation queue, no offline replay.
 *
 * Focus and reconnect do not refetch: PRKS is a local app, and the first
 * migrated surface (performance diagnostics) must keep its snapshot until
 * Refresh or Reset. Individual queries can still opt in later.
 *
 * networkMode is 'always'. Browser online/offline is only a hint in
 * offline-runtime.js; a same-origin PRKS request must run so its success
 * or failure stays authoritative. TanStack 'online' would pause that probe.
 */
export function createPrksQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        noteFinalPrksTransportFailure(error)
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        noteFinalPrksTransportFailure(error)
      },
    }),
    defaultOptions: {
      queries: {
        retry: prksQueryShouldRetry,
        retryDelay: prksQueryRetryDelay,
        staleTime: 30_000,
        gcTime: 5 * 60 * 1000,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        networkMode: 'always',
      },
      mutations: {
        retry: 0,
        networkMode: 'always',
      },
    },
  })
}

let shared: QueryClient | null = null

/**
 * The page's one QueryClient. The Settings app and every route surface use it,
 * so a write in one pane invalidates the same read in another pane.
 * Route surfaces render without an app, so they pass it to `useQuery` directly.
 */
export function prksQueryClient(): QueryClient {
  if (!shared) shared = createPrksQueryClient()
  return shared
}

export function resetPrksQueryClientForTests(): void {
  shared?.clear()
  shared = null
}
