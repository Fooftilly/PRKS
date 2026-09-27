<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { folderLibraryIntentsKey } from './intents'
import {
  effectiveRecentlyAddedRows,
  recentlyAddedMatchesQuery,
  recentlyAddedWorkCardHtml,
} from './legacy-recently-added'
import type { FolderRow, RecentlyAddedWork } from './types'
import { initLazyWorkThumbs, releaseWorkThumbResources } from './work-thumb-lifecycle'

const props = defineProps<{
  folders: readonly FolderRow[]
  filterQuery: string
  works: RecentlyAddedWork[] | null
  offlineCached: boolean
  unavailable: boolean
  loading: boolean
  generation: number
}>()

const intents = inject(folderLibraryIntentsKey)
const collectionEl = ref<HTMLElement | null>(null)

const effectiveRows = computed(() => {
  if (!props.works) return []
  return effectiveRecentlyAddedRows([...props.works])
})

const filtered = computed(() => {
  const q = props.filterQuery.trim()
  if (!q) return effectiveRows.value
  return effectiveRows.value.filter((w) => recentlyAddedMatchesQuery(w, q, props.folders))
})

const collectionClass = computed(() => {
  void props.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function'
    ? fn('prks-folder-library__grid')
    : 'prks-folder-library__grid card-grid'
})

const collectionHtml = computed(() => {
  if (props.unavailable) {
    return '<p class="prks-inline-message">Recently added is not available offline.</p>'
  }
  if (props.loading && !props.works) {
    return '<p class="meta-row" role="status">Loading recently added…</p>'
  }
  if (!filtered.value.length) {
    const q = props.filterQuery.trim()
    if (q) return '<p class="prks-inline-message">No files match your search.</p>'
    return (
      '<div class="prks-folder-tree__empty-state">' +
      '<p class="prks-inline-message">No files in the library yet.</p>' +
      '<button type="button" class="prks-btn prks-btn--primary prks-folder-tree__create-btn" data-prks-role="new-work-from-recently-added">New File</button>' +
      '</div>'
    )
  }
  const cached = props.offlineCached
  return filtered.value.map((w) => recentlyAddedWorkCardHtml(w, cached)).join('')
})

function paintCollection(): void {
  const el = collectionEl.value
  if (!el) return
  releaseWorkThumbResources(el)
  el.innerHTML = collectionHtml.value
  window.prksRefreshIcons?.(el)
  if (!props.offlineCached) {
    initLazyWorkThumbs(el)
  }
}

const debouncedPaint = useDebounceFn(() => {
  paintCollection()
}, 0)

function onCollectionClick(event: MouseEvent): void {
  const btn = (event.target as HTMLElement | null)?.closest('[data-prks-role="new-work-from-recently-added"]')
  if (btn) {
    event.preventDefault()
    intents?.openWorkModal()
  }
}

onMounted(() => {
  paintCollection()
})

onBeforeUnmount(() => {
  releaseWorkThumbResources(collectionEl.value)
})

watch(collectionHtml, () => {
  void debouncedPaint()
}, { flush: 'post' })

defineExpose({ releaseThumbs: () => releaseWorkThumbResources(collectionEl.value) })
</script>

<template>
  <div class="prks-folder-library__scroll prks-folder-library__scroll--added">
    <div
      id="prks-folder-library-recently-added"
      ref="collectionEl"
      :class="collectionClass"
      @click="onCollectionClick"
    ></div>
  </div>
</template>
