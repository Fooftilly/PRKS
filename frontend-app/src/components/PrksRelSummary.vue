<script setup lang="ts">
import { computed } from 'vue'
import { relSummaryItems, type RelSummaryPart } from './relSummary'

/** Plain text parts. A part may link to a same-app route; anything else stays text. */
const props = defineProps<{
  parts: readonly RelSummaryPart[]
}>()

const visibleParts = computed(() => relSummaryItems(props.parts))
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
