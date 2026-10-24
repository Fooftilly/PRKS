<script setup lang="ts">
import { ref } from 'vue'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'quiet-danger'
type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    disabled?: boolean
    busy?: boolean
    busyLabel?: string
    type?: 'button' | 'submit' | 'reset'
  }>(),
  {
    variant: 'secondary',
    disabled: false,
    busy: false,
    type: 'button',
  },
)

const emit = defineEmits<{
  click: [event: MouseEvent]
}>()

const root = ref<HTMLButtonElement | null>(null)

function focus(): void {
  root.value?.focus()
}

function onClick(event: MouseEvent): void {
  if (props.disabled || props.busy) {
    event.preventDefault()
    return
  }
  emit('click', event)
}

defineExpose({ focus })
</script>

<template>
  <button
    ref="root"
    :type="type"
    class="prks-btn"
    :class="{
      'prks-btn--primary': variant === 'primary',
      'prks-btn--secondary': variant === 'secondary',
      'prks-btn--ghost': variant === 'ghost',
      'prks-btn--danger': variant === 'danger',
      'prks-btn--quiet-danger': variant === 'quiet-danger',
      'prks-btn--sm': size === 'sm',
      'prks-btn--md': size === 'md',
      'prks-btn--lg': size === 'lg',
    }"
    :disabled="disabled || busy || undefined"
    :aria-busy="busy ? 'true' : undefined"
    @click="onClick"
  >
    <slot v-if="!(busy && busyLabel)" />
    <template v-else>{{ busyLabel }}</template>
  </button>
</template>
