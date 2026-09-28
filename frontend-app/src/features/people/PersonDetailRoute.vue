<script setup lang="ts">
import { computed, inject, onUpdated, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import { peopleIntentsKey } from './intents'
import { usePeoplePendingAction } from './pending-action'
import type { PersonDetailProjection } from './projection'
import type { PersonFieldDraft, PersonGroupChip } from './types'

const FIELD_KEYS = [
  'first_name',
  'last_name',
  'aliases',
  'about',
  'birth_date',
  'death_date',
  'image_url',
  'link_wikipedia',
  'link_stanford_encyclopedia',
  'link_iep',
  'links_other',
] as const

const props = defineProps<{
  projection: PersonDetailProjection
}>()

const intents = inject(peopleIntentsKey)
const { actionBusy, actionBlocked, resetPending, withBusy } = usePeoplePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const editorEl = ref<HTMLElement | null>(null)
const draft = ref<PersonFieldDraft>(emptyFields())
const fieldBaseline = ref<PersonFieldDraft>(emptyFields())
const groupBaseline = ref<string[]>([])
const status = ref('')

const person = computed(() => props.projection.person)
const editing = computed(() => props.projection.editing && !!person.value)
const showForm = computed(() => editing.value && props.projection.editorActive)
const unavailable = computed(() => props.projection.availability === 'unavailable')
const notFound = computed(
  () => !unavailable.value && (props.projection.availability === 'not-found' || !person.value),
)
const displayName = computed(() => `${person.value?.firstName || ''} ${person.value?.lastName || ''}`.trim() || 'Person')
const portraitSrc = computed(() => {
  const current = person.value
  if (!current || props.projection.offlineCached || !current.imageUrl) return ''
  return `/api/persons/${encodeURIComponent(current.id)}/profile-image`
})

function emptyFields(): PersonFieldDraft {
  return {
    first_name: '',
    last_name: '',
    aliases: '',
    about: '',
    birth_date: '',
    death_date: '',
    image_url: '',
    link_wikipedia: '',
    link_stanford_encyclopedia: '',
    link_iep: '',
    links_other: '',
  }
}

function fieldsOf(source: PersonFieldDraft | undefined): PersonFieldDraft {
  const next = emptyFields()
  if (!source) return next
  for (const key of FIELD_KEYS) next[key] = source[key] || ''
  return next
}

function groupIds(groups: readonly PersonGroupChip[]): string[] {
  return groups.map((group) => group.id).filter(Boolean)
}

function syncShared(fields: PersonFieldDraft, groups: readonly PersonGroupChip[], replaceGroups: boolean): void {
  intents?.bindDraft(props.projection.personId, fields, groups, replaceGroups)
}

watch(
  () => `${person.value?.id || ''}:${props.projection.editing ? '1' : '0'}`,
  (key, previous) => {
    if (key === previous) return
    intents?.invalidateEditSession()
    resetPending()
    status.value = ''
    if (!props.projection.editing || !person.value) return
    const session = fieldsOf(person.value.fields)
    const groups = person.value.groups
    draft.value = { ...session }
    fieldBaseline.value = { ...session }
    groupBaseline.value = groupIds(groups)
    syncShared(session, groups, true)
  },
  { immediate: true },
)

watch(
  draft,
  (fields) => {
    if (!editing.value || !person.value) return
    syncShared(fields, person.value.groups, false)
  },
  { deep: true },
)

watch(
  () => person.value?.id || '',
  (id, previous) => {
    if (!previous || id === previous) return
    resetPending()
  },
)

function viewIdentity(): { id: string; editSession: number } | null {
  const id = person.value?.id
  if (!id) return null
  return { id, editSession: intents?.editSession() ?? 0 }
}

function viewStill(identity: { id: string; editSession: number } | null): boolean {
  if (!identity) return false
  return person.value?.id === identity.id && (intents?.editSession() ?? 0) === identity.editSession
}

function settleOffline(): void {
  const root = rootEl.value
  if (!root || typeof window.prksApplyPersonOfflineState !== 'function') return
  window.prksApplyPersonOfflineState(root)
}

onUpdated(() => {
  settleOffline()
  const editor = editorEl.value
  if (!showForm.value || !editor || editor.dataset.prksGroupPicker === '1') return
  editor.dataset.prksGroupPicker = '1'
  intents?.mountGroups(editor)
})

function onCancel(): void {
  const id = person.value?.id
  if (!id) return
  status.value = ''
  intents?.cancelEdit(id)
}

async function onSave(): Promise<void> {
  const current = person.value
  const identity = viewIdentity()
  if (!current || !identity || !intents) return
  if (!draft.value.last_name.trim()) {
    status.value = 'Last name is required.'
    return
  }
  const draftSnapshot = { ...draft.value }
  const baselineSnapshot = { ...fieldBaseline.value }
  const groups = intents.liveGroupIds(current.id)
  const baselineGroups = groupBaseline.value.slice()
  const session = identity.editSession
  await withBusy('save', async () => {
    const result = await intents.saveProfile(
      current.id,
      draftSnapshot,
      baselineSnapshot,
      groups,
      baselineGroups,
      session,
    )
    if (!viewStill(identity) || !result) return
    if (!result.ok) status.value = result.message
    else status.value = ''
  })
}

async function onDelete(): Promise<void> {
  const current = person.value
  if (!current) return
  await withBusy('delete', async () => {
    await intents?.remove(current.id, props.projection.generation)
  })
}

function onToggleWorks(): void {
  intents?.toggleWorks(person.value?.id || '')
}

async function onUnlink(workId: string, roleType: string, orderIndex: string): Promise<void> {
  const current = person.value
  const identity = viewIdentity()
  if (!current || !identity) return
  await withBusy(`unlink:${workId}:${roleType}`, async () => {
    await intents?.removeWorkRole(current.id, workId, roleType, orderIndex)
    if (!viewStill(identity)) return
  })
}

function workCard(work: { id: string; title: string; subtitle: string }): string {
  const html = window.prksWorkCardHtml
  if (typeof html !== 'function') return ''
  return html(
    { id: work.id, title: work.title },
    {
      subtitle: work.subtitle || undefined,
      suppressThumbnail: props.projection.offlineCached,
    },
  )
}
</script>

<template>
  <div ref="rootEl" data-prks-person-detail-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Person not available offline</h2>
      </div>
      <p class="prks-inline-message" data-prks-role="offline-unavailable">This item is not available offline.</p>
    </template>
    <template v-else-if="notFound">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Person not found</h2>
      </div>
    </template>
    <template v-else-if="person">
      <div class="prks-page-header page-header page-header--split">
        <h2 class="prks-page-title">{{ displayName }}</h2>
      </div>
      <div v-if="showForm" ref="editorEl" class="doc-meta-card person-panel-edit" :data-person-edit-id="person.id">
        <div class="card-heading-row card-heading-row--wrap">
          <h3>Edit profile</h3>
        </div>
        <div class="form-pane person-edit-form">
          <section class="person-edit-section" aria-labelledby="person-edit-identity-heading">
            <h4 id="person-edit-identity-heading">Identity</h4>
            <label for="pd-first-name">First name</label>
            <input id="pd-first-name" v-model="draft.first_name" type="text" />
            <label for="pd-last-name">Last name</label>
            <input id="pd-last-name" v-model="draft.last_name" type="text" />
            <label for="pd-aliases">Aliases</label>
            <input id="pd-aliases" v-model="draft.aliases" type="text" />
          </section>
          <section class="person-edit-section" aria-labelledby="person-edit-biography-heading">
            <h4 id="person-edit-biography-heading">Biography</h4>
            <label for="pd-about">About / expertise</label>
            <textarea id="pd-about" v-model="draft.about" class="textarea-sm"></textarea>
          </section>
          <section class="person-edit-section" aria-labelledby="person-edit-dates-heading">
            <h4 id="person-edit-dates-heading">Dates</h4>
            <div class="form-grid-2 form-grid-2--compact">
              <div>
                <label for="pd-birth-date">Birth date</label>
                <input id="pd-birth-date" v-model="draft.birth_date" type="text" placeholder="dd/mm/yyyy or yyyy" autocomplete="off" />
              </div>
              <div>
                <label for="pd-death-date">Date of death</label>
                <input id="pd-death-date" v-model="draft.death_date" type="text" placeholder="dd/mm/yyyy or yyyy" autocomplete="off" />
              </div>
            </div>
          </section>
          <section class="person-edit-section" aria-labelledby="person-edit-portrait-heading">
            <h4 id="person-edit-portrait-heading">Portrait</h4>
            <label for="pd-image-url">Portrait image URL</label>
            <input id="pd-image-url" v-model="draft.image_url" type="url" />
          </section>
          <section class="person-edit-section" aria-labelledby="person-edit-references-heading">
            <h4 id="person-edit-references-heading">References</h4>
            <label for="pd-link-wikipedia">Wikipedia</label>
            <input id="pd-link-wikipedia" v-model="draft.link_wikipedia" type="url" />
            <label for="pd-link-stanford">Stanford Encyclopedia of Philosophy</label>
            <input id="pd-link-stanford" v-model="draft.link_stanford_encyclopedia" type="url" />
            <label for="pd-link-iep">Internet Encyclopedia of Philosophy</label>
            <input id="pd-link-iep" v-model="draft.link_iep" type="url" />
            <label for="pd-links-other">Other links</label>
            <textarea id="pd-links-other" v-model="draft.links_other" class="textarea-sm" placeholder="One URL per line, or [Title](https://...)"></textarea>
          </section>
          <section class="person-edit-section" aria-labelledby="person-edit-groups-heading">
            <h4 id="person-edit-groups-heading">Groups</h4>
            <fieldset class="person-groups-fieldset">
              <legend class="sr-only">Groups</legend>
              <p class="meta-row">
                Search for a group, pick from the list, or type a new name and <strong>Add</strong> to create a
                top-level group. Names are unique. <a href="#/people/groups">Browse groups</a>.
              </p>
              <div id="pd-group-chips" class="tag-cloud person-groups-fieldset__chips"></div>
              <label for="pd-group-search">Add group</label>
              <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                <div class="tag-add-shell__field">
                  <input id="pd-group-search" class="tag-add-shell__input" type="text" placeholder="Search or type new group name…" autocomplete="off" aria-label="Search group to add" />
                </div>
                <input id="pd-group-pick-id" type="hidden" value="" />
                <div id="pd-group-results" class="combobox-results combobox-results--tag-panel hidden"></div>
              </div>
              <button id="pd-group-add-btn" type="button" class="prks-btn prks-btn--primary person-groups-fieldset__action">
                Add group
              </button>
            </fieldset>
          </section>
        </div>
        <p v-if="status" class="prks-inline-message prks-inline-message--error" data-prks-role="person-save-status">{{ status }}</p>
        <div class="form-actions prks-form-actions--split person-edit-footer">
          <button type="button" class="prks-btn prks-btn--secondary" data-prks-person-cancel @click="onCancel">Cancel</button>
          <PrksButton
            id="pd-save-btn"
            variant="primary"
            :busy="actionBusy('save')"
            :disabled="actionBlocked('save')"
            busy-label="Saving…"
            @click="onSave"
          >
            Save profile
          </PrksButton>
        </div>
      </div>
      <div v-else class="document-view document-view--person">
        <div class="doc-content person-profile">
          <div class="person-profile__hero" :class="{ 'person-profile__hero--no-photo': !portraitSrc }">
            <div v-if="portraitSrc" class="person-profile__portrait">
              <div class="person-portrait-wrap">
                <img class="person-portrait" :src="portraitSrc" alt="" />
              </div>
            </div>
            <div class="person-profile__info">
              <div class="person-profile__summary">
                <p v-if="person.lifespan" class="person-profile__lifespan">{{ person.lifespan }}</p>
                <div v-if="person.aliases.length" class="person-profile__aliases">
                  <span class="person-card-label">Also known as</span>
                  <span class="person-profile__alias-list">
                    <span v-for="alias in person.aliases" :key="alias" class="person-profile__alias-tag">{{ alias }}</span>
                  </span>
                </div>
                <p v-if="person.groups.length" class="meta-row person-profile__groups">
                  <a
                    v-for="group in person.groups"
                    :key="group.id"
                    class="tag"
                    data-prks-role="person-group-link"
                    :href="`#/people/groups/${encodeURIComponent(group.id)}`"
                  >{{ group.name }}</a>
                </p>
              </div>
              <section v-if="person.about" class="person-profile__about" aria-labelledby="person-profile-about-heading">
                <h3 id="person-profile-about-heading">About</h3>
                <p class="person-profile__about-text">{{ person.about }}</p>
              </section>
              <div v-if="person.links.length" class="person-external-links">
                <h4>References</h4>
                <ul class="person-link-list">
                  <li v-for="link in person.links" :key="link.href">
                    <a :href="link.href" target="_blank" rel="noopener noreferrer">
                      <span>{{ link.label }}</span>
                      <span aria-hidden="true">↗</span>
                    </a>
                  </li>
                </ul>
              </div>
            </div>
          </div>
          <section
            class="person-profile__works"
            :class="{ 'person-profile__works--editing': projection.worksEditing }"
            aria-labelledby="person-profile-works-heading"
          >
            <div class="person-profile__works-head">
              <h2 id="person-profile-works-heading" class="person-profile__works-title">Linked files</h2>
              <span class="person-profile__works-count">{{ person.works.length }}</span>
              <button
                v-if="person.works.length || projection.worksEditing"
                type="button"
                class="prks-btn prks-btn--secondary prks-btn--sm person-profile__works-action"
                :data-prks-role="projection.worksEditing ? undefined : 'person-mutation-control'"
                @click="onToggleWorks"
              >
                {{ projection.worksEditing ? 'Done' : 'Edit relationships' }}
              </button>
            </div>
            <p v-if="!person.works.length" class="prks-inline-message">This person is not linked to any files.</p>
            <div v-for="work in person.works" :key="`${work.id}:${work.roleType}:${work.orderIndex}`" class="person-profile__work-card-wrap">
              <div v-if="workCard(work)" v-html="workCard(work)"></div>
              <p v-else class="meta-row">{{ work.title }} <span v-if="work.roleType">· {{ work.roleType }}</span></p>
              <PrksButton
                v-if="projection.worksEditing"
                variant="ghost"
                size="sm"
                class="person-profile__card-unlink"
                data-prks-role="person-mutation-control"
                :aria-label="`Remove link to this file`"
                :busy="actionBusy(`unlink:${work.id}:${work.roleType}`)"
                :disabled="actionBlocked(`unlink:${work.id}:${work.roleType}`)"
                busy-label="Removing…"
                @click="onUnlink(work.id, work.roleType, work.orderIndex)"
              >
                ×
              </PrksButton>
            </div>
          </section>
          <div class="person-profile__actions">
            <PrksButton
              id="prks-person-delete-btn"
              variant="danger"
              data-prks-role="person-delete-control"
              :disabled="person.works.length > 0 || actionBlocked('delete')"
              :busy="actionBusy('delete')"
              busy-label="Deleting…"
              @click="onDelete"
            >
              Delete person
            </PrksButton>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
