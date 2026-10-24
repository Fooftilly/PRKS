<script setup lang="ts">
defineProps<{
  kind: 'loading' | 'error' | 'empty'
  heading?: string
  message?: string
}>()
</script>

<template>
  <div
    class="prks-state"
    :class="`prks-state--${kind}`"
    :role="kind === 'empty' ? undefined : 'status'"
  >
    <p v-if="heading" class="prks-state__heading">{{ heading }}</p>
    <p v-if="message" class="prks-state__body">{{ message }}</p>
    <slot />
  </div>
</template>
