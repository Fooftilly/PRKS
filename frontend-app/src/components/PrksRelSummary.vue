<script setup lang="ts">
import { computed } from 'vue'

/** Plain text parts only. Classic `prksRelSummaryHtml` still owns safe internal links for Work cards. */
export type RelSummaryPart = string | { text: string } | null | undefined | false

const props = defineProps<{
  parts: readonly RelSummaryPart[]
}>()

const visibleParts = computed(() => {
  const out: string[] = []
  for (const part of props.parts) {
    if (part == null || part === false) continue
    if (typeof part === 'string') {
      const text = part.trim()
      if (text) out.push(text)
      continue
    }
    const text = String(part.text || '').trim()
    if (text) out.push(text)
  }
  return out
})
</script>

<template>
  <p v-if="visibleParts.length" class="prks-rel-summary">
    <template v-for="(part, index) in visibleParts" :key="`${index}:${part}`">
      <span v-if="index > 0" class="prks-summary-sep" aria-hidden="true"> · </span>
      {{ part }}
    </template>
  </p>
</template>
