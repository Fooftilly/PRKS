<script setup lang="ts">
/**
 * New File (Work) create form (#1036).
 *
 * The body teleports into `#work-modal-vue` and the footer (status, Cancel,
 * Create File) into `#work-modal-footer-vue`; the dialog shell and header
 * stay in `frontend/index.html` until ModalHost mounts feature forms. Text
 * fields are uncontrolled inputs, as in the other create forms: the dirty
 * snapshot and the shared widgets (doc-type menu, status segments, credit
 * picker) read and write them in the DOM.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { fieldValue, setFieldValue } from './dom'
import PrksButton from '../../components/PrksButton.vue'
import { createSubmitKeyHandler } from './keyboard'
import NewFileFolder from './new-file/NewFileFolder.vue'
import NewFilePeople from './new-file/NewFilePeople.vue'
import NewFilePlaylist from './new-file/NewFilePlaylist.vue'
import NewFileSource from './new-file/NewFileSource.vue'
import NewFileTags from './new-file/NewFileTags.vue'
import { defaultFolderId } from './new-file/folder'
import { loadList, runtime } from './new-file/runtime'
import { attachTagToWork, createPdfWork } from '../../api/works-create'
import { BAD_DATE, saveNewFile, type NewFileDraft, type NewFileField, type SourceKind } from './new-file/save'
import { createNewFileState, FIELD_CONTROL_IDS, NEW_FILE_STATE } from './new-file/state'

const state = createNewFileState()
provide(NEW_FILE_STATE, state)

const root = ref<HTMLElement | null>(null)
const source = ref<InstanceType<typeof NewFileSource> | null>(null)
const folder = ref<InstanceType<typeof NewFileFolder> | null>(null)
const people = ref<InstanceType<typeof NewFilePeople> | null>(null)
const tags = ref<InstanceType<typeof NewFileTags> | null>(null)
const playlist = ref<InstanceType<typeof NewFilePlaylist> | null>(null)

const DISCLOSURES = ['work-upload-biblio-details', 'work-upload-more-details'] as const

const moreTitle = computed(() =>
  state.kind.value === 'video' ? 'Playlist, tags, status & notes' : 'Tags, status & notes',
)

/** The dialog body shell: frozen while Create runs, so nothing can change what it sends. */
function shellBody(): HTMLElement | null {
  const el = root.value?.closest('.modal-body--scroll')
  return el instanceof HTMLElement ? el : null
}

function setBusy(on: boolean): void {
  state.busy.value = on
  const body = shellBody()
  if (!body) return
  if (on) {
    body.setAttribute('inert', '')
    body.setAttribute('aria-busy', 'true')
  } else {
    body.removeAttribute('inert')
    body.removeAttribute('aria-busy')
  }
}

/** A collapsed disclosure's body is inert, so Tab never lands out of sight. */
function syncDisclosureInert(): void {
  DISCLOSURES.forEach((id) => {
    const details = document.getElementById(id)
    const body = details?.querySelector('.work-upload-meta__details-body')
    if (!(details instanceof HTMLDetailsElement) || !body) return
    if (details.open) body.removeAttribute('inert')
    else body.setAttribute('inert', '')
  })
}

/** Collapsed disclosures say how much they hold, so nothing entered is silently out of sight. */
function refreshCounts(): void {
  const count = (id: string) => {
    const details = document.getElementById(id)
    if (!details) return 0
    let n = 0
    details.querySelectorAll<HTMLInputElement>('[data-prks-count]').forEach((el) => {
      if (el.closest('.hidden')) return
      const value = String(el.value || '').trim()
      if (!value) return
      const fallback = el.getAttribute('data-prks-count-default')
      if (fallback != null && value === fallback) return
      n += 1
    })
    return n
  }
  state.counts.biblio = count('work-upload-biblio-details')
  state.counts.more = count('work-upload-more-details')
}

let countFrame = 0
function scheduleCounts(): void {
  if (countFrame) return
  countFrame = window.requestAnimationFrame(() => {
    countFrame = 0
    refreshCounts()
  })
}

// Tags and the playlist also change without a form event (a quick-create
// settling): the section counts follow them.
watch([() => state.tags.value, () => state.playlistId.value], scheduleCounts)

/** Points at a field: the submit is over, and a control in a closed disclosure opens it first. */
function focusControl(el: HTMLElement | null): void {
  if (!el || typeof el.focus !== 'function') return
  setBusy(false)
  const details = el.closest('details.work-upload-meta__details')
  if (details instanceof HTMLDetailsElement && !details.open) {
    details.open = true
    syncDisclosureInert()
  }
  try {
    el.focus({ preventScroll: false })
    if (typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  } catch {
    el.focus()
  }
}

function focusField(field: NewFileField): void {
  focusControl(document.getElementById(FIELD_CONTROL_IDS[field]))
}

function parseDate(raw: string): string {
  const parse = runtime().prksParsePublishedDateInput
  return typeof parse === 'function' ? parse(raw) || '' : ''
}

/** Editing a date retracts its error; leaving it re-checks. */
function onDateInput(field: 'date' | 'videoDate'): void {
  state.errors[field] = ''
}

function onDateBlur(field: 'date' | 'videoDate'): void {
  const value = fieldValue(FIELD_CONTROL_IDS[field]).trim()
  if (value && !parseDate(value)) state.errors[field] = BAD_DATE
}

function initDocTypeMenu(kind: SourceKind): void {
  const init = runtime().initPrksDocTypeMenu
  if (kind === 'video') {
    if (typeof init === 'function') init('work-doc-type', { selectedValue: 'online', disabled: true })
    else setFieldValue('work-doc-type', 'online')
    return
  }
  const current = fieldValue('work-doc-type').trim()
  const next = current.toLowerCase() === 'online' || !current ? 'article' : current
  if (typeof init === 'function') init('work-doc-type', { selectedValue: next, disabled: false })
  else setFieldValue('work-doc-type', next)
}

function onKindChanged(kind: SourceKind): void {
  initDocTypeMenu(kind)
  if (kind === 'video' && !fieldValue('work-video-urldate').trim()) {
    setFieldValue('work-video-urldate', 'Auto (last edit)')
  }
  void nextTick(refreshCounts)
}

function readDraft(): NewFileDraft {
  return {
    kind: state.kind.value,
    pdf: state.kind.value === 'pdf' ? state.pdf.value : null,
    title: fieldValue('work-title'),
    status: fieldValue('work-status') || 'Not Started',
    docType: fieldValue('work-doc-type') || 'article',
    abstract: fieldValue('work-abstract'),
    year: fieldValue('work-year'),
    folderId: fieldValue('work-folder-id'),
    folderCommitted: folder.value?.isCommitted() ?? false,
    personSearch: fieldValue('upload-person-search'),
    videoUrl: fieldValue('work-video-url'),
    videoChannel: fieldValue('work-video-channel'),
    videoPublished: fieldValue('work-video-published-date'),
    playlistId: state.playlistId.value,
    pdfSourceUrl: fieldValue('work-pdf-source-url'),
    pdfPublished: fieldValue('work-date'),
    thumbPage: fieldValue('work-thumb-page'),
    privateNotes: fieldValue('work-private-notes'),
    publisher: fieldValue('work-publisher'),
    location: fieldValue('work-location'),
    edition: fieldValue('work-edition'),
    journal: fieldValue('work-journal'),
    volume: fieldValue('work-volume'),
    issue: fieldValue('work-issue'),
    pages: fieldValue('work-pages'),
    isbn: fieldValue('work-isbn'),
    doi: fieldValue('work-doi'),
    roles: state.roles.value.map((role) => ({ ...role })),
    tags: state.tags.value.map((tag) => ({ ...tag })),
  }
}

function readPdfBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '')
    reader.onerror = () => reject(reader.error || new Error('File read error'))
    reader.onabort = () => reject(new Error('File read aborted'))
    reader.readAsDataURL(file)
  })
}

async function onSave(): Promise<void> {
  if (state.busy.value) return
  const opening = state.currentOpening()
  const w = runtime()
  await saveNewFile({
    opening,
    stillOpening: () => state.isOpening(opening),
    inFlight: state.getInFlight,
    setInFlight: state.setInFlight,
    setBusy,
    pendingPerson: () => state.pendingPerson(opening),
    pendingQuickCreates: () => state.pendingQuickCreates(opening),
    readDraft,
    clearErrors: () => {
      state.clearErrors()
    },
    setFieldError: (field, message) => {
      state.errors[field] = message
    },
    focusField,
    setPeopleError: (message) => {
      state.peopleError.value = message
    },
    setStatus: (message) => {
      state.status.value = message
    },
    isValidYoutubeUrl: (url) => source.value?.isValidYoutubeUrl(url) ?? false,
    parseDate,
    readPdfBase64,
    videoMeta: (url) => source.value?.previewVideo(url) ?? Promise.resolve(null),
    today: () => new Date().toISOString().slice(0, 10),
    createVideoDurably: (fields, selectedTags) => {
      if (typeof w.prksCreateWorkDurably !== 'function') return Promise.reject(new Error('Could not create the file.'))
      return w.prksCreateWorkDurably(fields, { tags: selectedTags })
    },
    offlineRefused: (message) => w.prksOfflineGuardMutation?.(message) === true,
    waitForPeople: (ids) => (typeof w.prksWaitForPeopleOnServer === 'function' ? w.prksWaitForPeopleOnServer(ids) : Promise.resolve(true)),
    createPdfWork,
    attachTag: attachTagToWork,
    markCreated: (changes) => {
      if (changes.people) {
        // The create can link people in the same request, bypassing
        // POST /api/roles, so People and Person Groups owe a refresh.
        w.prksMarkPersonGroupsDomainChanged?.()
        w.prksMarkPeopleDomainChanged?.()
      }
      // A new Work enters the catalog and Recently added (not Recent), and
      // every new Work is filed into a Folder.
      w.prksMarkWorksBrowseChanged?.()
      w.prksMarkRecentlyAddedChanged?.()
      w.prksMarkFoldersDomainChanged?.()
    },
    failureText: (errText) =>
      typeof w.prksWorkCreateFailureText === 'function' ? w.prksWorkCreateFailureText(errText) : errText,
    closeAndOpen: (workId) => {
      w.closeModals?.()
      if (workId && typeof w.prksNavigate === 'function') w.prksNavigate(`#/works/${encodeURIComponent(workId)}`)
    },
    alert: (message, title) => {
      void w.prksAlertMessage?.(message, title)
    },
  })
}

const onKeydown = createSubmitKeyHandler(['work-title'], () => void onSave())

function onCancel(): void {
  runtime().requestModalClose?.('button')
}

function resetFields(): void {
  ;[
    'work-title',
    'work-year',
    'work-date',
    'work-publisher',
    'work-location',
    'work-edition',
    'work-journal',
    'work-volume',
    'work-issue',
    'work-pages',
    'work-isbn',
    'work-doi',
    'work-thumb-page',
    'work-private-notes',
    'work-video-published-date',
    'work-pdf-source-url',
    'work-abstract',
  ].forEach((id) => setFieldValue(id, ''))
  if (fieldValue('work-status') !== 'Not Started') {
    const notStarted = document.querySelector<HTMLElement>(
      '#work-modal .prks-segmented--status .prks-segmented__btn[data-value="Not Started"]',
    )
    if (notStarted) notStarted.click()
    else setFieldValue('work-status', 'Not Started')
  }
  DISCLOSURES.forEach((id) => {
    const details = document.getElementById(id)
    if (details instanceof HTMLDetailsElement) details.open = false
  })
  syncDisclosureInert()
}

/**
 * `openModal` for New File: a blank form, then the catalogues, then the
 * default Folder for the focused pane. Resolves once the dialog may thaw.
 */
async function prepare(stillOpening: () => boolean): Promise<void> {
  state.reset()
  resetFields()
  source.value?.reset()
  people.value?.reset()
  tags.value?.reset()
  playlist.value?.reset()
  folder.value?.reset()
  folder.value?.applyFolder('')
  setBusy(false)
  const w = runtime()
  const [persons, folders] = await Promise.all([loadList(w.fetchPersons), loadList(w.fetchFolders)])
  if (!stillOpening()) return
  state.persons.value = persons
  state.folders.value = folders
  // The shared person lookups (credit names, quick-create) read this list;
  // one setter keeps the classic catalogue and its window alias together.
  if (typeof w.prksSetPersonCatalogue === 'function') w.prksSetPersonCatalogue(persons)
  else w.allPersons = persons
  folder.value?.applyFolder(defaultFolderId(w, window.location.hash))
  syncDisclosureInert()
  state.clearErrors()
  setBusy(false)
  refreshCounts()
}

function focusInitial(): void {
  focusControl(source.value?.initialControl() ?? null)
}

/** Escape's inner layer: an open result list closes before the dialog. */
function closeOpenList(): boolean {
  return [folder.value, people.value, tags.value, playlist.value].some((part) => part?.close() === true)
}

onMounted(() => {
  syncDisclosureInert()
})

onBeforeUnmount(() => {
  if (countFrame) window.cancelAnimationFrame(countFrame)
})

defineExpose({ prepare, focusInitial, closeOpenList })
</script>

<template>
  <div
    ref="root"
    class="upload-grid form-pane"
    :data-prks-create-in-flight="state.inFlight.value ?? undefined"
    @keydown="onKeydown"
    @input="scheduleCounts"
    @change="scheduleCounts"
    @click="scheduleCounts"
  >
    <NewFileSource ref="source" :focus-control="focusControl" :on-kind-changed="onKindChanged" :on-autofilled="scheduleCounts" />

    <section class="prks-create-section" aria-labelledby="work-create-basic-heading">
      <h4 id="work-create-basic-heading" class="prks-create-section__title">Basic information</h4>

      <div class="prks-field">
        <label for="work-title">Title</label>
        <input id="work-title" type="text" placeholder="e.g. Capital Volume 1" autocomplete="off" aria-describedby="work-title-error">
        <p id="work-title-error" class="folder-field-error hidden" data-prks-field-error="work-title" role="alert"></p>
      </div>

      <NewFileFolder ref="folder" />

      <div class="form-grid-2 prks-create-grid">
        <div class="prks-field">
          <label for="work-doc-type-trigger">Document type</label>
          <div class="prks-doc-type-menu combobox-container">
            <input id="work-doc-type" type="hidden" value="article">
            <button id="work-doc-type-trigger" type="button" class="prks-doc-type-menu__trigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="work-doc-type-listbox" aria-label="Document type">
              <span class="prks-doc-type-menu__label">Article</span>
              <span class="prks-doc-type-menu__caret"><i data-lucide="chevron-down" class="prks-icon" aria-hidden="true"></i></span>
            </button>
            <div id="work-doc-type-listbox" class="prks-doc-type-menu__panel hidden" role="listbox"></div>
          </div>
        </div>
        <div class="prks-field">
          <label for="work-year">Year</label>
          <input id="work-year" type="text" placeholder="1950" autocomplete="off">
        </div>
      </div>

      <NewFilePeople ref="people" />
    </section>

    <details id="work-upload-biblio-details" class="work-upload-meta__details" @toggle="syncDisclosureInert">
      <summary class="work-upload-meta__summary">
        <span class="work-upload-meta__summary-chevron" aria-hidden="true"></span>
        <span class="work-upload-meta__summary-text">
          <span class="work-upload-meta__summary-title-row">
            <span class="work-upload-meta__summary-title">Publication details</span>
            <span class="work-upload-meta__summary-optional">Optional</span>
            <span class="work-upload-meta__summary-count" data-prks-count-for="work-upload-biblio-details" :hidden="!state.counts.biblio">{{ state.counts.biblio ? `${state.counts.biblio} set` : '' }}</span>
            <button type="button" class="prks-hint-btn work-upload-meta__hint-btn" data-prks-hint-type="upload-biblio-details" aria-label="What is included in publication details" aria-expanded="false" aria-controls="prks-hint-popover">?</button>
          </span>
        </span>
      </summary>
      <div class="work-upload-meta__details-body form-pane form-pane--tight" inert>
        <div id="work-upload-pdf-only-meta" class="prks-create-details-grid" :class="{ hidden: state.kind.value !== 'pdf' }">
          <div class="form-grid-2 prks-create-grid">
            <div id="work-upload-published-date-col" class="prks-field">
              <label for="work-date">Published date</label>
              <input
                id="work-date"
                type="text"
                placeholder="dd/mm/yyyy"
                inputmode="numeric"
                autocomplete="off"
                data-prks-count
                aria-describedby="work-date-error"
                :aria-invalid="state.errors.date ? 'true' : undefined"
                @input="onDateInput('date')"
                @blur="onDateBlur('date')"
              >
              <p id="work-date-error" class="folder-field-error" :class="{ hidden: !state.errors.date }" data-prks-field-error="work-date" role="alert">{{ state.errors.date }}</p>
            </div>
            <div class="prks-field">
              <label for="work-edition">Edition</label>
              <input id="work-edition" type="text" placeholder="e.g. 2 or revised" autocomplete="off" data-prks-count>
            </div>
          </div>
          <div id="work-pdf-source-url-row" class="prks-field">
            <label for="work-pdf-source-url">Original URL</label>
            <input id="work-pdf-source-url" type="url" placeholder="https://… (article or source page)" autocomplete="off" data-prks-count aria-describedby="work-pdf-source-url-hint">
            <p id="work-pdf-source-url-hint" class="meta-row meta-row--hint meta-row--compact">If this PDF was saved from the web, keep the link for reference and BibTeX.</p>
          </div>
          <div class="form-grid-2 prks-create-grid">
            <div class="prks-field">
              <label for="work-publisher">Publisher</label>
              <input id="work-publisher" type="text" autocomplete="off" data-prks-count>
            </div>
            <div class="prks-field">
              <label for="work-location">Location (place of publication)</label>
              <input id="work-location" type="text" autocomplete="off" placeholder="e.g. Cambridge, UK or Paris; Berlin" data-prks-count>
            </div>
          </div>
          <div class="prks-field">
            <label for="work-journal">Journal</label>
            <input id="work-journal" type="text" autocomplete="off" data-prks-count>
          </div>
          <div class="form-grid-2 prks-create-grid">
            <div class="prks-field"><label for="work-volume">Volume</label><input id="work-volume" type="text" autocomplete="off" data-prks-count></div>
            <div class="prks-field"><label for="work-issue">Issue</label><input id="work-issue" type="text" autocomplete="off" data-prks-count></div>
          </div>
          <div class="form-grid-2 prks-create-grid">
            <div class="prks-field"><label for="work-pages">Pages</label><input id="work-pages" type="text" autocomplete="off" data-prks-count></div>
            <div class="prks-field"><label for="work-isbn">ISBN</label><input id="work-isbn" type="text" autocomplete="off" data-prks-count></div>
          </div>
          <div class="form-grid-2 prks-create-grid">
            <div class="prks-field"><label for="work-doi">DOI</label><input id="work-doi" type="text" autocomplete="off" data-prks-count></div>
            <div class="prks-field"><label for="work-thumb-page">Thumbnail page (PDF)</label><input id="work-thumb-page" type="number" min="1" step="1" inputmode="numeric" placeholder="1" autocomplete="off" data-prks-count></div>
          </div>
        </div>
        <div id="work-upload-video-only-meta" class="prks-create-details-grid" :class="{ hidden: state.kind.value !== 'video' }">
          <div class="form-grid-2 prks-create-grid">
            <div class="prks-field">
              <label for="work-video-channel">Channel / author</label>
              <input id="work-video-channel" type="text" placeholder="e.g. YaleCourses" autocomplete="off" data-prks-count>
            </div>
            <div class="prks-field">
              <label for="work-video-published-date">Date published</label>
              <input
                id="work-video-published-date"
                type="text"
                placeholder="dd/mm/yyyy"
                inputmode="numeric"
                autocomplete="off"
                data-prks-count
                aria-describedby="work-video-published-date-error"
                :aria-invalid="state.errors.videoDate ? 'true' : undefined"
                @input="onDateInput('videoDate')"
                @blur="onDateBlur('videoDate')"
              >
              <p id="work-video-published-date-error" class="folder-field-error" :class="{ hidden: !state.errors.videoDate }" data-prks-field-error="work-video-published-date" role="alert">{{ state.errors.videoDate }}</p>
            </div>
          </div>
          <div class="prks-field">
            <label for="work-video-urldate">Accessed date (urldate)</label>
            <input id="work-video-urldate" type="text" placeholder="Auto (last edit)" disabled aria-describedby="work-video-urldate-hint">
            <p id="work-video-urldate-hint" class="meta-row meta-row--hint meta-row--compact">BibLaTeX <code>@online</code> expects <code>url</code> + <code>urldate</code>.</p>
          </div>
        </div>
      </div>
    </details>

    <details id="work-upload-more-details" class="work-upload-meta__details" @toggle="syncDisclosureInert">
      <summary class="work-upload-meta__summary">
        <span class="work-upload-meta__summary-chevron" aria-hidden="true"></span>
        <span class="work-upload-meta__summary-text">
          <span class="work-upload-meta__summary-title-row">
            <span id="work-upload-more-title" class="work-upload-meta__summary-title">{{ moreTitle }}</span>
            <span class="work-upload-meta__summary-optional">Optional</span>
            <span class="work-upload-meta__summary-count" data-prks-count-for="work-upload-more-details" :hidden="!state.counts.more">{{ state.counts.more ? `${state.counts.more} set` : '' }}</span>
          </span>
        </span>
      </summary>
      <div class="work-upload-meta__details-body form-pane form-pane--tight" inert>
        <NewFilePlaylist ref="playlist" />
        <NewFileTags ref="tags" />

        <div class="prks-work-upload-status-field">
          <label for="work-status">Status</label>
          <div class="prks-segmented-wrap prks-segmented-wrap--status-row">
            <input id="work-status" type="hidden" value="Not Started" data-prks-count data-prks-count-default="Not Started">
            <div class="prks-segmented prks-segmented--status prks-segmented--single-row" role="radiogroup" aria-label="File status">
              <button type="button" class="prks-segmented__btn prks-segmented__btn--active" data-value="Not Started" aria-checked="true" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="circle" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Not Started</span></button>
              <button type="button" class="prks-segmented__btn" data-value="Planned" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="clipboard-list" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Planned</span></button>
              <button type="button" class="prks-segmented__btn" data-value="In Progress" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="play" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">In Progress</span></button>
              <button type="button" class="prks-segmented__btn" data-value="Completed" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="check" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Completed</span></button>
              <button type="button" class="prks-segmented__btn" data-value="Paused" aria-checked="false" role="radio"><span class="prks-segmented__btn-icon"><i data-lucide="pause" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span><span class="prks-segmented__btn-label">Paused</span></button>
            </div>
          </div>
        </div>

        <div class="prks-field">
          <label for="work-private-notes">Private notes</label>
          <textarea id="work-private-notes" class="textarea-sm" placeholder="Reminders for yourself; not shown in search index." data-prks-count></textarea>
        </div>

        <div class="prks-field">
          <label for="work-abstract">Abstract / Preface</label>
          <textarea id="work-abstract" class="textarea-lg textarea-flex" placeholder="Briefly describe why this document is relevant, or use paper abstract..." data-prks-count></textarea>
        </div>
      </div>
    </details>
  </div>

  <Teleport to="#work-modal-footer-vue">
    <p id="upload-status-msg" class="upload-status" :class="{ hidden: !state.status.value }" role="status">{{ state.status.value }}</p>
    <PrksButton id="work-modal-cancel" variant="secondary" @click="onCancel">Cancel</PrksButton>
    <!--
      Native button on purpose, like New Folder Save. PrksButton ignores a
      click while its last rendered busy prop is still set, and a same-turn
      reopen-and-save must reach the opening-scoped gate in saveNewFile.
    -->
    <button
      id="save-work-btn"
      type="button"
      class="prks-btn prks-btn--primary"
      title="Create File (Ctrl+Enter)"
      aria-keyshortcuts="Control+Enter Meta+Enter"
      :disabled="state.busy.value"
      :aria-busy="state.busy.value ? 'true' : 'false'"
      @click="onSave"
      @keydown="onKeydown"
    >{{ state.busy.value ? 'Creating…' : 'Create File' }}</button>
  </Teleport>
</template>
