<script setup lang="ts">
import { computed, provide } from 'vue'
import {
  browserFolderDetailIntents,
  folderDetailIntentsKey,
  type FolderDetailIntentOwner,
} from './intents'

const props = defineProps<{
  owner: FolderDetailIntentOwner
  generation: number
}>()

const current = computed(() => browserFolderDetailIntents(props.owner, props.generation))

provide(folderDetailIntentsKey, {
  owner: () => current.value.owner(),
  remove: (folderId) => current.value.remove(folderId),
  createChild: (folder) => current.value.createChild(folder),
})
</script>

<template>
  <slot />
</template>
