<script setup lang="ts">
/**
 * The Save View / Edit Saved View dialog body (#1036).
 *
 * ModalHost renders it as the body of the `#saved-view-modal` ModalDialog,
 * whose heading reads the exposed `title`. The openers (`prksOpenSavedViewModal` and the Search
 * and current-view variants) are installed while this form is mounted.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import type { SavedViewMode } from '../../api/saved-views'
import { focusField } from '../create-forms/keyboard'
import {
  openingFromSearchHash,
  openingFromView,
  SAVED_VIEW_NAME_REQUIRED,
  SAVED_VIEW_OFFLINE_MESSAGE,
  saveSavedView,
  stateFromOpening,
  type SavedViewDraft,
  type SavedViewOpening,
} from './editor'
import { savedViewRecords } from './records'

const MODAL_ID = 'saved-view-modal'
const MODES: ReadonlyArray<{ value: SavedViewMode; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'advanced', label: 'Advanced' },
  { value: 'tag', label: 'Tag' },
]

const viewId = ref('')
const draft = reactive<SavedViewDraft>(stateFromOpening(null).draft)
const nameError = ref('')
const saveError = ref('')
const saving = ref(false)
/** Each opening's number. A Save that outlives its opening changes nothing. */
let opening = 0

const editing = computed(() => !!viewId.value)
const title = computed(() => (editing.value ? 'Edit Saved View' : 'Save View'))
const helper = computed(() =>
  editing.value ? 'Rename this view or change the saved search.' : 'Name this search to open it later from Saved Views.',
)
const saveLabel = computed(() => (editing.value ? 'Save changes' : 'Save View'))
const queryLabel = computed(() => (draft.mode === 'all' ? 'All fields' : 'Keywords'))

interface MainTabContext {
  getEntity?: (type: string) => unknown
}

interface SavedViewWindow {
  prksGetMainTabContext?: () => MainTabContext | null
}

function modalShown(): boolean {
  const el = document.getElementById(MODAL_ID)
  return el instanceof HTMLElement && !el.classList.contains('hidden')
}

function openWith(next?: SavedViewOpening): void {
  if (window.prksOfflineGuardMutation?.(SAVED_VIEW_OFFLINE_MESSAGE) === true) return
  const state = stateFromOpening(next)
  opening += 1
  viewId.value = state.viewId
  Object.assign(draft, state.draft)
  nameError.value = ''
  saveError.value = ''
  saving.value = false
  window.openModal?.(MODAL_ID, next?.returnFocus ? { returnFocus: next.returnFocus } : undefined)
}

/** The Search surface passes its owner's hash; the command palette uses the URL. */
function openFromSearch(searchHash?: string): void {
  const hash = typeof searchHash === 'string' && searchHash ? searchHash : window.location.hash
  const result = openingFromSearchHash(hash)
  if (!result.ok) {
    void window.prksAlertDialog?.({ title: 'Cannot save view', message: result.message })
    return
  }
  openWith(result.opening)
}

/** Saved View detail is Main-only, so its record is the Main TabContext's entity. */
function openForCurrentView(): void {
  const ctx = (window as unknown as SavedViewWindow).prksGetMainTabContext?.() ?? null
  const view = ctx && typeof ctx.getEntity === 'function' ? ctx.getEntity('savedView') : null
  const next = openingFromView(view as Parameters<typeof openingFromView>[0])
  if (next) openWith(next)
}

function closeDialog(): void {
  if (typeof window.requestModalClose === 'function') window.requestModalClose('save')
  else (window as unknown as { closeModals?: () => void }).closeModals?.()
}

async function onSave(): Promise<void> {
  if (saving.value) return
  const mine = opening
  const still = () => mine === opening && modalShown()
  saveError.value = ''
  if (!draft.name.trim()) {
    nameError.value = SAVED_VIEW_NAME_REQUIRED
    focusField('saved-view-name')
    return
  }
  nameError.value = ''
  saving.value = true
  const records = window.prksSavedViewRecords ?? savedViewRecords()
  const outcome = await saveSavedView(
    { viewId: viewId.value, draft: { ...draft } },
    {
      create: (input) => records.create(input),
      update: (id, input) => records.update(id, input),
      actionMessage: (err, fallback) => records.actionMessage(err, fallback),
    },
  )
  if (mine !== opening) return
  saving.value = false
  if (!still()) return
  if (outcome.status === 'failed') {
    saveError.value = outcome.message
    return
  }
  if (outcome.status === 'name-required') {
    nameError.value = SAVED_VIEW_NAME_REQUIRED
    return
  }
  closeDialog()
  if (outcome.status === 'created' && outcome.id) {
    window.prksNavigate?.(`#/views/${encodeURIComponent(outcome.id)}`)
  } else {
    window.prksNavigate?.(window.location.hash, { replace: true })
  }
}

function onNameKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.isComposing || event.defaultPrevented) return
  event.preventDefault()
  void onSave()
}

function focusInitial(): void {
  focusField('saved-view-name')
}

onMounted(() => {
  window.prksOpenSavedViewModal = openWith
  window.prksOpenSavedViewModalFromCurrentSearch = openFromSearch
  window.prksOpenSavedViewModalForCurrentView = openForCurrentView
})

onBeforeUnmount(() => {
  if (window.prksOpenSavedViewModal === openWith) delete window.prksOpenSavedViewModal
  if (window.prksOpenSavedViewModalFromCurrentSearch === openFromSearch) {
    delete window.prksOpenSavedViewModalFromCurrentSearch
  }
  if (window.prksOpenSavedViewModalForCurrentView === openForCurrentView) {
    delete window.prksOpenSavedViewModalForCurrentView
  }
})

defineExpose({ open: openWith, focusInitial, title })
</script>

<template>
  <div class="form-pane">
    <p id="saved-view-modal-helper" class="modal-helper">{{ helper }}</p>
    <p
      id="saved-view-modal-error"
      class="folder-field-error"
      :class="{ hidden: !saveError }"
      role="alert"
    >{{ saveError }}</p>
    <PrksField
      v-slot="{ labelledBy, describedBy }"
      label="Name"
      for-id="saved-view-name"
      :error="nameError || null"
    >
      <input
        id="saved-view-name"
        v-model="draft.name"
        type="text"
        placeholder="e.g. Adorno — culture industry"
        maxlength="80"
        autocomplete="off"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        :aria-invalid="nameError ? 'true' : undefined"
        @keydown="onNameKeydown"
      >
    </PrksField>
    <fieldset class="saved-view-modal__mode">
      <legend>Search mode</legend>
      <label v-for="option in MODES" :key="option.value" class="saved-view-modal__mode-opt">
        <input v-model="draft.mode" type="radio" name="saved-view-mode" :value="option.value">
        {{ option.label }}
      </label>
    </fieldset>
    <div v-show="draft.mode !== 'tag'" id="saved-view-field-q-wrap">
      <PrksField v-slot="{ labelledBy, describedBy }" :label="queryLabel" for-id="saved-view-q">
        <input
          id="saved-view-q"
          v-model="draft.q"
          type="text"
          maxlength="500"
          autocomplete="off"
          :aria-labelledby="labelledBy"
          :aria-describedby="describedBy"
        >
      </PrksField>
    </div>
    <div v-show="draft.mode === 'tag'" id="saved-view-field-tag-wrap">
      <PrksField v-slot="{ labelledBy, describedBy }" label="Tag" for-id="saved-view-tag">
        <input
          id="saved-view-tag"
          v-model="draft.tag"
          type="text"
          maxlength="200"
          autocomplete="off"
          :aria-labelledby="labelledBy"
          :aria-describedby="describedBy"
        >
      </PrksField>
    </div>
    <div v-show="draft.mode !== 'all'" id="saved-view-field-author-wrap">
      <PrksField v-slot="{ labelledBy, describedBy }" label="Author" for-id="saved-view-author">
        <input
          id="saved-view-author"
          v-model="draft.author"
          type="text"
          maxlength="200"
          autocomplete="off"
          :aria-labelledby="labelledBy"
          :aria-describedby="describedBy"
        >
      </PrksField>
    </div>
    <div v-show="draft.mode !== 'all'" id="saved-view-field-publisher-wrap">
      <PrksField v-slot="{ labelledBy, describedBy }" label="Publisher" for-id="saved-view-publisher">
        <input
          id="saved-view-publisher"
          v-model="draft.publisher"
          type="text"
          maxlength="200"
          autocomplete="off"
          :aria-labelledby="labelledBy"
          :aria-describedby="describedBy"
        >
      </PrksField>
    </div>
    <PrksButton
      id="save-saved-view-btn"
      variant="primary"
      :busy="saving"
      busy-label="Saving…"
      @click="onSave"
    >{{ saveLabel }}</PrksButton>
  </div>
</template>
