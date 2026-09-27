<script setup lang="ts">
import { computed } from 'vue'
import { vOwnedClass } from './owned-class'

/**
 * Presentational separator. Pointer, keyboard, and ARIA values are bound by
 * workspace-split.js. Ratio changes are coordinator commits, not local state.
 * Axis classes are toggled without replacing `className`, so `is-dragging`
 * from workspace-split.js survives a shell patch.
 */
const props = defineProps<{
  axis: 'left-right' | 'top-bottom'
  splitId?: string
  root?: boolean
}>()

const owned = computed(() => ({
  'prks-splitter--horizontal': props.axis === 'top-bottom',
  'prks-splitter--vertical': props.axis !== 'top-bottom',
}))
</script>

<template>
  <div
    class="prks-splitter"
    role="separator"
    tabindex="0"
    :aria-label="root ? 'Resize split view' : 'Resize split pane'"
    :aria-orientation="axis === 'top-bottom' ? 'horizontal' : 'vertical'"
    :data-prks-split-id="splitId"
    v-owned-class="owned"
  ></div>
</template>
