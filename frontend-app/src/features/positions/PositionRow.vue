<script setup lang="ts">
import { computed } from 'vue'
import PrksResearchRow from '../../components/PrksResearchRow.vue'
import { positionDescriptionExcerpt } from './match'
import type { PositionIndexItem } from './types'

const props = defineProps<{
  item: PositionIndexItem
  iconHtml?: string
}>()

const href = computed(() => `#/positions/${encodeURIComponent(props.item.id)}`)
const excerpt = computed(() => positionDescriptionExcerpt(props.item.description))
const meta = computed(() => (excerpt.value ? [excerpt.value] : []))
</script>

<template>
  <PrksResearchRow
    :href="href"
    :title="item.name || 'Position'"
    :icon-html="iconHtml"
    :meta="meta"
  />
</template>
