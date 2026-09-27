<script setup lang="ts">
import { computed, provide } from 'vue'
import { browserPositionIntents, positionIntentsKey, type PositionIntentOwner } from './intents'

const props = defineProps<{
  owner: PositionIntentOwner
  generation: number
}>()

/** Rebuild when owner/generation props change; keep one stable provide target. */
const current = computed(() => browserPositionIntents(props.owner, props.generation))

provide(positionIntentsKey, {
  create: () => current.value.create(),
  viewGraph: (position) => current.value.viewGraph(position),
})
</script>

<template>
  <slot />
</template>
