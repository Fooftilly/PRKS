<script setup lang="ts">
import { useEventListener } from '@vueuse/core'
import { computed, inject, nextTick, onMounted, ref, watch } from 'vue'
import { folderLibraryIntentsKey } from './intents'
import type { FolderRow } from './types'

const props = defineProps<{
  folders: readonly FolderRow[]
  filterQuery: string
  generation: number
}>()

const emit = defineEmits<{
  collapsedChanged: []
}>()

const intents = inject(folderLibraryIntentsKey)
const treeHost = ref<HTMLElement | null>(null)

const treeHtml = computed(() => {
  void props.generation
  const fn = window.prksFolderLibraryTreeInnerHtml
  if (typeof fn !== 'function') return ''
  return fn(props.folders, props.filterQuery)
})

function paintTree(): void {
  const host = treeHost.value
  if (!host) return
  host.innerHTML = treeHtml.value
  window.prksRefreshIcons?.(host)
}

useEventListener(treeHost, 'click', (event: MouseEvent) => {
  const target = event.target as HTMLElement | null
  if (!target) return
  const createBtn = target.closest('[data-prks-create-folder-query]')
  if (createBtn) {
    event.preventDefault()
    const q = createBtn.getAttribute('data-prks-create-folder-query') || ''
    intents?.createFolder(q)
    return
  }
  const toggle = target.closest('.prks-folder-tree__toggle')
  if (toggle) {
    event.preventDefault()
    event.stopPropagation()
    const row = toggle.closest('[data-folder-id]')
    const folderId = row?.getAttribute('data-folder-id') || ''
    if (folderId) {
      intents?.toggleExpand(folderId, treeHost.value, props.folders)
      emit('collapsedChanged')
    }
  }
})

onMounted(() => {
  paintTree()
})

watch(treeHtml, async () => {
  await nextTick()
  paintTree()
})
</script>

<template>
  <div
    ref="treeHost"
    class="prks-folder-library__scroll"
    data-prks-folder-tree-host
  ></div>
</template>
