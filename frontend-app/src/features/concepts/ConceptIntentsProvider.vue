<script setup lang="ts">
import { computed, provide } from 'vue'
import { browserConceptIntents, conceptIntentsKey, type ConceptIntentOwner } from './intents'

const props = defineProps<{
  owner: ConceptIntentOwner
  generation: number
}>()

/** Rebuild when owner/generation props change; keep one stable provide target. */
const current = computed(() => browserConceptIntents(props.owner, props.generation))

provide(conceptIntentsKey, {
  create: (initialName) => current.value.create(initialName),
  rename: (concept) => current.value.rename(concept),
  remove: (concept) => current.value.remove(concept),
  editDefinition: (concept) => current.value.editDefinition(concept),
  editAliases: (concept) => current.value.editAliases(concept),
  editParents: (concept) => current.value.editParents(concept),
  viewGraph: (concept) => current.value.viewGraph(concept),
})
</script>

<template>
  <slot />
</template>
