<script setup lang="ts">
import { computed } from 'vue'
import { scopeLineParts, type ScopeLineOptions } from './scopeLine'

const props = defineProps<ScopeLineOptions & { text?: string }>()

const parts = computed(() => {
  if (props.text != null && props.text !== '') return [props.text]
  return scopeLineParts(props)
})
</script>

<template>
  <p v-if="parts.length" class="prks-scope-line" role="status"><template v-for="(part, index) in parts" :key="part"><span v-if="index > 0" class="prks-summary-sep" aria-hidden="true"> · </span>{{ part }}</template></p>
</template>
