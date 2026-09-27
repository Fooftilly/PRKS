import { ref, watch } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  getPerformanceDiagnostics,
  performanceDiagnosticsErrorMessage,
  resetPerformanceDiagnostics,
} from '../../api/performance-diagnostics'
import { isAbortError } from '../../api/http'
import { performanceDiagnosticsEnabled } from './activation'
import { readClientRequestSnapshot, resetClientRequestCoordinator, type ClientRequestSnapshot } from './client-snapshot'

/** Domain query key. Not a URL. */
export const performanceDiagnosticsQueryKey = ['performance-diagnostics'] as const

const LOAD_ERROR = 'Could not load performance diagnostics.'
const RESET_ERROR = 'Could not reset measurements.'

/**
 * Server snapshot is TanStack Query state (stale until Refresh or Reset).
 * The client-coordinator paragraph is local process state captured when a
 * server read settles. It is not a second cache of the diagnostics response.
 */
export function usePerformanceDiagnostics() {
  const queryClient = useQueryClient()
  const clientSnapshot = ref<ClientRequestSnapshot | null>(null)
  const statusText = ref('')
  const enabled = performanceDiagnosticsEnabled()

  const query = useQuery({
    queryKey: performanceDiagnosticsQueryKey,
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 30 * 60 * 1000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async ({ signal }) => {
      const snapshot = await getPerformanceDiagnostics(signal)
      clientSnapshot.value = readClientRequestSnapshot()
      return snapshot
    },
  })

  watch(query.error, (error) => {
    if (!error || isAbortError(error)) return
    statusText.value = performanceDiagnosticsErrorMessage(error, LOAD_ERROR)
  })

  async function reloadFromServer(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: performanceDiagnosticsQueryKey })
  }

  const resetMutation = useMutation({
    retry: 0,
    mutationFn: resetPerformanceDiagnostics,
    onSuccess: async () => {
      resetClientRequestCoordinator()
      await reloadFromServer()
    },
  })

  function applyLoadError(error: unknown, fallback: string): void {
    if (isAbortError(error)) return
    statusText.value = performanceDiagnosticsErrorMessage(error, fallback)
  }

  async function refresh(): Promise<void> {
    statusText.value = ''
    try {
      await reloadFromServer()
    } catch (error) {
      applyLoadError(error, LOAD_ERROR)
      return
    }
    if (query.isError.value) applyLoadError(query.error.value, LOAD_ERROR)
  }

  async function resetMeasurements(): Promise<void> {
    statusText.value = ''
    try {
      await resetMutation.mutateAsync()
    } catch (error) {
      applyLoadError(error, RESET_ERROR)
      return
    }
    if (query.isError.value) {
      applyLoadError(query.error.value, RESET_ERROR)
      return
    }
    statusText.value = 'Measurements reset.'
  }

  return {
    data: query.data,
    isPending: query.isPending,
    isError: query.isError,
    clientSnapshot,
    statusText,
    refresh,
    resetMeasurements,
  }
}
