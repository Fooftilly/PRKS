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
  readTreeChrome: () => current.value.readTreeChrome(),
  writeExpanded: (ids) => current.value.writeExpanded(ids),
  writeLibraryQuery: (query) => current.value.writeLibraryQuery(query),
  loadRecentlyAdded: (force, cache) => current.value.loadRecentlyAdded(force, cache),
  bindFolderOfflineState: (root) => current.value.bindFolderOfflineState(root),
  scheduleGlance: (root, options) => current.value.scheduleGlance(root, options),
  subscribeMetadataOverlay: (onChange) => current.value.subscribeMetadataOverlay(onChange),
})
</script>

<template>
  <slot />
</template>
