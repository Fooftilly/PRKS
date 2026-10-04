<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksScopeLine from '../../components/PrksScopeLine.vue'
import { peopleIntentsKey } from './intents'
import type { PeopleIndexProjection } from './projection'
import type { PersonIndexItem } from './types'

const FILTER_KEY = 'prks-people-library-filter'

const props = defineProps<{
  projection: PeopleIndexProjection
}>()

const intents = inject(peopleIntentsKey)
const query = ref(readFilter())

const unavailable = computed(() => props.projection.availability === 'unavailable')
const unknownRole = computed(() => props.projection.availability === 'unknown-role')
const roleFilter = computed(() => props.projection.roleFilter)
const title = computed(() =>
  props.projection.roleLabel ? `People — ${props.projection.roleLabel}` : 'People',
)

const rolePeople = computed(() => {
  const role = roleFilter.value
  if (!role) return props.projection.people
  return props.projection.people.filter((person) => person.roles.includes(role))
})

const shown = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return rolePeople.value
  return rolePeople.value.filter((person) => matches(person, q))
})

const collectionEmpty = computed(
  () => !unavailable.value && !unknownRole.value && props.projection.people.length === 0 && !roleFilter.value,
)
const roleEmpty = computed(
  () => !unavailable.value && !unknownRole.value && !!roleFilter.value && rolePeople.value.length === 0,
)
const searchMiss = computed(
  () => !collectionEmpty.value && !roleEmpty.value && !!query.value.trim() && shown.value.length === 0,
)

function readFilter(): string {
  try {
    return sessionStorage.getItem(FILTER_KEY) || ''
  } catch {
    return ''
  }
}

function matches(person: PersonIndexItem, q: string): boolean {
  const name = `${person.firstName} ${person.lastName}`.trim().toLowerCase()
  const hay = [name, person.aliases, person.about, ...person.roles, ...person.groups.map((group) => group.name)]
  return hay.some((value) => value.toLowerCase().includes(q))
}

function preview(text: string): string {
  const oneLine = text.replace(/\s+/g, ' ').trim()
  if (oneLine.length <= 180) return oneLine
  return `${oneLine.slice(0, 179).trim()}…`
}

function visibleRoles(person: PersonIndexItem): string[] {
  const role = roleFilter.value
  return role ? person.roles.filter((item) => item !== role) : person.roles.slice()
}

function rowHref(id: string): string {
  return `#/people/${encodeURIComponent(id)}`
}

function groupHref(id: string): string {
  return `#/people/groups/${encodeURIComponent(id)}`
}

function icon(name: string): string {
  return typeof window.prksIcon === 'function' ? window.prksIcon(name, { size: 'sm' }) : ''
}

function onCreate(): void {
  intents?.create()
}

function activateRouteLink(event: KeyboardEvent): void {
  const target = event.currentTarget
  if (target instanceof HTMLElement) target.click()
}

function openRow(event: MouseEvent, id: string): void {
  const target = event.target
  if (target instanceof Element && target.closest('a[href^="#/people/groups/"]')) return
  event.preventDefault()
  intents?.openPerson(id)
}

watch(query, (value) => {
  try {
    sessionStorage.setItem(FILTER_KEY, value)
  } catch {
    /* ignore */
  }
})
</script>

<template>
  <div data-prks-people-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">People not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This list has not been cached on this device.
      </PrksInlineMessage>
    </template>
    <template v-else-if="unknownRole">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">People</h2>
      </div>
      <PrksInlineMessage>Unknown role filter.</PrksInlineMessage>
    </template>
    <div v-else class="prks-people-library">
      <div class="prks-page-header page-header prks-people-library__header">
        <h2 class="prks-page-title">{{ title }}</h2>
        <PrksButton
          id="prks-people-header-new"
          :variant="collectionEmpty ? 'secondary' : 'primary'"
          :data-prks-role="collectionEmpty ? undefined : 'person-create-control'"
          @click="onCreate"
        >
          New Person
        </PrksButton>
        <PrksScopeLine
          v-if="rolePeople.length"
          :shown="shown.length"
          :total="rolePeople.length"
          :filter="query.trim()"
          :label="roleFilter || 'People'"
        />
      </div>
      <div v-if="rolePeople.length" class="prks-people-library__toolbar">
        <div class="tag-add-shell tag-add-shell--flush prks-people-library__search">
          <div class="tag-add-shell__field">
            <input
              id="prks-people-library-search"
              v-model="query"
              type="text"
              class="tag-add-shell__input"
              placeholder="Search people…"
              maxlength="300"
              autocomplete="off"
              aria-label="Filter people"
            />
            <button
              id="prks-people-library-search-clear"
              type="button"
              class="tag-add-shell__clear"
              aria-label="Clear search"
              title="Clear search"
              :hidden="!query.trim()"
              @click="query = ''"
            >
              &times;
            </button>
          </div>
        </div>
      </div>
      <div v-if="shown.length" class="prks-people-library__scroll" data-prks-people-list-host>
        <div class="prks-people-list" role="list">
          <div
            v-for="person in shown"
            :key="person.id"
            class="prks-people-list__row"
            role="listitem"
            :data-person-id="person.id"
          >
            <span class="prks-people-list__toggle-spacer" aria-hidden="true"></span>
            <div class="prks-people-list__body">
              <a
                class="prks-people-list__link"
                role="link"
                tabindex="0"
                :href="rowHref(person.id)"
                :data-prks-route="rowHref(person.id)"
                data-prks-middleclick-nav="1"
                @click="openRow($event, person.id)"
                @keydown.enter.prevent="activateRouteLink"
              >
                <span class="prks-people-list__icon">
                  <span v-if="icon('user')" v-html="icon('user')"></span>
                </span>
                <span class="prks-people-list__title-row">
                  <span class="prks-people-list__title">{{
                    `${person.firstName} ${person.lastName}`.trim() || 'Person'
                  }}</span>
                  <span v-if="person.lifespan" class="prks-people-list__lifespan">{{ person.lifespan }}</span>
                </span>
              </a>
              <div v-if="preview(person.about) || person.groups.length || visibleRoles(person).length" class="prks-people-list__details">
                <p v-if="preview(person.about)" class="meta-row person-card-about">{{ preview(person.about) }}</p>
                <p
                  v-if="person.groups.length || visibleRoles(person).length"
                  class="meta-row prks-people-list__meta-line"
                >
                  <span v-if="person.groups.length" class="prks-people-list__groups">
                    <a
                      v-for="group in person.groups"
                      :key="group.id"
                      class="tag"
                      data-prks-role="person-group-link"
                      :href="groupHref(group.id)"
                    >{{ group.name }}</a>
                  </span>
                  <span v-if="visibleRoles(person).length" class="prks-people-list__roles">{{
                    visibleRoles(person).join(' · ')
                  }}</span>
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="prks-people-library__empty">
        <div v-if="collectionEmpty" class="prks-people-list__empty-state">
          <PrksInlineMessage class="prks-people-list__empty">No people yet.</PrksInlineMessage>
          <PrksButton
            id="prks-people-empty-new"
            variant="primary"
            data-prks-role="person-create-control"
            @click="onCreate"
          >
            New Person
          </PrksButton>
        </div>
        <PrksInlineMessage v-else-if="roleEmpty" class="prks-people-list__empty">
          No people with the <strong>{{ roleFilter }}</strong> role yet. Use
          <strong>Link Person to Work</strong> in the ribbon to assign roles.
        </PrksInlineMessage>
        <PrksInlineMessage v-else-if="searchMiss" class="prks-people-list__empty">No people match your search.</PrksInlineMessage>
      </div>
    </div>
  </div>
</template>
