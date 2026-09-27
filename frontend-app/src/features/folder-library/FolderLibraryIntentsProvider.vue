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
  loadRecentlyAdded: (force, cache) => current.value.loadRecentlyAdded(force, cache),
  toggleExpand: (id, host, folders) => current.value.toggleExpand(id, host, folders),
  toggleExpandAll: (host, folders) => current.value.toggleExpandAll(host, folders),
  bindFolderOfflineState: (root) => current.value.bindFolderOfflineState(root),
  scheduleGlance: (root, options) => current.value.scheduleGlance(root, options),
  subscribeMetadataOverlay: (onChange) => current.value.subscribeMetadataOverlay(onChange),
})
</script>

<template>
  <slot />
</template>
