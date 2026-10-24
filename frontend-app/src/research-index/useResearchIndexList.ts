import { computed, nextTick, onMounted, ref, watch, type ComputedRef, type Ref } from 'vue'

/**
 * Local search, header icon, and scope counts for a research index.
 * Filtering stays in the caller. This does not own route or durable state.
 */
export function useResearchIndexList<T>(options: {
  items: ComputedRef<readonly T[]>
  unavailable: ComputedRef<boolean>
  generation: ComputedRef<number>
  filter: (items: readonly T[], query: string) => readonly T[]
  normalizeQuery: (query: string) => string
  icon: string
  /** Plain string, or a ref when the label changes with the same mounted list. */
  scopeLabel: string | ComputedRef<string>
  rootEl: Ref<HTMLElement | null>
  titleIconHost: Ref<HTMLElement | null>
  searchInput: Ref<HTMLInputElement | null>
}) {
  const searchQuery = ref('')
  const { rootEl, titleIconHost, searchInput } = options

  const filtered = computed(() => options.filter(options.items.value, searchQuery.value))
  const normalizedQuery = computed(() => options.normalizeQuery(searchQuery.value))
  const showToolbar = computed(() => !options.unavailable.value && options.items.value.length > 0)
  const showEmptyData = computed(
    () => !options.unavailable.value && !filtered.value.length && !normalizedQuery.value,
  )
  const showSearchEmpty = computed(
    () => !options.unavailable.value && !filtered.value.length && !!normalizedQuery.value,
  )

  const rowIconHtml = computed(() => {
    void options.generation.value
    return typeof window.prksIcon === 'function' ? window.prksIcon(options.icon, { size: 'sm' }) : ''
  })

  const scopeLine = computed(() => ({
    shown: filtered.value.length,
    total: options.items.value.length,
    filter: normalizedQuery.value,
    label: typeof options.scopeLabel === 'string' ? options.scopeLabel : options.scopeLabel.value,
  }))

  function paintTitleIcon(): void {
    const host = titleIconHost.value
    if (!host) return
    host.innerHTML =
      typeof window.prksPageHeaderIconHtml === 'function'
        ? window.prksPageHeaderIconHtml(options.icon)
        : ''
  }

  function refreshIcons(): void {
    const root = rootEl.value
    if (root && typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(root)
  }

  function clearSearch(): void {
    searchQuery.value = ''
    // Match legacy research-index Clear: restore focus to the live search input.
    void nextTick(() => {
      searchInput.value?.focus()
    })
  }

  onMounted(() => {
    paintTitleIcon()
    refreshIcons()
  })

  watch(
    () =>
      [
        options.generation.value,
        filtered.value.length,
        normalizedQuery.value,
        options.items.value.length,
      ] as const,
    async () => {
      await nextTick()
      refreshIcons()
    },
  )

  return {
    searchQuery,
    filtered,
    normalizedQuery,
    showToolbar,
    showEmptyData,
    showSearchEmpty,
    rowIconHtml,
    scopeLine,
    clearSearch,
  }
}
