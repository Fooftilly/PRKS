<script setup lang="ts">
import { computed } from 'vue'
import PrksResearchRow from '../../components/PrksResearchRow.vue'
import type { ConceptIndexItem } from './types'

const props = defineProps<{
  item: ConceptIndexItem
  iconHtml?: string
}>()

const href = computed(() => `#/concepts/${encodeURIComponent(props.item.id)}`)

const parentLabel = computed(() => {
  const names = props.item.parents.map((p) => p.name || p.id).filter(Boolean)
  return names.length ? `Parent: ${names.join(', ')}` : 'Top-level concept'
})

const subsLabel = computed(() => {
  const n = props.item.subconcept_count
  return `${n} ${n === 1 ? 'subconcept' : 'subconcepts'}`
})

const notesLabel = computed(() => {
  const n = props.item.mention_count
  return `${n} ${n === 1 ? 'note mention' : 'note mentions'}`
})
</script>

<template>
  <PrksResearchRow
    :href="href"
    :title="item.name || 'Concept'"
    :icon-html="iconHtml"
    :meta="[parentLabel, subsLabel, notesLabel]"
  />
</template>
