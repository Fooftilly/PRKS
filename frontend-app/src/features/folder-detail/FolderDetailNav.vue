<script setup lang="ts">
import { computed } from 'vue'

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
</script>

<template>
  <nav
    class="prks-folder-nav"
    data-prks-role="folder-hierarchy-nav"
    aria-label="Folder navigation"
    v-html="shellHtml"
  ></nav>
</template>
