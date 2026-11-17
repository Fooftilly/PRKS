<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import PrksField from '../../components/PrksField.vue'
import { useComboboxPanel } from './combobox-panel'
import { fieldValue, setFieldValue } from './dom'
import { finishFolderCreate, type FolderDraft } from './folder-effects'
import { FOLDER_TITLE_CONFLICT, folderTitleConflicts, type FolderTitleRow } from './folder-rules'
import { createFolderSaveGate } from './folder-save'
import { bindComboboxKeys, createSubmitKeyHandler, focusField, markComboboxRendered } from './keyboard'

interface FolderWindow {
  fetchFolders?: () => Promise<FolderTitleRow[] | null>
  prksFolderRowLabel?: (folder: FolderTitleRow, all: FolderTitleRow[]) => string
  createFolder?: (title: string, description: string, options: { parent_id: string | null }) => Promise<string>
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
  prksOfflineWasGuardRefusal?: (error: unknown) => boolean
  prksGetFocusedTabContext?: () => unknown
}

const folderError = ref<string | null>(null)
const parentError = ref<string | null>(null)
const folderBusy = ref(false)
const folderBlocked = ref(false)
const folderLabel = ref('Save Folder')
const parentRows = ref<Array<{ id: string; label: string }>>([])
const { hidden: parentHidden, open: parentOpen, show: showParent, hide: hideParent, hideNow: hideParentNow } =
  useComboboxPanel()

let folders: FolderTitleRow[] = []
const gate = createFolderSaveGate()

/** Typed parent text that was never picked is a query, not a parent (#467). */
const PARENT_NOT_PICKED = 'Pick a parent folder from the list, or clear this field.'

function runtime(): FolderWindow {
  return window as unknown as FolderWindow
}

function readDraft(): FolderDraft {
  return {
    title: fieldValue('folder-title'),
    description: fieldValue('folder-description'),
    parentId: fieldValue('folder-parent-id'),
  }
}

function rowLabel(folder: FolderTitleRow): string {
  const label = runtime().prksFolderRowLabel
  if (typeof label === 'function') return label(folder, folders)
  return String(folder.title || 'Folder')
}

function applyChrome(): void {
  const draft = readDraft()
  const duplicate = folderTitleConflicts(draft.title, folders, draft.parentId)
  folderError.value = duplicate ? FOLDER_TITLE_CONFLICT : null
  const block = duplicate || folderBusy.value
  folderBlocked.value = block
  folderLabel.value = folderBusy.value ? 'Creating…' : 'Save Folder'
  const button = document.getElementById('save-folder-btn')
  if (button instanceof HTMLButtonElement) {
    button.disabled = block
    if (folderBusy.value) button.setAttribute('aria-busy', 'true')
    else button.removeAttribute('aria-busy')
    button.textContent = folderLabel.value
  }
  const title = document.getElementById('folder-title')
  if (title) {
    if (duplicate) title.setAttribute('aria-invalid', 'true')
    else title.removeAttribute('aria-invalid')
  }
}

function renderParents(): void {
  const query = fieldValue('folder-parent-search').trim().toLowerCase()
  const filtered = folders.filter((folder) => {
    const label = rowLabel(folder)
    return !query || label.toLowerCase().includes(query) || String(folder.title || '').toLowerCase().includes(query)
  })
  parentRows.value = filtered.slice(0, 80).map((folder) => ({
    id: String(folder.id || ''),
    label: rowLabel(folder),
  }))
  showParent()
  markComboboxRendered('folder-parent-search', nextTick())
}

function parentNotPicked(): boolean {
  return !!fieldValue('folder-parent-search').trim() && !fieldValue('folder-parent-id').trim()
}

function bindParentKeys(): void {
  bindComboboxKeys('folder-parent-search', 'folder-parent-results', {
    onOpen: renderParents,
    onEscape: hideParentNow,
  })
}

onMounted(bindParentKeys)

function onTitleInput(): void {
  applyChrome()
}

function onParentFocus(): void {
  bindParentKeys()
  renderParents()
}

function onParentInput(): void {
  parentError.value = null
  setFieldValue('folder-parent-id', '')
  renderParents()
  applyChrome()
}

function onParentBlur(): void {
  window.setTimeout(() => hideParent(), 200)
  parentError.value = parentNotPicked() ? PARENT_NOT_PICKED : null
}

function pickParent(row: { id: string; label: string }): void {
  setFieldValue('folder-parent-id', row.id)
  setFieldValue('folder-parent-search', row.label)
  parentError.value = null
  hideParentNow()
  applyChrome()
}

async function refreshValidation(): Promise<void> {
  try {
    const rows = await runtime().fetchFolders?.()
    folders = Array.isArray(rows) ? rows : []
  } catch {
    folders = []
  }
  applyChrome()
}

function releaseSave(): void {
  folderBusy.value = false
  applyChrome()
}

function onSave(): void {
  if (parentNotPicked()) {
    parentError.value = PARENT_NOT_PICKED
    focusField('folder-parent-search')
    return
  }
  const w = runtime()
  void gate.save({
    modal: () => {
      const el = document.getElementById('folder-modal')
      return el instanceof HTMLElement ? el : null
    },
    buttonDisabled: () => {
      const button = document.getElementById('save-folder-btn')
      return button instanceof HTMLButtonElement ? button.disabled : false
    },
    setBusy: (busy) => {
      folderBusy.value = busy
      applyChrome()
    },
    readDraft,
    createFolder: (title, description, parentId) => {
      if (typeof w.createFolder !== 'function') return Promise.reject(new Error('Could not create folder'))
      return w.createFolder(title, description, { parent_id: parentId })
    },
    alert: async (message, title) => {
      await w.prksAlertMessage?.(message, title)
    },
    guardRefusal: (error) => w.prksOfflineWasGuardRefusal?.(error) === true,
    revalidate: () => applyChrome(),
    owner: () => w.prksGetFocusedTabContext?.() ?? null,
    afterCreate: finishFolderCreate,
  })
}

const onKeydown = createSubmitKeyHandler(['folder-title'], onSave)

function focusInitial(): void {
  focusField('folder-title')
}

/** A closed dialog drops its draft, so the next New Folder starts blank (#373). */
function resetFields(): void {
  setFieldValue('folder-title', '')
  setFieldValue('folder-description', '')
  setFieldValue('folder-parent-search', '')
  setFieldValue('folder-parent-id', '')
  parentError.value = null
  hideParentNow()
  applyChrome()
}

defineExpose({ refreshValidation, releaseSave, focusInitial, resetFields })
</script>

<template>
  <div class="form-pane" @keydown="onKeydown">
    <p class="modal-helper">Folders can nest. Pick a parent to create a subfolder, or leave blank for top-level.</p>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Title" for-id="folder-title" :error="folderError">
      <input
        id="folder-title"
        type="text"
        placeholder="Folder title"
        autocomplete="off"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        :aria-invalid="folderError ? 'true' : undefined"
        @input="onTitleInput"
      >
    </PrksField>
    <PrksField
      v-slot="{ labelledBy, describedBy }"
      label="Parent folder (optional)"
      for-id="folder-parent-search"
      help="Search and choose a parent folder to create this one as a subfolder."
      :error="parentError"
    >
      <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
        <div class="tag-add-shell__field">
          <span class="tag-add-shell__icon"><i data-lucide="search" class="prks-icon" aria-hidden="true"></i></span>
          <input
            id="folder-parent-search"
            class="tag-add-shell__input"
            type="text"
            placeholder="Search parent folder…"
            autocomplete="off"
            aria-label="Search parent folder"
            :aria-labelledby="labelledBy"
            :aria-describedby="describedBy"
            :aria-invalid="parentError ? 'true' : undefined"
            @focus="onParentFocus"
            @input="onParentInput"
            @blur="onParentBlur"
          >
        </div>
        <input id="folder-parent-id" type="hidden">
        <div
          id="folder-parent-results"
          class="combobox-results combobox-results--tag-panel"
          :class="{ hidden: parentHidden, 'is-open': parentOpen }"
        >
          <div v-if="parentRows.length === 0" class="result-item no-results">No folders found</div>
          <div
            v-for="row in parentRows"
            :key="row.id"
            class="result-item"
            @mousedown.prevent="pickParent(row)"
          >{{ row.label }}</div>
        </div>
      </div>
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Description" for-id="folder-description">
      <textarea
        id="folder-description"
        placeholder="Optional description…"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      ></textarea>
    </PrksField>
    <!--
      Native button on purpose. PrksButton ignores a click while its last
      rendered busy or disabled prop is still set, so a same-turn
      reopen-and-save (the folder opening guard) would be swallowed.
      Person, Group, and Playlist use PrksButton; they do not have that guard.
    -->
    <button
      id="save-folder-btn"
      type="button"
      class="prks-btn prks-btn--primary"
      :disabled="folderBlocked"
      :aria-busy="folderBusy ? 'true' : undefined"
      @click="onSave"
    >{{ folderLabel }}</button>
  </div>
</template>
