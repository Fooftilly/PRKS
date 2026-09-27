<script setup lang="ts">
import { computed, provide } from 'vue'
import {
  browserFolderLibraryIntents,
  folderLibraryIntentsKey,
  type FolderLibraryIntentOwner,
} from './intents'

const props = defineProps<{
  owner: FolderLibraryIntentOwner
  generation: number
}>()

/** Rebuild when owner/generation props change; keep one stable provide target. */
const current = computed(() => browserFolderLibraryIntents(props.owner, props.generation))

provide(folderLibraryIntentsKey, {
  createFolder: (query) => current.value.createFolder(query),
  createWork: () => current.value.createWork(),
  openFolder: (folderId) => current.value.openFolder(folderId),
  toggleFolderNode: (folderId) => current.value.toggleFolderNode(folderId),
  toggleAllFolderNodes: () => current.value.toggleAllFolderNodes(),
  persistTab: (tab) => current.value.persistTab(tab),
  persistFolderFilter: (query) => current.value.persistFolderFilter(query),
  persistFilesFilter: (query) => current.value.persistFilesFilter(query),
})
</script>

<template>
  <slot />
</template>
