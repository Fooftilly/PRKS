<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

const props = defineProps<{
  name?: string
  role?: string
  status?: string
  size?: 'sm' | 'nav' | 'navSub' | 'ribbon'
  extraClass?: string
}>()

const el = ref<HTMLElement | null>(null)

function paint(): void {
  const node = el.value
  if (!node) return
  let html = ''
  if (props.status) {
    html = window.prksProgressStatusIconHtml?.(props.status, { className: 'prks-status-icon' }) ?? ''
  } else if (props.role) {
    html = window.prksRoleTypeIconHtml?.(props.role, { size: 'navSub' }) ?? ''
  } else if (props.name && typeof window.prksIcon === 'function') {
    html = window.prksIcon(props.name, {
      size: props.size,
      className: props.extraClass || '',
    })
  }
  node.innerHTML = html
  window.prksRefreshIcons?.(node)
}

onMounted(paint)
watch(() => [props.name, props.role, props.status, props.size, props.extraClass], paint)
</script>

<template>
  <span ref="el" aria-hidden="true"></span>
</template>
