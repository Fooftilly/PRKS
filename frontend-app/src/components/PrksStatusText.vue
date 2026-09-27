<script setup lang="ts">
withDefaults(
  defineProps<{
    live?: boolean
  }>(),
  { live: true },
)
</script>

<template>
  <p class="prks-settings-hint" :aria-live="live ? 'polite' : undefined">
    <slot />
  </p>
</template>
