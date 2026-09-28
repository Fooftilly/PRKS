<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { ArgumentIndexItem } from './types'

const props = defineProps<{
  item: ArgumentIndexItem
  iconHtml?: string
}>()

const iconHost = ref<HTMLElement | null>(null)
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

function paintIcon(): void {
  const host = iconHost.value
  if (!host) return
  host.innerHTML = props.iconHtml || ''
}

onMounted(() => {
  paintIcon()
})

watch(
  () => props.iconHtml,
  () => {
    paintIcon()
  },
)
</script>

<template>
  <a class="prks-list-row prks-research-row" :href="href">
    <span
      v-if="iconHtml"
      ref="iconHost"
      class="prks-research-row__icon"
      aria-hidden="true"
    ></span>
    <span class="prks-research-row__body">
      <span class="prks-research-row__title-line">
        <span class="prks-research-row__title">{{ item.name || item.id }}</span>
        <span class="prks-research-row__kind">{{ kindLabel }}</span>
      </span>
      <span class="prks-research-row__meta">
        <span v-for="part in meta" :key="part" class="prks-research-row__meta-item">{{ part }}</span>
      </span>
    </span>
  </a>
</template>
