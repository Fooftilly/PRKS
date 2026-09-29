<script setup lang="ts">
import { computed, provide } from 'vue'
import { browserPersonGroupIntents, personGroupIntentsKey, type PersonGroupIntentOwner } from './intents'

const props = defineProps<{
  owner: PersonGroupIntentOwner
  generation: number
}>()

const current = computed(() => browserPersonGroupIntents(props.owner))

provide(personGroupIntentsKey, {
  ownerTabId: () => current.value.ownerTabId(),
  create: () => current.value.create(),
  openGroup: (groupId) => current.value.openGroup(groupId),
  openPerson: (personId) => current.value.openPerson(personId),
  beginEdit: () => current.value.beginEdit(),
  cancelEdit: () => current.value.cancelEdit(),
  toggleMembers: () => current.value.toggleMembers(),
  editSession: () => current.value.editSession(),
  memberSession: () => current.value.memberSession(),
  capturedBaseline: (groupId) => current.value.capturedBaseline(groupId),
  save: (groupId, draft, baseline, session) => current.value.save(groupId, draft, baseline, session),
  remove: (groupId, session) => current.value.remove(groupId, session),
  bindChrome: () => current.value.bindChrome(),
  indexChrome: () => current.value.indexChrome(),
  writeIndexChrome: (query, expandedIds) => current.value.writeIndexChrome(query, expandedIds),
})
</script>

<template>
  <slot />
</template>
