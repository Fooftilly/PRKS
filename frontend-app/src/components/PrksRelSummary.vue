<script setup lang="ts">
import { computed } from 'vue'

export type RelSummaryPart = string | { text: string; href?: string } | null | undefined | false

const props = defineProps<{
  parts: readonly RelSummaryPart[]
}>()

const visibleParts = computed(() => {
  const out: { text: string; href?: string }[] = []
  for (const part of props.parts) {
    if (part == null || part === false) continue
    if (typeof part === 'string') {
      const text = part.trim()
      if (text) out.push({ text })
      continue
    }
    const text = String(part.text || '').trim()
    if (text) out.push({ text, href: part.href })
  }
  return out
})
</script>

<template>
  <p v-if="visibleParts.length" class="prks-rel-summary">
    <template v-for="(part, index) in visibleParts" :key="`${index}:${part.text}`">
      <span v-if="index > 0" class="prks-summary-sep" aria-hidden="true"> · </span>
      <a v-if="part.href" class="prks-summary-link" :href="part.href">{{ part.text }}</a>
      <template v-else>{{ part.text }}</template>
    </template>
  </p>
</template>
