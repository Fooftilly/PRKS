<script setup lang="ts">
import { computed, ref } from 'vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksWorkCard from '../../components/PrksWorkCard.vue'
import { useWorkCardCollection } from '../../components/use-work-card-collection'
import { workCardCollectionFingerprint } from '../../components/work-card'
import { searchResultSubtitle } from './search-result-subtitle'
import type { SearchResultsProjection } from './types'

const props = defineProps<{
  projection: SearchResultsProjection
}>()

const collectionEl = ref<HTMLElement | null>(null)
const rows = computed(() => props.projection.rows)
const collectionClass = computed(() => {
  void props.projection.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn() : 'card-grid'
})

useWorkCardCollection(collectionEl, {
  source: () => workCardCollectionFingerprint(rows.value),
})
</script>

<template>
  <div ref="collectionEl" :class="collectionClass" data-prks-search-results>
    <PrksInlineMessage v-if="!rows.length">{{ projection.emptyMessage }}</PrksInlineMessage>
    <PrksWorkCard
      v-for="work in rows"
      :key="String(work.id ?? '')"
      :work="work"
      :options="{ subtitle: searchResultSubtitle(work) }"
    />
  </div>
</template>
