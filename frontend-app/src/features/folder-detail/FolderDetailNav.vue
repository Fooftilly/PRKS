<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  folderId: string
  title: string
  parentId?: string
  parentTitle?: string
  tabId?: string
}>()

/**
 * The hierarchy switcher paints crumbs and nearby chips into this shell.
 * `folder-hierarchy-nav.js` owns that DOM. Vue supplies the host and does not
 * reconcile the interior, so a later catalogue paint is not reset.
 */
const navEl = ref<HTMLElement | null>(null)

const shellHtml = computed(() => {
  const fn = window.prksFolderNavTriggerHtml
  if (typeof fn !== 'function' || !props.folderId) return ''
  const parent =
    props.parentId && props.parentTitle
      ? { id: props.parentId, title: props.parentTitle }
      : null
  return fn(
    { id: props.folderId, title: props.title || 'Folder', parent },
    null,
    { tabId: props.tabId },
  )
})

/**
 * The switcher module owns this markup and later rewrites crumbs and nearby
 * chips in place. Paint it once per shell string so Vue does not reset that
 * interior. The string is `prksFolderNavTriggerHtml`, which escapes text.
 */
function paintShell(): void {
  const node = navEl.value as (HTMLElement & { __prksNavShell?: string }) | null
  if (!node) return
  const html = shellHtml.value
  if (node.__prksNavShell === html) return
  node.__prksNavShell = html
  node.innerHTML = html
}

onMounted(paintShell)
watch(shellHtml, paintShell)
</script>

<template>
  <nav
    ref="navEl"
    class="prks-folder-nav"
    data-prks-role="folder-hierarchy-nav"
    aria-label="Folder navigation"
  ></nav>
</template>
