<script setup lang="ts">
defineProps<{
  on: boolean
  labelledBy: string
  describedBy?: string
}>()

defineEmits<{ click: [] }>()
</script>

<template>
  <button
    type="button"
    class="prks-toggle"
    :class="{ 'prks-toggle--on': on }"
    role="switch"
    :aria-checked="on ? 'true' : 'false'"
    :aria-labelledby="labelledBy"
    :aria-describedby="describedBy || undefined"
    @click="$emit('click')"
  >
    <span class="prks-toggle__thumb" aria-hidden="true"></span>
  </button>
</template>
