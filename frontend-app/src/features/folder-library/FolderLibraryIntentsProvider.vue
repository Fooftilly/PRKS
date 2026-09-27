<script setup lang="ts">
import { computed, provide } from 'vue'
import { browserFolderLibraryIntents, folderLibraryIntentsKey, type FolderIntentOwner } from './intents'

const props = defineProps<{
  owner: FolderIntentOwner
  generation: number
}>()

const current = computed(() => browserFolderLibraryIntents(props.owner, props.generation))

provide(folderLibraryIntentsKey, {
  createFolder: (title) => current.value.createFolder(title),
  openWorkModal: () => current.value.openWorkModal(),
  navigateFolder: (id) => current.value.navigateFolder(id),
  switchTab: (tab) => current.value.switchTab(tab),
  loadRecentlyAdded: (force) => current.value.loadRecentlyAdded(force),
  toggleExpand: (id) => current.value.toggleExpand(id),
  toggleExpandAll: () => current.value.toggleExpandAll(),
  bindFolderOfflineState: (root) => current.value.bindFolderOfflineState(root),
  scheduleGlance: (root) => current.value.scheduleGlance(root),
})
</script>

<template>
  <slot />
</template>
