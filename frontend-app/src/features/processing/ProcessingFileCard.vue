<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import type { ProcessingIntents } from './intents'
import {
  PROCESSING_STATUS_LABELS,
  filterProcessingFolders,
  filterProcessingPeople,
  filterProcessingTags,
  folderTitle,
  processingFoldersAfterCreate,
  processingPeopleAfterCreate,
  processingWidgetPrefix,
  type ProcessingFileDraft,
  type ProcessingFileRow,
  type ProcessingFolder,
  type ProcessingPerson,
  type ProcessingTagOption,
} from './projection'

const props = defineProps<{
  file: ProcessingFileRow
  people: ProcessingPerson[]
  folders: ProcessingFolder[]
  roleTypes: string[]
  domPrefix: string
  visibleCount: number
  intents: ProcessingIntents
}>()

const emit = defineEmits<{
  people: [people: ProcessingPerson[]]
  folders: [folders: ProcessingFolder[]]
}>()

const rootEl = ref<HTMLElement | null>(null)
const statusHost = ref<HTMLElement | null>(null)
const docTypeHost = ref<HTMLElement | null>(null)
const roleHost = ref<HTMLElement | null>(null)
const personInput = ref<HTMLInputElement | null>(null)
const personResults = ref<HTMLElement | null>(null)
const tagInput = ref<HTMLInputElement | null>(null)
const tagResults = ref<HTMLElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)
const folderResults = ref<HTMLElement | null>(null)

const draft = reactive<ProcessingFileDraft>(cloneDraft(props.file.draft))
const message = ref('')
const saving = ref(false)
const importing = ref(false)
const personQuery = ref('')
const personId = ref('')
const personOpen = ref(false)
const tagQuery = ref('')
const tagOptions = ref<ProcessingTagOption[]>([])
const tagOpen = ref(false)
const folderQuery = ref(folderTitle(props.folders, props.file.draft.target_folder_id))
const folderOpen = ref(false)
let tagSearchToken = 0
let hideTimer = 0

const statusPrefix = processingWidgetPrefix(props.domPrefix, props.file.id, 'status')
const docPrefix = processingWidgetPrefix(props.domPrefix, props.file.id, 'doctype')
const rolePrefix = processingWidgetPrefix(props.domPrefix, props.file.id, 'role')
const linkIcon = computed(() => window.prksIcon?.('link', { size: 'sm' }) ?? '')
const plusIcon = computed(() => window.prksIcon?.('plus', { size: 'sm' }) ?? '')
const userIcon = computed(() => window.prksIcon?.('user', { size: 'sm' }) ?? '')
const searchIcon = computed(() => window.prksTagSearchIconHtml?.() ?? '')
const tagIcon = computed(() => window.prksTagPlusIconHtml?.() ?? window.prksTagSearchIconHtml?.() ?? '')

const personChoices = computed(() => {
  const match = window.personMatchesComboboxQuery
  return filterProcessingPeople(props.people, personQuery.value, match)
})
const folderChoices = computed(() => filterProcessingFolders(props.folders, folderQuery.value))
const tagFilter = computed(() => {
  const attached = new Set(draft.tags.map((tag) => tag.id))
  const match = window.prksTagMatchesQuery
  const exact = window.prksTagExactMatch
  return filterProcessingTags(
    tagOptions.value,
    tagQuery.value,
    attached,
    match
      ? (tag, query) => match(tag, query)
      : undefined,
    exact
      ? (tag, query) => exact(tag, query)
      : undefined,
  )
})

function cloneDraft(source: ProcessingFileDraft): ProcessingFileDraft {
  const published = window.prksIsoToDdMmYyyy?.(source.published_date) ?? source.published_date
  return {
    ...source,
    published_date: published,
    roles: source.roles.map((role) => ({ ...role })),
    tags: source.tags.map((tag) => ({ ...tag })),
  }
}

function showResults(input: HTMLInputElement | null, results: HTMLElement | null, open: { value: boolean }): void {
  open.value = true
  if (!input || !results) return
  if (typeof window.prksShowInlineComboboxResults === 'function') {
    window.prksShowInlineComboboxResults(input, results)
    return
  }
  results.classList.remove('hidden')
}

function hideResults(results: HTMLElement | null, open: { value: boolean }): void {
  open.value = false
  if (!results) return
  if (typeof window.prksHideInlineComboboxResults === 'function') {
    window.prksHideInlineComboboxResults(results)
    return
  }
  results.classList.add('hidden')
}

function scheduleHide(results: HTMLElement | null, open: { value: boolean }): void {
  window.clearTimeout(hideTimer)
  hideTimer = window.setTimeout(() => hideResults(results, open), 200)
}

function openPersonResults(): void {
  showResults(personInput.value, personResults.value, personOpen)
}

function onPersonInput(): void {
  personId.value = ''
  openPersonResults()
}

function blurPerson(): void {
  scheduleHide(personResults.value, personOpen)
}

function blurTags(): void {
  scheduleHide(tagResults.value, tagOpen)
}

function openFolderResults(): void {
  showResults(folderInput.value, folderResults.value, folderOpen)
}

function onFolderInput(): void {
  draft.target_folder_id = ''
  openFolderResults()
}

function blurFolder(): void {
  scheduleHide(folderResults.value, folderOpen)
}

function tagLabel(tag: ProcessingTagOption): string {
  const label = window.prksTagComboboxLabel
  if (typeof label === 'function') return label(tag, tagQuery.value.trim().toLowerCase())
  return tag.name
}

function personSubtitle(person: ProcessingPerson): string {
  const format = window.formatPersonComboboxSubtitle
  if (typeof format !== 'function') return ''
  return format(person.raw) || ''
}

function collectDraft(): ProcessingFileDraft {
  const root = rootEl.value
  const statusEl = root?.querySelector<HTMLInputElement>('[data-field="status_draft"]')
  const docEl = root?.querySelector<HTMLInputElement>('[data-field="doc_type"]')
  return {
    ...draft,
    status_draft: statusEl?.value || draft.status_draft,
    doc_type: docEl?.value || draft.doc_type,
    target_folder_id: draft.target_folder_id.trim(),
    roles: draft.roles.map((role) => ({
      person_id: role.person_id,
      person_name: role.person_name,
      role_type: role.role_type,
    })),
    tags: draft.tags.map((tag) => ({ id: tag.id, name: tag.name })),
  }
}

function mountWidgets(): void {
  const statusHtml = window.prksSegmentedControlHtml?.(
    statusPrefix,
    'File status',
    [...PROCESSING_STATUS_LABELS],
    draft.status_draft,
    'status',
    { dataField: 'status_draft' },
  )
  if (statusHost.value && statusHtml) {
    statusHost.value.innerHTML = statusHtml
    window.prksBindSegmentedHidden?.(statusPrefix)
  }
  const roleHtml = window.prksSegmentedControlHtml?.(
    rolePrefix,
    'Role for linked person',
    props.roleTypes,
    'Author',
    'roles',
    { compact: true, dataRole: 'role-type', withRoleIcons: true },
  )
  if (roleHost.value && roleHtml) {
    roleHost.value.innerHTML = roleHtml
    window.prksBindSegmentedHidden?.(rolePrefix)
  }
  const docHtml = window.prksDocTypeMenuShellHtml?.(docPrefix, draft.doc_type, false)
  if (docTypeHost.value && docHtml) {
    docTypeHost.value.innerHTML = docHtml
    const hidden = docTypeHost.value.querySelector<HTMLInputElement>('input[type="hidden"]')
    if (hidden) hidden.setAttribute('data-field', 'doc_type')
    window.initPrksDocTypeMenu?.(docPrefix, {})
  }
  window.prksRefreshIcons?.(rootEl.value)
}

function currentRoleType(): string {
  const hidden = rootEl.value?.querySelector<HTMLInputElement>('[data-role="role-type"]')
  return String(hidden?.value || 'Author').trim() || 'Author'
}

function addRole(): void {
  const selectedId = personId.value.trim()
  if (!selectedId) {
    message.value = 'Pick person from results first.'
    return
  }
  const roleType = currentRoleType()
  const person = props.people.find((row) => row.id === selectedId)
  const personName = person?.name || personQuery.value.trim() || selectedId
  const hasDup = window.prksWorkHasRoleLink
    ? window.prksWorkHasRoleLink(draft.roles, selectedId, roleType)
    : draft.roles.some((role) => role.person_id === selectedId && role.role_type === roleType)
  if (hasDup) {
    message.value = `Already linked as ${roleType}.`
    return
  }
  draft.roles.push({ person_id: selectedId, person_name: personName, role_type: roleType })
  personId.value = ''
  personQuery.value = ''
  message.value = ''
  window.prksRefreshIcons?.(rootEl.value)
}

function removeRole(index: number): void {
  draft.roles.splice(index, 1)
}

function removeTag(index: number): void {
  draft.tags.splice(index, 1)
}

function choosePerson(person: ProcessingPerson): void {
  personId.value = person.id
  personQuery.value = person.name || '(Unnamed)'
  hideResults(personResults.value, personOpen)
}

async function createPerson(): Promise<void> {
  const typed = personQuery.value.trim()
  hideResults(personResults.value, personOpen)
  const created = await props.intents.quickCreatePerson(typed)
  if (!created) return
  emit('people', processingPeopleAfterCreate(props.people, { id: created.id, name: created.name }))
  personId.value = created.id
  personQuery.value = created.name
}

function chooseFolder(folder: ProcessingFolder): void {
  draft.target_folder_id = folder.id
  folderQuery.value = folder.title
  hideResults(folderResults.value, folderOpen)
}

async function createFolder(): Promise<void> {
  const created = await props.intents.quickCreateFolder(folderQuery.value)
  if (!created) return
  emit('folders', processingFoldersAfterCreate(
    props.folders,
    created.foldersFailed ? null : created.folders,
    { id: created.id, title: created.title },
  ))
  draft.target_folder_id = created.id
  folderQuery.value = created.title
  hideResults(folderResults.value, folderOpen)
}

async function refreshTags(): Promise<void> {
  const token = ++tagSearchToken
  const rows = await props.intents.searchTags()
  if (rows == null || token !== tagSearchToken) return
  tagOptions.value = rows
  showResults(tagInput.value, tagResults.value, tagOpen)
}

function chooseTag(tag: ProcessingTagOption): void {
  if (!draft.tags.some((row) => row.id === tag.id)) {
    draft.tags.push({ id: tag.id, name: tag.name })
  }
  tagQuery.value = ''
  hideResults(tagResults.value, tagOpen)
}

async function createTag(): Promise<void> {
  const name = tagQuery.value.trim()
  if (!name) return
  const created = await props.intents.createTag(name)
  if (!created) return
  if (!draft.tags.some((row) => row.id === created.id)) {
    draft.tags.push({ id: created.id, name: created.name })
  }
  tagQuery.value = ''
  hideResults(tagResults.value, tagOpen)
}

function preview(): void {
  const placement = props.intents.setPreview({
    id: props.file.id,
    filename: props.file.filename,
    relPath: props.file.relPath,
    canPreview: props.file.canPreview,
  })
  if (placement === 'unavailable') message.value = 'Preview unavailable for this file.'
  else if (placement === 'card') message.value = 'Preview below this card.'
  else message.value = 'Preview opened on the right.'
}

async function save(): Promise<void> {
  saving.value = true
  const outcome = await props.intents.save(props.file.id, collectDraft())
  if (!rootEl.value?.isConnected) return
  saving.value = false
  if (outcome.status === 'success') message.value = 'Saved.'
  else if (outcome.status === 'error') message.value = outcome.message
}

async function importFile(): Promise<void> {
  importing.value = true
  try {
    const outcome = await props.intents.importFile(props.file.id, collectDraft(), {
      visibleCount: props.visibleCount,
    })
    if (!rootEl.value?.isConnected) return
    if (outcome.status === 'error') message.value = outcome.message
    else if (outcome.status === 'success') message.value = 'Imported to library.'
  } catch (err) {
    if (!rootEl.value?.isConnected) return
    message.value = err instanceof Error && err.message.trim()
      ? err.message.trim()
      : 'Could not refresh files for processing.'
  } finally {
    if (rootEl.value?.isConnected) importing.value = false
  }
}

onMounted(() => {
  mountWidgets()
})

onBeforeUnmount(() => {
  window.clearTimeout(hideTimer)
})
</script>

<template>
  <article class="project-card prks-processing-card" :data-processing-id="file.id">
    <header class="prks-processing-card__header">
      <div class="card-title prks-processing-card__title">{{ file.filename }}</div>
      <div class="prks-processing-card__meta">
        <p class="meta-row">
          <strong>Path:</strong>
          <code :title="file.relPath || undefined">{{ file.relPath }}</code>
        </p>
        <p class="meta-row"><strong>State:</strong> {{ file.statusLabel }} · {{ file.sourceHint }}</p>
        <p v-if="file.lastError" class="meta-row meta-row--error"><strong>Error:</strong> {{ file.lastError }}</p>
      </div>
    </header>
    <div ref="rootEl" class="form-pane form-pane--tight prks-processing-card__core">
      <div class="prks-processing-card__section">
        <div class="prks-processing-card__title-row">
          <label>Title</label>
          <input v-model="draft.title" type="text" data-field="title" placeholder="Library title">
        </div>
        <div class="prks-processing-card__status-field prks-work-upload-status-field">
          <label>Status</label>
          <div ref="statusHost" v-once class="work-html-slot" data-prks-processing-status-host></div>
        </div>
      </div>
      <div class="prks-processing-card__section">
        <div class="prks-processing-card__section-title">Link person to roles</div>
        <div class="prks-upload-person-stack">
          <div class="form-row prks-upload-person-stack__search">
            <div class="prks-combobox-with-action">
              <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                <div class="tag-add-shell__field">
                  <span class="work-html-slot" v-html="searchIcon"></span>
                  <input
                    ref="personInput"
                    v-model="personQuery"
                    type="text"
                    class="tag-add-shell__input"
                    placeholder="Search person from library…"
                    autocomplete="off"
                    aria-label="Search person"
                    @focus="openPersonResults"
                    @input="onPersonInput"
                    @blur="blurPerson"
                  >
                </div>
                <div
                  ref="personResults"
                  class="combobox-results combobox-results--tag-panel"
                  :class="{ hidden: !personOpen }"
                >
                  <div
                    v-if="personQuery.trim()"
                    class="result-item result-item--create"
                    @mousedown.prevent="createPerson()"
                  >Quick-create person "{{ personQuery.trim() }}"</div>
                  <div v-if="!personChoices.length && !personQuery.trim()" class="result-item no-results">No people found</div>
                  <div
                    v-for="person in personChoices"
                    :key="person.id"
                    class="result-item result-item--person-pick"
                    @mousedown.prevent="choosePerson(person)"
                  >
                    <div class="result-item__primary">{{ person.name || '(Unnamed)' }}</div>
                    <div v-if="personSubtitle(person)" class="result-item__secondary">{{ personSubtitle(person) }}</div>
                  </div>
                </div>
              </div>
            </div>
            <PrksButton variant="secondary" size="sm" @click="addRole">
              <span class="ribbon-btn__icon" v-html="linkIcon"></span>
              <span class="ribbon-btn__label">Link</span>
            </PrksButton>
          </div>
          <div class="prks-upload-person-stack__roles prks-upload-person-stack__roles--tiles">
            <div class="prks-upload-role-seg">
              <div ref="roleHost" v-once class="work-html-slot" data-prks-processing-role-host></div>
            </div>
          </div>
        </div>
        <div class="tag-cloud status-chip-list">
          <span v-if="!draft.roles.length" class="status-chip-list__empty">No persons linked yet</span>
          <span v-for="(role, index) in draft.roles" :key="`${role.person_id}-${role.role_type}-${index}`" class="tag author-tag">
            <span class="work-html-slot" v-html="userIcon"></span>
            {{ role.person_name || role.person_id }} ({{ role.role_type }})
            <button type="button" class="status-chip-remove" aria-label="Remove role link" @click="removeRole(index)">&times;</button>
          </span>
        </div>
      </div>
      <div class="prks-processing-card__section">
        <p class="tag-add-field__caption">Tags (optional)</p>
        <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
          <div class="tag-add-shell__field">
            <span class="work-html-slot" v-html="tagIcon"></span>
            <input
              ref="tagInput"
              v-model="tagQuery"
              type="text"
              class="tag-add-shell__input"
              placeholder="Search or create tag…"
              maxlength="300"
              autocomplete="off"
              aria-label="Add tag for processing file"
              @focus="refreshTags()"
              @input="refreshTags()"
              @blur="blurTags"
            >
          </div>
          <div
            ref="tagResults"
            class="combobox-results combobox-results--tag-panel"
            :class="{ hidden: !tagOpen }"
          >
            <div
              v-if="tagFilter.canCreate"
              class="result-item result-item--create"
              @mousedown.prevent="createTag()"
            >Create tag "{{ tagQuery.trim() }}"</div>
            <div v-if="!tagFilter.tags.length && !tagFilter.canCreate" class="result-item no-results">No tags found</div>
            <div
              v-for="tag in tagFilter.tags"
              :key="tag.id || tag.name"
              class="result-item"
              @mousedown.prevent="chooseTag(tag)"
            >{{ tagLabel(tag) }}</div>
          </div>
        </div>
        <div class="tag-cloud work-tags-list">
          <span v-if="!draft.tags.length" class="status-chip-list__empty">No tags selected</span>
          <span v-for="(tag, index) in draft.tags" :key="`${tag.id}-${index}`" class="tag work-tag-chip">
            {{ tag.name }}
            <button type="button" class="work-tag-remove" title="Remove" aria-label="Remove tag" @click="removeTag(index)">&times;</button>
          </span>
        </div>
      </div>
      <div class="prks-processing-card__section">
        <div class="prks-processing-card__section-title">Bibliographic</div>
        <div class="form-grid-2">
          <div>
            <label>Year</label>
            <input v-model="draft.year" type="text" data-field="year">
          </div>
          <div>
            <label>Published date</label>
            <input v-model="draft.published_date" type="text" data-field="published_date" placeholder="dd/mm/yyyy" inputmode="numeric" autocomplete="off">
          </div>
        </div>
        <div>
          <label :for="`${docPrefix}-trigger`">Document type (BibLaTeX)</label>
          <div ref="docTypeHost" v-once class="work-html-slot" data-prks-processing-doctype-host></div>
        </div>
        <div class="form-grid-2">
          <div>
            <label>Publisher</label>
            <input v-model="draft.publisher" type="text" data-field="publisher">
          </div>
          <div>
            <label>Location</label>
            <input v-model="draft.location" type="text" data-field="location">
          </div>
        </div>
      </div>
      <div class="prks-processing-card__section">
        <label>Folder (optional)</label>
        <p class="meta-row meta-row--hint">Placed in this folder when you import.</p>
        <div class="prks-combobox-with-action">
          <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
            <div class="tag-add-shell__field">
              <span class="work-html-slot" v-html="searchIcon"></span>
              <input
                ref="folderInput"
                v-model="folderQuery"
                type="text"
                class="tag-add-shell__input"
                placeholder="Search folder…"
                autocomplete="off"
                aria-label="Search folder"
                @focus="openFolderResults"
                @input="onFolderInput"
                @blur="blurFolder"
              >
            </div>
            <div
              ref="folderResults"
              class="combobox-results combobox-results--tag-panel"
              :class="{ hidden: !folderOpen }"
            >
              <div v-if="!folderChoices.length" class="result-item no-results">No folders found</div>
              <div
                v-for="folder in folderChoices"
                :key="folder.id"
                class="result-item"
                @mousedown.prevent="chooseFolder(folder)"
              >{{ folder.title }}</div>
            </div>
          </div>
          <PrksButton variant="secondary" size="sm" title="Create new folder" aria-label="Create new folder" @click="createFolder">
            <span class="ribbon-btn__icon" v-html="plusIcon"></span>
          </PrksButton>
        </div>
      </div>
      <details class="prks-processing-card__more">
        <summary>More metadata</summary>
        <div class="prks-processing-card__more-body">
          <label>Original URL</label>
          <input v-model="draft.source_url" type="url" data-field="source_url" placeholder="https://...">
          <div class="form-grid-2">
            <div>
              <label>Edition</label>
              <input v-model="draft.edition" type="text" data-field="edition">
            </div>
            <div>
              <label>Journal</label>
              <input v-model="draft.journal" type="text" data-field="journal">
            </div>
          </div>
          <div class="form-grid-2">
            <div>
              <label>Volume</label>
              <input v-model="draft.volume" type="text" data-field="volume">
            </div>
            <div>
              <label>Issue</label>
              <input v-model="draft.issue" type="text" data-field="issue">
            </div>
          </div>
          <div class="form-grid-2">
            <div>
              <label>Pages</label>
              <input v-model="draft.pages" type="text" data-field="pages">
            </div>
            <div>
              <label>ISBN</label>
              <input v-model="draft.isbn" type="text" data-field="isbn">
            </div>
          </div>
          <div class="form-grid-2">
            <div>
              <label>DOI</label>
              <input v-model="draft.doi" type="text" data-field="doi">
            </div>
            <div>
              <label>Thumbnail page</label>
              <input v-model="draft.thumb_page" type="number" min="1" step="1" data-field="thumb_page">
            </div>
          </div>
          <label>Abstract</label>
          <textarea v-model="draft.abstract" class="textarea-sm" data-field="abstract"></textarea>
          <label>Private notes</label>
          <textarea v-model="draft.private_notes" class="textarea-sm" data-field="private_notes"></textarea>
        </div>
      </details>
    </div>
    <div class="prks-form-actions prks-form-actions--split form-actions prks-processing-card__actions">
      <PrksButton variant="secondary" :disabled="!file.canPreview" @click="preview">Preview</PrksButton>
      <PrksButton variant="secondary" :busy="saving" :disabled="importing" busy-label="Saving…" @click="save">Save metadata</PrksButton>
      <PrksButton variant="primary" :busy="importing" :disabled="!file.canImport || saving" busy-label="Importing…" @click="importFile">Import to library</PrksButton>
    </div>
    <p class="meta-row prks-processing-card__message" aria-live="polite">{{ message }}</p>
  </article>
</template>
