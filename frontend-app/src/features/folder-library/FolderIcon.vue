<script setup lang="ts">
/**
 * Lucide placeholder as an element, not an HTML string. `prksRefreshIcons`
 * replaces `[data-lucide]` under the owning tree host after paint.
 * `name` is the Lucide icon name (`chevron-right`), not a PRKS alias.
 */
defineProps<{
  name: string
  size?: number | 'sm'
}>()
</script>

<template>
  <i
    :data-lucide="name"
    class="prks-icon"
    :class="{ 'prks-icon--sm': size === 'sm' }"
    :style="typeof size === 'number' ? { '--prks-icon-size': `${size}px` } : undefined"
    aria-hidden="true"
  ></i>
</template>
