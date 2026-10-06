<script setup lang="ts">
type Variant = 'secondary'

withDefaults(
  defineProps<{
    expanded: boolean
    controls?: string
    variant?: Variant
  }>(),
  {
    variant: 'secondary',
  },
)

const emit = defineEmits<{
  click: [event: MouseEvent]
}>()

function onClick(event: MouseEvent): void {
  emit('click', event)
}
</script>

<template>
  <button
    type="button"
    class="prks-btn"
    :class="{ 'prks-btn--secondary': variant === 'secondary' }"
    :aria-expanded="expanded ? 'true' : 'false'"
    :aria-controls="controls || undefined"
    @click="onClick"
  >
    <slot />
  </button>
</template>
