<script setup lang="ts">
import { useEventListener } from '@vueuse/core'
import { computed, inject, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksWorkCard from '../../components/PrksWorkCard.vue'
import { useWorkCardCollection } from '../../components/use-work-card-collection'
import { workCardCollectionFingerprint, workCardThumbOptions } from '../../components/work-card'
import { folderLibraryIntentsKey } from './intents'
import {
  effectiveRecentlyAddedRows,
  recentlyAddedDateLabel,
  recentlyAddedMatchesQuery,
} from './legacy-recently-added'
import type { FolderRow, RecentlyAddedWork } from './types'

const props = defineProps<{
  folders: readonly FolderRow[]
  filterQuery: string
  works: RecentlyAddedWork[] | null
  offlineCached: boolean
  unavailable: boolean
  loading: boolean
  generation: number
  /** Bumped when pending work-metadata overlay changes while mounted. */
  overlayRevision: number
}>()

const intents = inject(folderLibraryIntentsKey)
const collectionEl = ref<HTMLElement | null>(null)

const effectiveRows = computed(() => {
  void props.overlayRevision
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

const waitingFirstFetch = computed(() => props.loading && !props.works)

function cardSubtitle(work: RecentlyAddedWork): string {
  const dateLabel = recentlyAddedDateLabel(work.created_at)
  return dateLabel ? `Added ${dateLabel}` : ''
}

const { release } = useWorkCardCollection(collectionEl, {
  source: () =>
    workCardCollectionFingerprint(filtered.value, { suppressThumbnail: props.offlineCached }),
})

// Generic click listener with VueUse scope cleanup (#233). Preview ownership
// stays in work-thumb-lifecycle / legacy helpers.
useEventListener(collectionEl, 'click', (event: MouseEvent) => {
  const btn = (event.target as HTMLElement | null)?.closest(
    '[data-prks-role="new-work-from-recently-added"]',
  )
  if (btn) {
    event.preventDefault()
    intents?.openWorkModal()
  }
})

defineExpose({ releaseThumbs: () => release() })
</script>

<template>
  <div class="prks-folder-library__scroll prks-folder-library__scroll--added">
    <div
      id="prks-folder-library-recently-added"
      ref="collectionEl"
      :class="collectionClass"
    >
      <PrksInlineMessage v-if="unavailable">Recently added is not available offline.</PrksInlineMessage>
      <template v-else-if="!waitingFirstFetch">
        <PrksWorkCard
          v-for="work in filtered"
          :key="String(work.id ?? '')"
          :work="work"
          :options="workCardThumbOptions(offlineCached, { subtitle: cardSubtitle(work) })"
        />
        <PrksInlineMessage v-if="!filtered.length && filterQuery.trim()">
          No files match your search.
        </PrksInlineMessage>
        <div v-else-if="!filtered.length" class="prks-folder-tree__empty-state">
          <PrksInlineMessage>No files in the library yet.</PrksInlineMessage>
          <PrksButton
            variant="primary"
            class="prks-folder-tree__create-btn"
            data-prks-role="new-work-from-recently-added"
          >
            New File
          </PrksButton>
        </div>
      </template>
    </div>
  </div>
</template>
