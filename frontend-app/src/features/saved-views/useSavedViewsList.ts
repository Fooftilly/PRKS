import { computed } from 'vue'
import { useQuery, type QueryClient } from '@tanstack/vue-query'
import { isAbortError } from '../../api/http'
import { listSavedViews } from '../../api/saved-views'
import { prksQueryClient } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'
import { savedViewIndexRows } from './projection'
import { SAVED_VIEWS_READ_META } from './records'

const LOAD_ERROR = 'Could not load Saved Views.'
const REFRESH_ERROR = 'Could not refresh Saved Views.'

/**
 * The Saved Views list as server state. Every pane on `#/views` shares one
 * read with the command palette. Each mount refetches, while the cached list
 * paints at once. A failed refetch keeps the rows on screen and reports
 * itself; it never paints the empty state.
 */
export function useSavedViewsList(queryClient: QueryClient = prksQueryClient()) {
  const query = useQuery(
    {
      queryKey: prksQueryKeys.savedViews.list(),
      queryFn: ({ signal }) => listSavedViews(signal),
      refetchOnMount: 'always',
      meta: SAVED_VIEWS_READ_META,
    },
    queryClient,
  )

  const rows = computed(() => savedViewIndexRows(query.data.value ?? []))
  const loaded = computed(() => query.data.value !== undefined)
  const failed = computed(() => query.isError.value && !isAbortError(query.error.value))
  const loadError = computed(() => (failed.value && !loaded.value ? LOAD_ERROR : ''))
  const refreshError = computed(() => (failed.value && loaded.value ? REFRESH_ERROR : ''))
  const loading = computed(() => !loaded.value && !failed.value)

  function retry(): void {
    void query.refetch()
  }

  return { rows, loaded, loading, loadError, refreshError, retry }
}
