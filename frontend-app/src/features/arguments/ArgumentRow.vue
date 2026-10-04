<script setup lang="ts">
import { computed } from 'vue'
import PrksResearchRow from '../../components/PrksResearchRow.vue'
import type { ArgumentIndexItem } from './types'

const props = defineProps<{
  item: ArgumentIndexItem
  iconHtml?: string
}>()

const href = computed(() => `#/arguments/${encodeURIComponent(props.item.id)}`)
const kindLabel = computed(() => (props.item.kind === 'stance' ? 'Stance' : 'Argument'))
const meta = computed(() => {
  const responses = props.item.response_count
  const targets = props.item.targets.length
  const sources = props.item.sources.length
  return [
    `${responses} ${responses === 1 ? 'response' : 'responses'}`,
    `${targets} ${targets === 1 ? 'target' : 'targets'}`,
    `${sources} ${sources === 1 ? 'source' : 'sources'}`,
  ]
})
</script>

<template>
  <PrksResearchRow
    :href="href"
    :title="item.name || item.id"
    :kind="kindLabel"
    :icon-html="iconHtml"
    :meta="meta"
  />
</template>
