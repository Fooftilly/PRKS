<script setup lang="ts">
import { computed } from 'vue'

type Variant = 'default' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    /** Accessible name. Required. Shown as aria-label, not as visible text. */
    label: string
    title?: string
    variant?: Variant
    size?: Size
    disabled?: boolean
    busy?: boolean
    /** Replaces the icon and the accessible name while busy. */
    busyLabel?: string
  }>(),
  {
    variant: 'default',
    disabled: false,
    busy: false,
  },
)

const emit = defineEmits<{
  click: [event: MouseEvent]
}>()

const accessibleName = computed(() =>
  props.busy && props.busyLabel ? props.busyLabel : props.label,
)

function onClick(event: MouseEvent): void {
  if (props.disabled || props.busy) {
    event.preventDefault()
    return
  }
  emit('click', event)
}
</script>

<template>
  <button
    type="button"
    class="prks-icon-btn"
    :class="{
      'prks-icon-btn--ghost': variant === 'ghost',
      'prks-icon-btn--danger': variant === 'danger',
      'prks-icon-btn--sm': size === 'sm',
      'prks-icon-btn--md': size === 'md',
      'prks-icon-btn--lg': size === 'lg',
    }"
    :aria-label="accessibleName"
    :title="title"
    :disabled="disabled || busy || undefined"
    :aria-busy="busy ? 'true' : undefined"
    @click="onClick"
  >
    <slot v-if="!(busy && busyLabel)" />
    <template v-else>{{ busyLabel }}</template>
  </button>
</template>
