<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { ConceptIndexItem } from './types'

const props = defineProps<{
  item: ConceptIndexItem
  iconHtml?: string
}>()

const iconHost = ref<HTMLElement | null>(null)

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

onMounted(() => {
  const host = iconHost.value
  if (host && props.iconHtml) host.innerHTML = props.iconHtml
})
</script>

<template>
  <a class="prks-list-row prks-research-row" :href="href">
    <span ref="iconHost" class="prks-research-row__icon" aria-hidden="true"></span>
    <span class="prks-research-row__body">
      <span class="prks-research-row__title-line">
        <span class="prks-research-row__title">{{ item.name || 'Concept' }}</span>
      </span>
      <span class="prks-research-row__meta">
        <span class="prks-research-row__meta-item">{{ parentLabel }}</span>
        <span class="prks-research-row__meta-item">{{ subsLabel }}</span>
        <span class="prks-research-row__meta-item">{{ notesLabel }}</span>
      </span>
    </span>
  </a>
</template>
