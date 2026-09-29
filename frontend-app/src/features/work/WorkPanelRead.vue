<script setup lang="ts">
import { computed } from 'vue'
import { workPanelBibFields, type WorkPanelPerson, type WorkPanelReadModel } from './panel-read'

const props = defineProps<{
  model: WorkPanelReadModel
  slots: {
    summary: boolean
    identity: boolean
    dates: boolean
    bib: boolean
    source: boolean
    people: boolean
    tags: boolean
    folder: boolean
    playlist: boolean
  }
}>()

const summaryParts = computed(() => {
  const display = props.model.display
  const parts: { key: string; text: string; href?: string }[] = [
    { key: 'status', text: display.status },
    { key: 'type', text: display.docType.label },
  ]
  if (display.folder) {
    parts.push({
      key: 'folder',
      text: display.folder.title,
      href: display.folder.id ? `#/folders/${encodeURIComponent(display.folder.id)}` : undefined,
    })
  }
  parts.push({
    key: 'people',
    text: `${display.peopleCount} ${display.peopleCount === 1 ? 'person' : 'people'}`,
  })
  parts.push({
    key: 'tags',
    text: `${display.tagsCount} ${display.tagsCount === 1 ? 'tag' : 'tags'}`,
  })
  return parts.filter((part) => part.text.trim())
})

function statusClass(status: string): string {
  return status.replace(/[^A-Za-z0-9 ]+/g, ' ').trim().replace(/\s+/g, ' ')
}

function peopleGroups(people: readonly WorkPanelPerson[]): { role: string; people: WorkPanelPerson[] }[] {
  const order: string[] = []
  const groups = new Map<string, WorkPanelPerson[]>()
  for (const person of people) {
    const role = person.roleType || 'Linked'
    if (!groups.has(role)) {
      groups.set(role, [])
      order.push(role)
    }
    groups.get(role)?.push(person)
  }
  return order.map((role) => ({ role, people: groups.get(role) ?? [] }))
}

function personHref(personId: string): string {
  return `#/people/${encodeURIComponent(personId)}`
}

function tagHref(name: string): string {
  return `#/search?tag=${encodeURIComponent(name)}`
}

function folderHref(id: string): string {
  return `#/folders/${encodeURIComponent(id)}`
}

function publisherHref(value: string): string {
  return `#/search?publisher=${encodeURIComponent(value.trim())}`
}
</script>

<template>
  <Teleport v-if="slots.summary" to="[data-prks-role='work-panel-summary']">
    <p v-if="summaryParts.length" class="prks-state-summary">
      <template v-for="(part, index) in summaryParts" :key="part.key">
        <span v-if="index > 0" class="prks-summary-sep" aria-hidden="true"> · </span>
        <a v-if="part.href" class="prks-summary-link" :href="part.href">{{ part.text }}</a>
        <template v-else>{{ part.text }}</template>
      </template>
    </p>
  </Teleport>

  <Teleport v-if="slots.identity" to="[data-prks-role='work-panel-identity']">
    <p class="card-title">{{ model.display.title }}</p>
    <div class="card-heading-row card-heading-row--wrap">
      <span class="meta-row">Status</span>
      <span class="status-badge" :class="statusClass(model.display.status)">
        <span
          v-if="model.display.statusIcon"
          class="prks-icon prks-icon--sm status-badge__icon"
          :data-lucide="model.display.statusIcon"
        ></span>
        {{ model.display.status }}
      </span>
    </div>
    <div class="card-heading-row card-heading-row--wrap">
      <span class="meta-row">Document type</span>
      <span
        class="doc-type-badge"
        :style="{
          '--doc-type-color': model.display.docType.color,
          '--doc-type-border': model.display.docType.border,
        }"
        :title="`BibTeX type: @${model.display.docType.value}`"
        >{{ model.display.docType.label }}</span
      >
    </div>
  </Teleport>

  <Teleport v-if="slots.dates" to="[data-prks-role='work-panel-dates']">
    <p v-if="model.display.year" class="meta-row"><strong>Year:</strong> {{ model.display.year }}</p>
    <p v-if="model.display.showPublishedDate && model.display.publishedDisplay" class="meta-row">
      <strong>Published:</strong> {{ model.display.publishedDisplay }}
    </p>
  </Teleport>

  <Teleport v-if="slots.bib" to="[data-prks-role='work-bib-rows']">
    <p v-for="field in workPanelBibFields(model.display)" :key="field.field" class="meta-row">
      <strong>{{ field.label }}:</strong>
      <a v-if="field.field === 'publisher'" class="route-sidebar__link" :href="publisherHref(field.value)">{{
        field.value
      }}</a>
      <template v-else> {{ field.value }}</template>
    </p>
  </Teleport>

  <Teleport v-if="slots.source" to="[data-prks-role='work-panel-source']">
    <p v-if="model.display.showOriginalUrl" class="meta-row">
      <strong>Original URL:</strong>
      <a :href="model.display.sourceUrl" target="_blank" rel="noopener noreferrer">{{ model.display.sourceUrl }}</a>
    </p>
    <p v-if="model.display.abstract" class="meta-row"><strong>Abstract:</strong> {{ model.display.abstract }}</p>
    <p
      v-if="!model.display.hasBibliographicText"
      class="meta-row meta-row--muted-italic"
      data-prks-role="work-meta-empty"
    >
      No metadata available.
    </p>
  </Teleport>

  <Teleport v-if="slots.people" to="[data-prks-role='work-people-read']">
    <p v-if="model.display.people.length === 0" class="meta-row work-linked-persons__empty">No persons linked.</p>
    <div v-for="group in peopleGroups(model.display.people)" :key="group.role" class="work-linked-persons__role">
      <h4 class="work-linked-persons__role-title">{{ group.role }}</h4>
      <div class="tag-cloud">
        <span
          v-for="person in group.people"
          :key="`${group.role}:${person.personId}:${person.orderIndex}`"
          class="work-linked-persons__chip tag"
        >
          <a
            class="work-linked-persons__chip-link"
            :href="personHref(person.personId)"
            :title="person.displayName !== person.canonicalName ? `Profile: ${person.canonicalName}` : undefined"
          >
            <span class="prks-icon prks-icon--sm" data-lucide="user"></span>
            {{ person.displayName }}
          </a>
        </span>
      </div>
    </div>
  </Teleport>

  <Teleport v-if="slots.tags" to="[data-prks-role='work-tags-read']">
    <span v-if="model.display.tags.length === 0" class="work-tags-empty">No tags yet.</span>
    <span
      v-for="tag in model.display.tags"
      :key="tag.id || tag.name"
      class="tag work-tag-chip work-tag-chip--colored"
      :style="{ '--tag-accent': tag.color || '#6d6cf7' }"
    >
      <a class="work-tag-chip__link" :href="tagHref(tag.name)">{{ tag.name }}</a>
    </span>
  </Teleport>

  <Teleport v-if="slots.folder" to="[data-prks-role='work-folder-read']">
    <template v-if="model.display.folder">
      <span class="meta-row">Folder:</span>
      <a class="route-sidebar__link" :href="folderHref(model.display.folder.id)">{{ model.display.folder.title }}</a>
    </template>
    <span v-else class="meta-row">Not in a folder</span>
  </Teleport>

  <Teleport v-if="slots.playlist" to="[data-prks-role='work-playlist-read']">
    <template v-if="model.display.playlist">Current: <strong>{{ model.display.playlist.title }}</strong></template>
    <template v-else>Not in a playlist.</template>
  </Teleport>
</template>
