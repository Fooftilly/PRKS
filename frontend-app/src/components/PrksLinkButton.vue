<script setup lang="ts">
type Variant = 'secondary'
type Size = 'sm' | 'md'

withDefaults(
  defineProps<{
    href: string
    variant?: Variant
    size?: Size
  }>(),
  {
    variant: 'secondary',
  },
)
</script>

<template>
  <a
    class="prks-btn"
    :class="{
      'prks-btn--secondary': variant === 'secondary',
      'prks-btn--sm': size === 'sm',
      'prks-btn--md': size === 'md',
    }"
    :href="href"
  >
    <slot />
  </a>
</template>
