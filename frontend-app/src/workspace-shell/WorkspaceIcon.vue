<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

const props = defineProps<{
  name: string
  svgClass?: string
}>()

const el = ref<HTMLElement | null>(null)

function paint(): void {
  const node = el.value
  if (!node) return
  if (node.getAttribute('data-icon') === props.name && node.innerHTML) return
  node.setAttribute('data-icon', props.name)
  const icon = window.prksIcon
  node.innerHTML =
    typeof icon === 'function' ? icon(props.name, { size: 'sm', className: props.svgClass || '' }) : ''
}

onMounted(paint)
watch(() => props.name, paint)
</script>

<template>
  <span ref="el" aria-hidden="true"></span>
</template>
