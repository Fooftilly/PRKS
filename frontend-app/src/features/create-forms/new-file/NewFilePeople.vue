<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { fieldValue, setFieldValue } from '../dom'
import { PEOPLE_ROLES } from '../../../domain/people-roles'
import { useInlineCombobox } from './combobox'
import { runtime, type PersonRow } from './runtime'
import { useNewFileState, type UploadRole } from './state'

/*
 * People on this file. Picking a person adds them at once (Author, or the
 * first role they do not hold yet). Each row owns its role, a "Name on file"
 * credit editor and Remove. The credit editor is the one shared `upload-role`
 * credit picker, teleported under the row being edited, so credit_name keeps
 * the picker's alias and override rules. Nothing is linked here: the roles
 * go out with the one canonical create.
 */

const PREFIX = 'upload-role'
const state = useNewFileState()

interface PersonOption {
  key: string
  person: PersonRow
  label: string
  subtitle: string
}

const rows = ref<PersonOption[]>([])
const createLabel = ref('')

function persons(): PersonRow[] {
  const shared = runtime().allPersons
  return Array.isArray(shared) ? shared : state.persons.value
}

function render(): void {
  const raw = fieldValue('upload-person-search')
  const query = raw.toLowerCase()
  const w = runtime()
  const matches = typeof w.personMatchesComboboxQuery === 'function' ? w.personMatchesComboboxQuery : null
  createLabel.value = raw.trim()
  rows.value = persons()
    .filter((person) => (matches ? matches(person, query) : true))
    .map((person) => ({
      key: String(person.id),
      person,
      label: `${person.first_name || ''} ${person.last_name || ''}`.trim(),
      subtitle: typeof w.formatPersonComboboxSubtitle === 'function' ? w.formatPersonComboboxSubtitle(person) : '',
    }))
  combobox.shown()
}

const combobox = useInlineCombobox('upload-person-search', 'person-results', { onOpen: render })

function roleLabels(): readonly string[] {
  // The bibliographic subset (no Mentioned) from its domain owner.
  return PEOPLE_ROLES
}

function hasRole(list: readonly UploadRole[], personId: string, roleType: string): boolean {
  const has = runtime().prksWorkHasRoleLink
  if (typeof has === 'function') return has(list as UploadRole[], personId, roleType)
  return list.some((role) => role.person_id === personId && role.role_type === roleType)
}

function profileName(personId: string, fallback: string): string {
  const w = runtime()
  const person = typeof w.prksFindPersonInCache === 'function' ? w.prksFindPersonInCache(personId) : null
  const canonical = person && typeof w.prksPersonCanonicalName === 'function' ? w.prksPersonCanonicalName(person) : ''
  return canonical || fallback.trim() || 'Unnamed person'
}

function refreshPicker(person: PersonRow | null): void {
  runtime().prksRefreshRoleCreditPicker?.(PREFIX, person)
}

/** Adds the picked person. True when a row was added. */
function addPicked(): boolean {
  const personId = fieldValue('upload-person-id').trim()
  const typed = fieldValue('upload-person-search')
  if (!personId) {
    state.peopleError.value = 'Choose a person from the list, or quick-create one.'
    return false
  }
  const roleType = roleLabels().find((label) => !hasRole(state.roles.value, personId, label)) || ''
  if (!roleType) {
    state.peopleError.value = `${typed || 'This person'} already holds every role on this file.`
    return false
  }
  const w = runtime()
  const credit =
    typeof w.prksResolveRoleCreditNameForLink === 'function'
      ? w.prksResolveRoleCreditNameForLink(PREFIX, personId, 'upload-person-search')
      : ''
  const profile = profileName(personId, typed)
  state.roles.value = state.roles.value.concat([
    { person_id: personId, person_name: credit || profile, profile_name: profile, role_type: roleType, credit_name: credit },
  ])
  state.peopleError.value = ''
  setFieldValue('upload-person-id', '')
  setFieldValue('upload-person-search', '')
  refreshPicker(null)
  return true
}

function pick(row: PersonOption): void {
  setFieldValue('upload-person-search', row.label)
  setFieldValue('upload-person-id', String(row.person.id))
  // Choosing a person IS adding them: no separate Link step.
  refreshPicker(null)
  addPicked()
  combobox.hide()
}

function quickCreate(): void {
  const typedName = createLabel.value
  combobox.hide()
  if (!typedName || state.personLocked.value) return
  const w = runtime()
  // The search, its hidden id and the credit picker are shared: hold them
  // until this person exists, so a newer query cannot be overwritten (and
  // then cleared) by the older create.
  state.personLocked.value = true
  const opening = state.currentOpening()
  // Closed or reopened meanwhile: this create belongs to a form that no
  // longer exists, and must never touch or block the new one.
  const stillApplies = () => state.isOpening(opening)
  const done = (async () => {
    try {
      if (typeof w.prksQuickCreatePersonForSearchField !== 'function') return false
      await w.prksQuickCreatePersonForSearchField(
        typedName,
        'upload-person-search',
        'upload-person-id',
        'Quick-created from upload',
        { stillApplies },
      )
    } finally {
      // A reopened form was already unlocked by its reset (and may hold a
      // newer create's lock): only release our own.
      if (stillApplies()) state.personLocked.value = false
    }
    if (!stillApplies()) return false
    // The helper reports its own failure and resolves anyway; only a
    // selected id means the Person exists.
    return !!fieldValue('upload-person-id') && addPicked() === true
  })()
  // Create File waits for this, so a quick submit cannot snapshot People
  // without them.
  state.setPersonPending(opening, done)
}

function onFocus(): void {
  // The credit picker is shared: a new pick must never repaint the row being edited.
  if (state.creditIdx.value >= 0) closeCredit({ restoreFocus: false })
  render()
}

function onInput(): void {
  setFieldValue('upload-person-id', '')
  state.peopleError.value = ''
  render()
}

function changeRole(idx: number, event: Event): void {
  const select = event.target
  const row = state.roles.value[idx]
  if (!(select instanceof HTMLSelectElement) || !row) return
  const next = select.value.trim()
  if (!next || next === row.role_type) return
  const others = state.roles.value.filter((_role, i) => i !== idx)
  if (hasRole(others, row.person_id, next)) {
    select.value = row.role_type
    state.peopleError.value = `${row.profile_name || row.person_name || 'This person'} is already on this file as ${next}.`
    return
  }
  row.role_type = next
  state.peopleError.value = ''
}

function removeRole(idx: number): void {
  if (!(idx >= 0 && idx < state.roles.value.length)) return
  if (state.creditIdx.value >= 0) closeCredit({ restoreFocus: false })
  state.roles.value = state.roles.value.filter((_role, i) => i !== idx)
  state.peopleError.value = ''
  // Keep keyboard users in the list: the next row's Remove, else the search.
  void nextTick(() => {
    const last = state.roles.value.length - 1
    const next =
      document.querySelector<HTMLElement>(`#upload-roles-list [data-upload-role-remove="${Math.min(idx, last)}"]`) ||
      document.getElementById('upload-person-search')
    next?.focus()
  })
}

const creditTarget = computed(() =>
  state.creditIdx.value >= 0 ? `#upload-role-credit-slot-${state.creditIdx.value}` : '#upload-role-credit-parking',
)

function closeCredit(options?: { restoreFocus?: boolean }): void {
  const idx = state.creditIdx.value
  state.creditIdx.value = -1
  refreshPicker(null)
  if (idx < 0 || options?.restoreFocus === false) return
  void nextTick(() => {
    document.querySelector<HTMLElement>(`#upload-roles-list [data-upload-role-credit="${idx}"]`)?.focus()
  })
}

function toggleCredit(idx: number): void {
  const row = state.roles.value[idx]
  if (!row) return
  if (state.creditIdx.value === idx) {
    closeCredit()
    return
  }
  const w = runtime()
  const person = (typeof w.prksFindPersonInCache === 'function' && w.prksFindPersonInCache(row.person_id)) || {
    id: row.person_id,
    first_name: row.profile_name || row.person_name || '',
    last_name: '',
    aliases: '',
  }
  refreshPicker(person)
  w.prksSetRoleCreditPickerValue?.(PREFIX, row.credit_name || '')
  state.creditIdx.value = idx
  void nextTick(() => {
    const input = document.getElementById('upload-role-credit-input')
    const enable = document.getElementById('upload-role-credit-enable')
    const target = input instanceof HTMLInputElement && !input.disabled ? input : enable
    target?.focus()
  })
}

/** Mirror the shared picker into the row being edited, after its own handlers ran. */
function syncCredit(): void {
  const row = state.roles.value[state.creditIdx.value]
  if (!row) return
  const read = runtime().prksReadRoleCreditName
  const credit = typeof read === 'function' ? read(PREFIX) : ''
  row.credit_name = credit
  row.person_name = credit || row.profile_name || row.person_name
}

function onCreditKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && event.target instanceof HTMLElement && event.target.id === 'upload-role-credit-input') {
    event.preventDefault()
    closeCredit()
  }
}

function userIcon(): string {
  return runtime().prksIcon?.('user', { size: 'sm' }) ?? ''
}

function optionsFor(role: UploadRole): readonly string[] {
  const labels = roleLabels()
  return labels.includes(role.role_type) ? labels : labels.concat([role.role_type])
}

onMounted(() => {
  runtime().prksBindRoleCreditPicker?.(PREFIX)
})

/** Opening reset: the picker and the search start empty. */
function reset(): void {
  setFieldValue('upload-person-id', '')
  setFieldValue('upload-person-search', '')
  refreshPicker(null)
  combobox.hideNow()
}

defineExpose({ reset, close: combobox.close })
</script>

<template>
  <div id="upload-people-field" class="prks-field prks-upload-people">
    <label for="upload-person-search">People</label>
    <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
      <div class="tag-add-shell__field">
        <span class="tag-add-shell__icon"><i data-lucide="user-plus" class="prks-icon" aria-hidden="true"></i></span>
        <input
          id="upload-person-search"
          type="text"
          class="tag-add-shell__input"
          placeholder="Add a person…"
          autocomplete="off"
          aria-describedby="upload-people-help upload-people-error"
          :readonly="state.personLocked.value"
          :aria-busy="state.personLocked.value ? 'true' : undefined"
          @focus="onFocus"
          @input="onInput"
          @blur="combobox.onBlur"
        >
      </div>
      <input id="upload-person-id" type="hidden">
      <div
        id="person-results"
        class="combobox-results combobox-results--tag-panel"
        :class="{ hidden: combobox.hidden.value, 'is-open': combobox.open.value }"
      >
        <div
          v-if="createLabel"
          class="result-item result-item--create"
          @mousedown.prevent="quickCreate"
        >Quick-create person "{{ createLabel }}"</div>
        <div v-if="rows.length === 0 && !createLabel" class="result-item no-results">No results found</div>
        <div
          v-for="row in rows"
          :key="row.key"
          class="result-item result-item--person-pick"
          @mousedown.prevent="pick(row)"
        >
          <div class="result-item__primary">{{ row.label || '(Unnamed)' }}</div>
          <div v-if="row.subtitle" class="result-item__secondary">{{ row.subtitle }}</div>
        </div>
      </div>
    </div>
    <p
      id="upload-people-error"
      class="folder-field-error"
      :class="{ hidden: !state.peopleError.value }"
      data-prks-field-error="upload-people"
      role="alert"
    >{{ state.peopleError.value }}</p>
    <ul
      id="upload-roles-list"
      class="prks-upload-people-list"
      aria-label="People on this file"
      :hidden="state.roles.value.length === 0"
    >
      <li
        v-for="(role, idx) in state.roles.value"
        :key="`${role.person_id}:${idx}`"
        class="prks-upload-person-row"
        :data-upload-role-idx="idx"
      >
        <span class="prks-upload-person-row__icon" aria-hidden="true" v-html="userIcon()"></span>
        <span class="prks-upload-person-row__name">
          <span class="prks-upload-person-row__profile">{{ role.profile_name || role.person_name }}</span>
          <template v-if="role.credit_name.trim()"> <span class="prks-upload-person-row__credit">as “{{ role.credit_name.trim() }}”</span></template>
        </span>
        <label class="prks-sr-only" :for="`upload-role-row-${idx}`">Role for {{ role.profile_name || role.person_name }}</label>
        <select
          :id="`upload-role-row-${idx}`"
          class="prks-select prks-upload-person-row__role"
          :data-upload-role-select="idx"
          :value="role.role_type"
          @change="changeRole(idx, $event)"
        >
          <option v-for="label in optionsFor(role)" :key="label" :value="label">{{ label }}</option>
        </select>
        <button
          type="button"
          class="prks-btn prks-btn--ghost prks-btn--sm prks-upload-person-row__credit-btn"
          :data-upload-role-credit="idx"
          :aria-expanded="state.creditIdx.value === idx ? 'true' : 'false'"
          aria-controls="upload-role-credit-panel"
          :aria-label="`Name on this file for ${role.profile_name || role.person_name}`"
          @click="toggleCredit(idx)"
        >Name on file</button>
        <button
          type="button"
          class="prks-icon-btn prks-icon-btn--ghost prks-icon-btn--sm"
          :data-upload-role-remove="idx"
          :aria-label="`Remove ${role.profile_name || role.person_name} (${role.role_type})`"
          @click="removeRole(idx)"
        >&times;</button>
        <div :id="`upload-role-credit-slot-${idx}`" class="prks-upload-person-row__credit-slot"></div>
      </li>
    </ul>
    <p id="upload-people-help" class="meta-row meta-row--hint meta-row--compact" :hidden="state.roles.value.length > 0">
      Choosing a person adds them as Author. Change the role on their row.
    </p>
    <div id="upload-role-credit-parking" hidden>
      <Teleport defer :to="creditTarget" :disabled="state.creditIdx.value < 0">
        <div
          id="upload-role-credit-panel"
          class="prks-upload-credit-panel"
          @input="syncCredit"
          @change="syncCredit"
          @click="syncCredit"
          @keydown="onCreditKeydown"
        >
          <div id="upload-role-credit-wrap" class="prks-role-credit-picker" hidden>
            <label class="prks-role-credit-picker__heading" for="upload-role-credit-input">Name on this file</label>
            <p id="upload-role-credit-profile" class="prks-role-credit-picker__profile meta-row meta-row--hint"></p>
            <div id="upload-role-credit-shell" class="tag-add-shell tag-add-shell--flush prks-role-credit-picker__shell prks-role-credit-picker__shell--off">
              <div class="tag-add-shell__field prks-role-credit-picker__field">
                <label class="prks-role-credit-picker__check" for="upload-role-credit-enable" title="Use a different name on this file">
                  <input id="upload-role-credit-enable" type="checkbox" class="prks-role-credit-picker__enable-input" aria-label="Use a different name on this file">
                </label>
                <input id="upload-role-credit-input" type="text" class="tag-add-shell__input prks-role-credit-picker__input" disabled placeholder="Uses profile name" autocomplete="off" aria-label="Name shown on this file">
              </div>
            </div>
            <div id="upload-role-credit-aliases" class="prks-role-credit-picker__aliases" hidden role="group" aria-label="Known aliases"></div>
            <p class="meta-row meta-row--hint meta-row--compact">Check the box to override; pick an alias or type a name.</p>
          </div>
          <div class="prks-upload-credit-panel__actions">
            <button id="upload-role-credit-done" type="button" class="prks-btn prks-btn--secondary prks-btn--sm" @click="closeCredit()">Done</button>
          </div>
        </div>
      </Teleport>
    </div>
  </div>
</template>
