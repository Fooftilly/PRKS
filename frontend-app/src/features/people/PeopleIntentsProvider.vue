<script setup lang="ts">
import { computed, provide } from 'vue'
import { browserPeopleIntents, peopleIntentsKey, type PeopleIntentOwner } from './intents'

const props = defineProps<{
  owner: PeopleIntentOwner
  generation: number
}>()

const current = computed(() => browserPeopleIntents(props.owner, props.generation))

provide(peopleIntentsKey, {
  create: () => current.value.create(),
  openPerson: (personId) => current.value.openPerson(personId),
  beginEdit: (personId) => current.value.beginEdit(personId),
  cancelEdit: (personId) => current.value.cancelEdit(personId),
  editSession: () => current.value.editSession(),
  invalidateEditSession: () => current.value.invalidateEditSession(),
  liveGroupIds: (personId) => current.value.liveGroupIds(personId),
  saveProfile: (personId, draft, baseline, groupIds, baselineGroupIds, session, generation) =>
    current.value.saveProfile(personId, draft, baseline, groupIds, baselineGroupIds, session, generation),
  toggleWorks: (personId) => current.value.toggleWorks(personId),
  removeWorkRole: (personId, workId, roleType, orderIndex, workTitle) =>
    current.value.removeWorkRole(personId, workId, roleType, orderIndex, workTitle),
  remove: (personId, routeGeneration) => current.value.remove(personId, routeGeneration),
  viewGraph: () => current.value.viewGraph(),
  bindDraft: (personId, fields, groups, replaceGroups) =>
    current.value.bindDraft(personId, fields, groups, replaceGroups),
  mountGroups: (editor) => current.value.mountGroups(editor),
})
</script>

<template>
  <slot />
</template>
