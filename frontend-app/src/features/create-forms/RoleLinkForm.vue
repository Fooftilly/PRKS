<script setup lang="ts">
/**
 * Link Person to Work (#1036). ModalHost renders it as the body of the
 * `#role-modal` ModalDialog.
 *
 * Vue owns the pickers, the role choice, the busy state and the messages.
 * The shared role-credit picker (`role-link` prefix), person quick-create,
 * and the opener checks plus durable write (`prksLinkPersonFromRoleModal`)
 * stay classic. The hidden ids hold the picked Person and File, as before.
 */
import { nextTick, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import { useDialogLifecycle } from '../../modal/dialogs'
import { parseRoute } from '../../routing/route-model'
import { fieldValue, setFieldValue } from './dom'
import { useInlineCombobox } from './new-file/combobox'
import { LINK_ROLES, ROLE_LINK_NEEDS_BOTH, roleLinkInputReady, submitRoleLink, type RoleLinkResult } from './role-link/save'

const PREFIX = 'role-link'

interface PersonRow {
  id: string | number
  first_name?: string
  last_name?: string
  aliases?: string
}

interface WorkRow {
  id: string | number
  title?: string
}

interface ListResult<T> {
  value?: T[] | null
}

interface RoleLinkWindow {
  allPersons?: PersonRow[]
  prksSetPersonCatalogue?: (persons: PersonRow[]) => void
  prksOfflinePeopleFetch?: () => Promise<ListResult<PersonRow> | null>
  prksOfflineWorksBrowseFetch?: () => Promise<ListResult<WorkRow> | null>
  fetchPersons?: () => Promise<PersonRow[] | null>
  fetchWorks?: () => Promise<WorkRow[] | null>
  personMatchesComboboxQuery?: (person: PersonRow, queryLower: string) => boolean
  formatPersonComboboxSubtitle?: (person: PersonRow) => string
  personDisplayName?: (person: PersonRow) => string
  prksFocusedEntity?: (type: string) => (WorkRow & { roles?: unknown[] }) | null
  prksBindRoleCreditPicker?: (prefix: string) => void
  prksRefreshRoleCreditPicker?: (prefix: string, person: PersonRow | null) => void
  prksResolveRoleCreditNameForLink?: (prefix: string, personId: string, searchInputId: string) => string
  prksQuickCreatePersonForSearchField?: (
    typedName: string,
    searchInputId: string,
    hiddenInputId: string,
    aboutText: string,
    options: { stillApplies: () => boolean },
  ) => Promise<unknown>
  prksLinkPersonFromRoleModal?: (input: {
    person_id: string
    work_id: string
    role_type: string
    credit_name: string
  }) => Promise<RoleLinkResult>
  prksShowDuplicateRoleLinkAlert?: (roleType: string) => Promise<void> | void
  prksAlertDialog?: (opts: { title: string; message: string }) => Promise<void>
  prksRoleTypeIconHtml?: (role: string, opts?: { size?: string }) => string
  prksRoleTypeShortLabel?: (role: string) => string
  closeModals?: () => void
}

function runtime(): RoleLinkWindow {
  return window as unknown as RoleLinkWindow
}

const role = ref<string>('Author')
const error = ref('')
const busy = ref(false)
const personLocked = ref(false)
const works = ref<WorkRow[]>([])
/** Each opening's number. Work that outlives its opening changes nothing. */
let opening = 0
let quietFocus = false

// ---- People --------------------------------------------------------------

interface PersonOption {
  key: string
  person: PersonRow
  label: string
  subtitle: string
}

const personRows = ref<PersonOption[]>([])
const createLabel = ref('')

function persons(): PersonRow[] {
  const shared = runtime().allPersons
  return Array.isArray(shared) ? shared : []
}

function personLabel(person: PersonRow): string {
  return `${person.first_name || ''} ${person.last_name || ''}`.trim()
}

function renderPeople(): void {
  const raw = fieldValue('role-person-search')
  const query = raw.toLowerCase()
  const w = runtime()
  const matches = typeof w.personMatchesComboboxQuery === 'function' ? w.personMatchesComboboxQuery : null
  createLabel.value = raw.trim()
  personRows.value = persons()
    .filter((person) => (matches ? matches(person, query) : personLabel(person).toLowerCase().includes(query)))
    .map((person) => ({
      key: String(person.id),
      person,
      label: personLabel(person),
      subtitle: typeof w.formatPersonComboboxSubtitle === 'function' ? w.formatPersonComboboxSubtitle(person) : '',
    }))
  people.shown()
}

// Enter picks the first real match; it quick-creates only when nothing matches.
const people = useInlineCombobox('role-person-search', 'role-person-results', { onOpen: renderPeople, enterCreates: true })

function refreshPicker(person: PersonRow | null): void {
  runtime().prksRefreshRoleCreditPicker?.(PREFIX, person)
}

function pickPerson(row: PersonOption): void {
  setFieldValue('role-person-search', row.label)
  setFieldValue('role-person-id', String(row.person.id))
  refreshPicker(row.person)
  error.value = ''
  people.hide()
}

function quickCreate(): void {
  const typedName = createLabel.value
  people.hide()
  if (!typedName || personLocked.value) return
  const create = runtime().prksQuickCreatePersonForSearchField
  if (typeof create !== 'function') return
  const mine = opening
  const stillApplies = () => mine === opening
  personLocked.value = true
  // The helper selects the new Person (search, hidden id and credit picker)
  // only while this opening still applies, and reports its own failure.
  void create(typedName, 'role-person-search', 'role-person-id', 'Quick-created from Link Person to Work', {
    stillApplies,
  }).finally(() => {
    if (stillApplies()) personLocked.value = false
  })
}

function onPersonFocus(): void {
  if (quietFocus) return
  renderPeople()
}

function onPersonInput(): void {
  setFieldValue('role-person-id', '')
  error.value = ''
  renderPeople()
}

// ---- Files ---------------------------------------------------------------

interface WorkOption {
  key: string
  work: WorkRow
  label: string
}

const workRows = ref<WorkOption[]>([])

function renderWorks(): void {
  const query = fieldValue('role-work-search').toLowerCase()
  workRows.value = works.value
    .filter((work) => String(work.title || '').toLowerCase().includes(query))
    .map((work) => ({ key: String(work.id), work, label: String(work.title || '') }))
  files.shown()
}

const files = useInlineCombobox('role-work-search', 'role-work-results', { onOpen: renderWorks })

function pickWork(row: WorkOption): void {
  setFieldValue('role-work-search', row.label)
  setFieldValue('role-work-id', String(row.work.id))
  error.value = ''
  files.hide()
}

function onWorkInput(): void {
  setFieldValue('role-work-id', '')
  error.value = ''
  renderWorks()
}

// ---- Role ----------------------------------------------------------------

function roleIcon(label: string): string {
  return runtime().prksRoleTypeIconHtml?.(label, { size: 'sm' }) ?? ''
}

function roleShort(label: string): string {
  return runtime().prksRoleTypeShortLabel?.(label) ?? label
}

function onRoleKeydown(event: KeyboardEvent): void {
  const step: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }
  const labels: readonly string[] = LINK_ROLES
  const at = Math.max(0, labels.indexOf(role.value))
  let next: string | undefined
  if (event.key in step) next = labels[(at + step[event.key] + labels.length) % labels.length]
  else if (event.key === 'Home') next = labels[0]
  else if (event.key === 'End') next = labels[labels.length - 1]
  if (!next) return
  event.preventDefault()
  role.value = next
  const group = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  void nextTick(() => {
    group?.querySelector<HTMLElement>(`.prks-segmented__btn[data-value="${next}"]`)?.focus()
  })
}

// ---- Opening -------------------------------------------------------------

async function listOf<T>(
  projected: (() => Promise<ListResult<T> | null>) | undefined,
  fallback: (() => Promise<T[] | null>) | undefined,
): Promise<T[]> {
  try {
    const result = typeof projected === 'function' ? await projected() : null
    if (result && Array.isArray(result.value)) return result.value
  } catch {
    /* Fall back to the plain read below. */
  }
  if (typeof fallback !== 'function') return []
  try {
    const rows = await fallback()
    return Array.isArray(rows) ? rows : []
  } catch {
    return []
  }
}

function resetFields(): void {
  opening += 1
  setFieldValue('role-person-search', '')
  setFieldValue('role-person-id', '')
  setFieldValue('role-work-search', '')
  setFieldValue('role-work-id', '')
  refreshPicker(null)
  role.value = 'Author'
  error.value = ''
  busy.value = false
  personLocked.value = false
  people.hideNow()
  files.hideNow()
}

/**
 * A blank form, both catalogues, and the opener's Person or File. People and
 * Files load together: linking an existing Person works offline, so each
 * reads the offline projection first and its plain fetch only as a fallback.
 */
async function prepare(stillOpening: () => boolean): Promise<void> {
  resetFields()
  const mine = opening
  const w = runtime()
  const [peopleList, workList] = await Promise.all([
    listOf<PersonRow>(w.prksOfflinePeopleFetch, w.fetchPersons),
    listOf<WorkRow>(w.prksOfflineWorksBrowseFetch, w.fetchWorks),
  ])
  if (mine !== opening || !stillOpening()) return
  if (typeof w.prksSetPersonCatalogue === 'function') w.prksSetPersonCatalogue(peopleList)
  else w.allPersons = peopleList
  works.value = workList

  const route = parseRoute(window.location.hash || '')
  if (route.name === 'person') {
    const personId = String((route.params as { personId?: string }).personId || '')
    const person = peopleList.find((row) => String(row.id) === personId)
    if (person) {
      setFieldValue('role-person-id', String(person.id))
      setFieldValue('role-person-search', w.personDisplayName ? w.personDisplayName(person) : personLabel(person))
      refreshPicker(person)
    }
  }
  let workId = route.name === 'work' ? String((route.params as { workId?: string }).workId || '') : ''
  const focused = typeof w.prksFocusedEntity === 'function' ? w.prksFocusedEntity('work') : null
  if (!workId && focused && focused.id != null) workId = String(focused.id)
  if (workId) {
    // The focused Work is the fallback, not only the catalogue: the dialog was
    // opened from that file, so a list that could not be read must not hide it.
    const work =
      workList.find((row) => String(row.id) === workId) ||
      (focused && String(focused.id) === workId ? focused : null)
    if (work) {
      setFieldValue('role-work-id', String(work.id))
      setFieldValue('role-work-search', String(work.title || ''))
    }
  }
  await nextTick()
}

/** Focus lands in Person without opening its list. */
function focusInitial(): void {
  const el = document.getElementById('role-person-search')
  if (!(el instanceof HTMLInputElement)) return
  quietFocus = true
  try {
    el.focus({ preventScroll: true })
  } finally {
    quietFocus = false
  }
}

/** Escape's inner layer: an open result list. */
function closeOpenList(): boolean {
  return people.close() || files.close()
}

// ---- Create Link ---------------------------------------------------------

async function onSave(): Promise<void> {
  if (busy.value) return
  const w = runtime()
  const personId = fieldValue('role-person-id').trim()
  const input = {
    person_id: personId,
    work_id: fieldValue('role-work-id').trim(),
    role_type: role.value,
    credit_name: '',
  }
  if (!roleLinkInputReady(input)) {
    error.value = ROLE_LINK_NEEDS_BOTH
    return
  }
  error.value = ''
  input.credit_name =
    typeof w.prksResolveRoleCreditNameForLink === 'function'
      ? String(w.prksResolveRoleCreditNameForLink(PREFIX, personId, 'role-person-search') || '').trim()
      : ''
  const mine = opening
  busy.value = true
  try {
    await submitRoleLink(input, {
      link: (next) => {
        const link = w.prksLinkPersonFromRoleModal
        return typeof link === 'function' ? link(next) : Promise.resolve({ code: 'failed' })
      },
      duplicateAlert: async (roleType) => {
        await w.prksShowDuplicateRoleLinkAlert?.(roleType)
      },
      failureAlert: async (message) => {
        await w.prksAlertDialog?.({ title: 'Could not link', message })
      },
      close: () => {
        // The busy state belongs to this opening, which is closing.
        busy.value = false
        w.closeModals?.()
      },
      current: () => mine === opening,
    })
  } finally {
    // A newer opening owns the button now; an older Save leaves it alone.
    if (mine === opening) busy.value = false
  }
}

onMounted(() => {
  runtime().prksBindRoleCreditPicker?.(PREFIX)
})

// The dialog stays inert until both catalogues and the opener's Person or
// File load.
useDialogLifecycle({
  opened: (opening) => prepare(opening.isCurrent),
  focusInitial,
  dismissInnerLayer: closeOpenList,
})

defineExpose({ prepare, focusInitial, closeOpenList })
</script>

<template>
  <div class="role-link-form">
    <p class="modal-helper">Search for someone already in your library, or type a name and choose <strong>Quick-create person</strong> in the list. Then click Create Link.</p>
    <div class="form-pane">
      <p
        id="role-link-error"
        class="folder-field-error"
        :class="{ hidden: !error }"
        role="alert"
      >{{ error }}</p>
      <label for="role-person-search">Person</label>
      <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
        <div class="tag-add-shell__field">
          <span class="tag-add-shell__icon"><i data-lucide="search" class="prks-icon" aria-hidden="true"></i></span>
          <input
            id="role-person-search"
            type="text"
            class="tag-add-shell__input"
            placeholder="Search person…"
            autocomplete="off"
            :readonly="personLocked"
            :aria-busy="personLocked ? 'true' : undefined"
            @focus="onPersonFocus"
            @input="onPersonInput"
            @blur="people.onBlur"
          >
        </div>
        <input id="role-person-id" type="hidden">
        <div
          id="role-person-results"
          class="combobox-results combobox-results--tag-panel"
          :class="{ hidden: people.hidden.value, 'is-open': people.open.value }"
        >
          <div
            v-if="createLabel"
            class="result-item result-item--create"
            @mousedown.prevent="quickCreate"
          >Quick-create person "{{ createLabel }}"</div>
          <div v-if="personRows.length === 0 && !createLabel" class="result-item no-results">No results found</div>
          <div
            v-for="row in personRows"
            :key="row.key"
            class="result-item result-item--person-pick"
            @mousedown.prevent="pickPerson(row)"
          >
            <div class="result-item__primary">{{ row.label || '(Unnamed)' }}</div>
            <div v-if="row.subtitle" class="result-item__secondary">{{ row.subtitle }}</div>
          </div>
        </div>
      </div>
      <label for="role-work-search">File</label>
      <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
        <div class="tag-add-shell__field">
          <span class="tag-add-shell__icon"><i data-lucide="search" class="prks-icon" aria-hidden="true"></i></span>
          <input
            id="role-work-search"
            type="text"
            class="tag-add-shell__input"
            placeholder="Search file…"
            autocomplete="off"
            @focus="renderWorks"
            @input="onWorkInput"
            @blur="files.onBlur"
          >
        </div>
        <input id="role-work-id" type="hidden">
        <div
          id="role-work-results"
          class="combobox-results combobox-results--tag-panel"
          :class="{ hidden: files.hidden.value, 'is-open': files.open.value }"
        >
          <div v-if="workRows.length === 0" class="result-item no-results">No results found</div>
          <div
            v-for="row in workRows"
            :key="row.key"
            class="result-item"
            @mousedown.prevent="pickWork(row)"
          >{{ row.label }}</div>
        </div>
      </div>
      <span id="role-type-label" class="prks-field__label">Role</span>
      <div class="prks-upload-person-stack__roles prks-upload-person-stack__roles--tiles prks-upload-person-stack__roles--tiles-2row prks-upload-person-stack__roles--tiles-link">
        <div id="role-role-seg-mount" class="prks-upload-role-seg">
          <div class="prks-segmented-wrap prks-segmented-wrap--compact">
            <input id="role-type" type="hidden" :value="role">
            <div
              class="prks-segmented prks-segmented--roles prks-segmented--roles-icons prks-segmented--compact"
              role="radiogroup"
              aria-labelledby="role-type-label"
              @keydown="onRoleKeydown"
            >
              <button
                v-for="label in LINK_ROLES"
                :key="label"
                type="button"
                class="prks-segmented__btn"
                :class="{ 'prks-segmented__btn--active': role === label }"
                :data-value="label"
                role="radio"
                :aria-checked="role === label ? 'true' : 'false'"
                :aria-label="label"
                :tabindex="role === label ? 0 : -1"
                @click="role = label"
              >
                <span class="prks-segmented__btn-icon" v-html="roleIcon(label)"></span>
                <span class="prks-segmented__btn-label">{{ roleShort(label) }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div id="role-link-credit-wrap" class="prks-role-credit-picker" hidden>
        <label class="prks-role-credit-picker__heading" for="role-link-credit-input">Name on this file</label>
        <p id="role-link-credit-profile" class="prks-role-credit-picker__profile meta-row meta-row--hint"></p>
        <div id="role-link-credit-shell" class="tag-add-shell tag-add-shell--flush prks-role-credit-picker__shell prks-role-credit-picker__shell--off">
          <div class="tag-add-shell__field prks-role-credit-picker__field">
            <label class="prks-role-credit-picker__check" for="role-link-credit-enable" title="Use a different name on this file">
              <input id="role-link-credit-enable" type="checkbox" class="prks-role-credit-picker__enable-input" aria-label="Use a different name on this file">
            </label>
            <input id="role-link-credit-input" type="text" class="tag-add-shell__input prks-role-credit-picker__input" disabled placeholder="Uses profile name" autocomplete="off" aria-label="Name shown on this file">
          </div>
        </div>
        <div id="role-link-credit-aliases" class="prks-role-credit-picker__aliases" hidden role="group" aria-label="Known aliases"></div>
        <p class="meta-row meta-row--hint meta-row--compact">Check the box to override; pick an alias or type a name.</p>
      </div>
      <PrksButton id="save-role-btn" variant="primary" :busy="busy" busy-label="Linking…" @click="onSave">Create Link</PrksButton>
    </div>
  </div>
</template>
