<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { positionDescriptionExcerpt } from './match'
import type { PositionIndexItem } from './types'

const props = defineProps<{
  item: PositionIndexItem
  iconHtml?: string
}>()

const iconHost = ref<HTMLElement | null>(null)

const href = computed(() => `#/positions/${encodeURIComponent(props.item.id)}`)
const excerpt = computed(() => positionDescriptionExcerpt(props.item.description))

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
        <span class="prks-research-row__title">{{ item.name || 'Position' }}</span>
      </span>
      <span v-if="excerpt" class="prks-research-row__meta">
        <span class="prks-research-row__meta-item">{{ excerpt }}</span>
      </span>
    </span>
  </a>
</template>
