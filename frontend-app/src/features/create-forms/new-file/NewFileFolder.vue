<script setup lang="ts">
import { ref } from 'vue'
import { fieldValue, setFieldValue } from '../dom'
import { useInlineCombobox } from './combobox'
import { canonicalUncategorizedFolder, folderChoiceFor, FOLDER_NOT_PICKED } from './folder'
import { alertMessage, loadList, runtime, type FolderRow } from './runtime'
import { useNewFileState } from './state'

const state = useNewFileState()

interface FolderOption {
  key: string
  id: string
  label: string
  isDefault: boolean
}

const rows = ref<FolderOption[]>([])

function rowLabel(folder: FolderRow, all: readonly FolderRow[]): string {
  const label = runtime().prksFolderRowLabel
  if (typeof label === 'function') return label(folder, all as FolderRow[])
  return String(folder.title || folder.id)
}

function render(): void {
  const raw = fieldValue('work-folder-search')
  const query = raw.toLowerCase()
  const all = state.folders.value
  const next: FolderOption[] = []
  // The synthetic default row only while no real Uncategorized exists, so
  // there are never two rows that read the same.
  if (!canonicalUncategorizedFolder(all)) {
    const q = raw.trim().toLowerCase()
    if (!q || 'uncategorized'.includes(q)) {
      next.push({ key: '__default', id: '', label: 'Uncategorized', isDefault: true })
    }
  }
  all.forEach((folder) => {
    const label = rowLabel(folder, all)
    const title = String(folder.title || '')
    if (!query || label.toLowerCase().includes(query) || title.toLowerCase().includes(query)) {
      next.push({ key: String(folder.id), id: String(folder.id), label, isDefault: false })
    }
  })
  rows.value = next
  combobox.shown()
}

const combobox = useInlineCombobox('work-folder-search', 'folder-results', { onOpen: render })

function choose(id: string, label: string, isDefault: boolean): void {
  setFieldValue('work-folder-id', id)
  setFieldValue('work-folder-search', label)
  state.folderDefault.value = isDefault
  state.errors.folder = ''
}

function pick(row: FolderOption): void {
  choose(row.id, row.label, row.isDefault)
  combobox.hide()
}

function onFocus(event: FocusEvent): void {
  if (state.folderDefault.value && event.target instanceof HTMLInputElement) {
    try {
      event.target.select()
    } catch {
      /* not selectable */
    }
  }
  render()
}

function onInput(): void {
  // Free text is a query, never a choice.
  setFieldValue('work-folder-id', '')
  state.folderDefault.value = false
  state.errors.folder = ''
  render()
}

/** A choice is a picked folder or the explicit default; typed text is not. */
function isCommitted(): boolean {
  return !!fieldValue('work-folder-id').trim() || state.folderDefault.value
}

/** Field-local check, the same rule and copy Create applies. */
function validate(): void {
  if (isCommitted() || !fieldValue('work-folder-search').trim()) return
  state.errors.folder = FOLDER_NOT_PICKED
}

const actionRow = ref<HTMLElement | null>(null)

function onBlur(): void {
  combobox.onBlur()
  // Let a row pick or the quick-create button commit first.
  window.setTimeout(() => {
    const row = actionRow.value
    if (row && row.contains(document.activeElement)) return
    if (state.isOpening(state.currentOpening())) validate()
  }, 250)
}

/** Shows `folderId`, or the default destination when it is empty. */
function applyFolder(folderId: string): void {
  const choice = folderChoiceFor(folderId, state.folders.value, (folder) => rowLabel(folder, state.folders.value))
  choose(choice.id, choice.label, choice.isDefault)
}

/** True while this opening's folder create runs: a repeat press is ignored. */
const creating = ref(false)

async function quickCreate(): Promise<void> {
  const title = fieldValue('work-folder-search').trim()
  if (!title) {
    await alertMessage('Please enter a folder title first', 'Validation')
    return
  }
  if (creating.value) return
  creating.value = true
  const w = runtime()
  // A create that outlives its opening must never touch the next form.
  const opening = state.currentOpening()
  const stillApplies = () => state.isOpening(opening)
  const done = (async () => {
    try {
      // The canonical create boundary owns the offline guard and the Folder
      // coherence hook, so this surface cannot reopen either gap.
      let newId: string
      try {
        if (typeof w.createFolder !== 'function') throw new Error('Could not create folder')
        newId = await w.createFolder(title, 'Quick created via upload')
      } catch (error) {
        if (stillApplies() && w.prksOfflineWasGuardRefusal?.(error) !== true) {
          await alertMessage((error instanceof Error && error.message) || 'Could not create folder', 'Could not save')
        }
        return
      }
      const folders = await loadList(w.fetchFolders)
      if (!stillApplies()) return
      state.folders.value = folders
      choose(newId, title, false)
      combobox.hide()
    } finally {
      if (stillApplies()) creating.value = false
    }
  })()
  // Create File waits for this. A failed create leaves the Folder
  // uncommitted, which validation then reports on the field, so the task
  // itself never refuses the save.
  void state.trackQuickCreate(done.then(() => true, () => true))
  await done
}

/** Opening reset: a create from the last opening no longer holds the button. */
function reset(): void {
  creating.value = false
}

defineExpose({ applyFolder, isCommitted, reset, close: combobox.close })
</script>

<template>
  <div class="prks-field">
    <label for="work-folder-search">Folder <span class="prks-field__required" aria-hidden="true">Required</span></label>
    <div ref="actionRow" class="prks-combobox-with-action">
      <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
        <div class="tag-add-shell__field">
          <span class="tag-add-shell__icon"><i data-lucide="search" class="prks-icon" aria-hidden="true"></i></span>
          <input
            id="work-folder-search"
            type="text"
            class="tag-add-shell__input"
            placeholder="Uncategorized"
            autocomplete="off"
            aria-required="true"
            aria-describedby="work-folder-error"
            :aria-invalid="state.errors.folder ? 'true' : undefined"
            :data-prks-folder-default="state.folderDefault.value ? '1' : undefined"
            @focus="onFocus"
            @input="onInput"
            @blur="onBlur"
          >
        </div>
        <input id="work-folder-id" type="hidden">
        <div
          id="folder-results"
          class="combobox-results combobox-results--tag-panel"
          :class="{ hidden: combobox.hidden.value, 'is-open': combobox.open.value }"
        >
          <div v-if="rows.length === 0" class="result-item no-results">No results found</div>
          <div
            v-for="row in rows"
            :key="row.key"
            class="result-item"
            @mousedown.prevent="pick(row)"
          >{{ row.label }}</div>
        </div>
      </div>
      <button
        type="button"
        class="prks-btn prks-btn--secondary prks-btn--sm"
        title="Create New Folder"
        aria-label="Create New Folder"
        :disabled="creating"
        :aria-busy="creating ? 'true' : undefined"
        @click="quickCreate"
      ><span class="ribbon-btn__icon"><i data-lucide="plus" class="prks-icon prks-icon--sm" aria-hidden="true"></i></span></button>
    </div>
    <p
      id="work-folder-error"
      class="folder-field-error"
      :class="{ hidden: !state.errors.folder }"
      data-prks-field-error="work-folder"
      role="alert"
    >{{ state.errors.folder }}</p>
  </div>
</template>
