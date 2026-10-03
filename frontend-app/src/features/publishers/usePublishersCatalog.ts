import { computed } from 'vue'
import { useQuery, type QueryClient } from '@tanstack/vue-query'
import { isAbortError } from '../../api/http'
import { listPublishersInUse } from '../../api/publishers'
import { prksQueryClient } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'
import { publisherRows } from './projection'

const LOAD_ERROR = 'Could not load publishers.'
const REFRESH_ERROR = 'Could not refresh publishers.'

/**
 * The Publishers list as server state. Every pane on `#/publishers` shares one
 * read. Each mount refetches, so Work edits made elsewhere show up the way the
 * old route reload did, while the cached list paints at once. A failed refetch
 * keeps the rows on screen and reports itself; it never paints the empty state.
 */
export function usePublishersCatalog(queryClient: QueryClient = prksQueryClient()) {
  const query = useQuery(
    {
      queryKey: prksQueryKeys.publishers.inUse(),
      queryFn: ({ signal }) => listPublishersInUse(signal),
      refetchOnMount: 'always',
    },
    queryClient,
  )

  const rows = computed(() => publisherRows(query.data.value ?? []))
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
