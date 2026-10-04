<script setup lang="ts">
import { computed, inject, onMounted, onUpdated, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { personGroupIntentsKey } from './intents'
import { usePersonGroupPendingAction } from './pending-action'
import type { PersonGroupDetailProjection } from './projection'
import type { PersonGroupFieldDraft, PersonGroupMemberItem } from './types'

const props = defineProps<{
  projection: PersonGroupDetailProjection
}>()

const intents = inject(personGroupIntentsKey)
const { actionBusy, resetPending, withBusy } = usePersonGroupPendingAction()
const rootEl = ref<HTMLElement | null>(null)
const fieldBaseline = ref<PersonGroupFieldDraft | null>(null)

const group = computed(() => props.projection.group)
const editing = computed(() => props.projection.editing && !!group.value)
const membersEditing = computed(() => props.projection.membersEditing && !!group.value)
const unavailable = computed(() => props.projection.availability === 'unavailable')
const notFound = computed(() => !unavailable.value && (props.projection.availability === 'not-found' || !group.value))
const description = computed(() => group.value?.description.trim() || '')

watch(
  () => group.value?.id || '',
  () => {
    resetPending()
  },
)
const parentHint = computed(() =>
  typeof window.prksHintBtnHtml === 'function'
    ? window.prksHintBtnHtml('group-edit-parent', 'About parent group', 'group-sidebar__hint-btn')
    : '',
)

function memberName(member: PersonGroupMemberItem): string {
  return `${member.firstName} ${member.lastName}`.trim() || 'Person'
}

function personHref(id: string): string {
  return `#/people/${encodeURIComponent(id)}`
}

function groupHref(id: string): string {
  return `#/people/groups/${encodeURIComponent(id)}`
}

function headerIcon(): string {
  return typeof window.prksPageHeaderIconHtml === 'function' ? window.prksPageHeaderIconHtml('folders') : ''
}

function readDraft(root: HTMLElement): PersonGroupFieldDraft {
  const value = (selector: string) => {
    const field = root.querySelector(selector)
    return field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement ? field.value : ''
  }
  return {
    name: value('#gd-name').trim(),
    description: value('#gd-description'),
    parent_id: value('#gd-parent-id').trim(),
    parent_name: value('#gd-parent-search'),
  }
}

function viewStill(groupId: string, session: number): boolean {
  return group.value?.id === groupId && (intents?.editSession() ?? 0) === session && props.projection.editing
}

async function onSave(): Promise<void> {
  const root = rootEl.value
  const current = group.value
  const baseline = fieldBaseline.value
  const btn = root?.querySelector('#gd-save-btn')
  if (!root || !current || !baseline || !intents || !(btn instanceof HTMLButtonElement)) return
  const session = intents.editSession()
  const draft = readDraft(root)
  const busy = window.prksSetButtonBusy
  if (typeof busy === 'function') busy(btn, true, { busyLabel: 'Saving…' })
  try {
    const result = await intents.save(current.id, draft, baseline, session)
    if (!result || !result.ok || result.quiet || !viewStill(current.id, session)) return
    intents.cancelEdit()
  } finally {
    if (typeof busy === 'function') busy(btn, false)
  }
}

async function onDelete(): Promise<void> {
  const current = group.value
  if (!current || !intents) return
  const session = intents.editSession()
  await withBusy('delete', async () => {
    await intents.remove(current.id, session)
  })
}

function onCancel(): void {
  intents?.cancelEdit()
}

function onToggleMembers(): void {
  intents?.toggleMembers()
}

function stampEditor(): void {
  const root = rootEl.value
  const current = group.value
  if (!root || !current) return
  const editor = root.querySelector('.group-sidebar-pane--edit')
  if (editor instanceof HTMLElement && editing.value) {
    const name = editor.querySelector('#gd-name')
    const session = String(intents?.editSession() ?? 0)
    if (name instanceof HTMLInputElement && name.dataset.prksGroupDraft !== session) {
      const stored = intents?.capturedBaseline(current.id)
      const parent = current.parent
      const baseline: PersonGroupFieldDraft = stored || {
        name: current.name,
        description: current.description,
        parent_id: parent?.id || '',
        parent_name: parent?.name || '',
      }
      fieldBaseline.value = baseline
      name.value = baseline.name
      name.dataset.prksGroupDraft = session
      const descriptionField = editor.querySelector('#gd-description')
      if (descriptionField instanceof HTMLTextAreaElement) descriptionField.value = baseline.description
      const hidden = editor.querySelector('#gd-parent-id')
      const search = editor.querySelector('#gd-parent-search')
      if (hidden instanceof HTMLInputElement) hidden.value = baseline.parent_id
      if (search instanceof HTMLInputElement) search.value = baseline.parent_name
    } else if (!fieldBaseline.value) {
      fieldBaseline.value = intents?.capturedBaseline(current.id) || null
    }
    const save = editor.querySelector('#gd-save-btn')
    if (save instanceof HTMLButtonElement) save.onclick = () => { void onSave() }
    const remove = editor.querySelector('#gd-delete-btn')
    if (remove instanceof HTMLButtonElement) remove.onclick = () => { void onDelete() }
  }
  intents?.bindChrome()
}

onMounted(stampEditor)
onUpdated(stampEditor)
</script>

<template>
  <div ref="rootEl" data-prks-person-group-detail-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Group not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">This item is not available offline.</PrksInlineMessage>
    </template>
    <template v-else-if="notFound">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Group not found</h2>
      </div>
      <p class="meta-row"><a href="#/people/groups" class="route-sidebar__link">Back to groups</a></p>
    </template>
    <template v-else-if="group">
      <div class="prks-page-header page-header page-header--split">
        <h2 class="prks-page-title">
          <span v-if="headerIcon()" v-html="headerIcon()"></span>
          {{ group.name }}
        </h2>
      </div>
      <p class="meta-row meta-row--lede">
        <a href="#/people/groups" class="route-sidebar__link">All groups</a>
        <template v-if="group.parent">
          <span aria-hidden="true"> › </span>
          <a :href="groupHref(group.parent.id)" class="route-sidebar__link">{{ group.parent.name }}</a>
        </template>
      </p>
      <div
        class="document-view document-view--person document-view--group-detail"
        :class="{ 'is-group-members-editing': membersEditing }"
      >
        <div class="doc-content">
          <div v-if="editing" class="group-sidebar-pane group-sidebar-pane--edit">
            <h3 class="group-sidebar__title">Edit group</h3>
            <div class="form-pane group-sidebar-form">
              <section class="group-sidebar-form__section">
                <h4>Identity</h4>
                <PrksField label="Name" for-id="gd-name">
                  <input id="gd-name" type="text" />
                </PrksField>
              </section>
              <section class="group-sidebar-form__section">
                <h4>Hierarchy</h4>
                <div class="group-sidebar__label-with-hint">
                  <label for="gd-parent-search" class="group-sidebar__label-text">Parent group</label>
                  <span v-if="parentHint" v-html="parentHint"></span>
                </div>
                <p class="meta-row">
                  Choose an existing Group, leave blank for top-level, or type a new name to create a parent when saving.
                </p>
                <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                  <div class="tag-add-shell__field">
                    <input
                      id="gd-parent-search"
                      type="text"
                      class="tag-add-shell__input"
                      placeholder="Search or type a new parent name…"
                      autocomplete="off"
                      aria-label="Search parent group"
                    />
                  </div>
                  <input id="gd-parent-id" type="hidden" value="" />
                  <div id="gd-parent-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                </div>
              </section>
              <section class="group-sidebar-form__section">
                <h4>Description</h4>
                <label for="gd-description" class="sr-only">Description</label>
                <textarea id="gd-description" class="prks-textarea prks-textarea--short"></textarea>
              </section>
            </div>
            <div class="form-actions prks-form-actions--split group-sidebar__sticky-actions">
              <PrksButton data-prks-group-edit-cancel @click="onCancel">Cancel</PrksButton>
              <PrksButton id="gd-save-btn" variant="primary">Save changes</PrksButton>
            </div>
            <details class="group-sidebar__advanced">
              <summary>Advanced</summary>
              <PrksButton
                id="gd-delete-btn"
                variant="danger"
                class="group-sidebar__delete"
                :busy="actionBusy('delete')"
                busy-label="Deleting…"
              >
                Delete group
              </PrksButton>
            </details>
          </div>
          <section v-else class="group-detail__section" aria-labelledby="group-description-heading">
            <h3 id="group-description-heading">Description</h3>
            <p class="group-detail__description">{{ description || 'No description yet.' }}</p>
          </section>
          <section class="group-detail__section" aria-labelledby="group-hierarchy-heading">
            <h3 id="group-hierarchy-heading">Hierarchy</h3>
            <div class="group-detail__relationship">
              <span>Parent</span>
              <span>
                <a v-if="group.parent" :href="groupHref(group.parent.id)" class="route-sidebar__link">{{ group.parent.name }}</a>
                <template v-else>Top-level group</template>
              </span>
            </div>
            <div class="group-detail__relationship">
              <span>Subgroups · {{ group.children.length }}</span>
              <ul v-if="group.children.length" class="person-link-list group-detail__relationship-list">
                <li v-for="child in group.children" :key="child.id">
                  <a :href="groupHref(child.id)" class="route-sidebar__link">{{ child.name }}</a>
                </li>
              </ul>
              <span v-else class="meta-row">No subgroups.</span>
            </div>
          </section>
          <section class="group-detail__section group-detail__members" aria-labelledby="group-members-heading">
            <div class="group-detail__section-head">
              <h3 id="group-members-heading">Members</h3>
              <span class="group-detail__count">{{ group.members.length }}</span>
              <PrksButton
                size="sm"
                data-prks-group-members-toggle
                :data-prks-role="membersEditing ? undefined : 'group-mutation-control'"
                @click="onToggleMembers"
              >
                {{ membersEditing ? 'Done' : 'Manage members' }}
              </PrksButton>
            </div>
            <div v-if="membersEditing" class="group-detail__member-add">
              <p class="tag-add-field__caption">Add a person</p>
              <div class="tag-add-shell combobox-container">
                <input id="group-add-member-id" type="hidden" value="" />
                <div class="tag-add-shell__field">
                  <input
                    id="group-add-member-search"
                    type="text"
                    class="tag-add-shell__input"
                    placeholder="Search by name, alias, group, or role…"
                    maxlength="200"
                    autocomplete="off"
                    aria-label="Search person to add to group"
                  />
                </div>
                <div id="group-add-member-results" class="combobox-results combobox-results--tag-panel hidden"></div>
              </div>
              <PrksButton id="group-add-member-btn" variant="primary">Add to group</PrksButton>
            </div>
            <div
              v-if="group.members.length"
              class="prks-people-library__scroll prks-people-library__scroll--embedded"
              data-prks-group-members-host
            >
              <div class="prks-people-list" role="list">
                <div
                  v-for="member in group.members"
                  :key="member.id"
                  class="prks-people-list__row"
                  :class="{ 'prks-people-list__row--removable': membersEditing }"
                  role="listitem"
                  :data-person-id="member.id"
                >
                  <button
                    v-if="membersEditing"
                    type="button"
                    class="prks-people-list__remove"
                    :data-remove-member="member.id"
                    aria-label="Remove from group"
                    title="Remove from group"
                  >
                    &times;
                  </button>
                  <span class="prks-people-list__toggle-spacer" aria-hidden="true"></span>
                  <div class="prks-people-list__body">
                    <a class="prks-people-list__link" :href="personHref(member.id)">
                      <span class="prks-people-list__title-row">
                        <span class="prks-people-list__title">{{ memberName(member) }}</span>
                        <span v-if="member.lifespan" class="prks-people-list__lifespan">{{ member.lifespan }}</span>
                      </span>
                    </a>
                  </div>
                </div>
              </div>
            </div>
            <p v-else class="meta-row">No members in this group yet.</p>
          </section>
        </div>
      </div>
    </template>
  </div>
</template>
