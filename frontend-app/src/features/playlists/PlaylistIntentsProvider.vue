<script setup lang="ts">
import { computed, provide } from 'vue'
import { browserPlaylistIntents, playlistIntentsKey, type PlaylistIntentOwner } from './intents'

const props = defineProps<{
  owner: PlaylistIntentOwner
  generation: number
}>()

const current = computed(() => browserPlaylistIntents(props.owner, props.generation))

provide(playlistIntentsKey, {
  create: () => current.value.create(),
  cancelEdit: (playlistId) => current.value.cancelEdit(playlistId),
  saveFields: (playlistId, draft, shown) => current.value.saveFields(playlistId, draft, shown),
  reorder: (playlistId, workIds) => current.value.reorder(playlistId, workIds),
  removeWork: (playlistId, workId) => current.value.removeWork(playlistId, workId),
  addWork: (playlistId, workId) => current.value.addWork(playlistId, workId),
  loadAddableVideos: (playlistId, presentIds) => current.value.loadAddableVideos(playlistId, presentIds),
  beginRename: (playlistId, workId) => current.value.beginRename(playlistId, workId),
  cancelRename: (playlistId, workId) => current.value.cancelRename(playlistId, workId),
  saveWorkTitle: (playlistId, workId, title) => current.value.saveWorkTitle(playlistId, workId, title),
  remove: (playlistId, playlist) => current.value.remove(playlistId, playlist),
})
</script>

<template>
  <slot />
</template>
