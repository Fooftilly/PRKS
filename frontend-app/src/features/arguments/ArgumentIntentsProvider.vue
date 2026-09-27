<script setup lang="ts">
import { computed, provide } from 'vue'
import { argumentIntentsKey, browserArgumentIntents, type ArgumentIntentOwner } from './intents'

const props = defineProps<{
  owner: ArgumentIntentOwner
  generation: number
}>()

const current = computed(() => browserArgumentIntents(props.owner, props.generation))

provide(argumentIntentsKey, {
  create: (kind) => current.value.create(kind),
  filterKind: (kind) => current.value.filterKind(kind),
  viewGraph: (argument) => current.value.viewGraph(argument),
  enterEdit: (argumentId) => current.value.enterEdit(argumentId),
  cancelEdit: () => current.value.cancelEdit(),
  save: (argumentId, draft) => current.value.save(argumentId, draft),
  createResponse: (argument) => current.value.createResponse(argument),
  remove: (argument) => current.value.remove(argument),
  pickTarget: (selfId, onPick) => current.value.pickTarget(selfId, onPick),
  pickSource: (onPick) => current.value.pickSource(onPick),
})
</script>

<template>
  <slot />
</template>
