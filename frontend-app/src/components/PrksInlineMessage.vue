<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'neutral' | 'warning' | 'error'
    /**
     * Announce this message as a status. Only the callers that already used
     * role="status" set this. A silent in-flow note stays silent.
     */
    status?: boolean
  }>(),
  {
    tone: 'neutral',
    status: false,
  },
)
</script>

<template>
  <p
    class="prks-inline-message"
    :class="{ 'prks-inline-message--error': tone === 'error', 'prks-inline-message--warning': tone === 'warning' }"
    :role="status ? 'status' : undefined"
  >
    <slot />
  </p>
</template>
