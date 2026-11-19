<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import { useDialogLifecycle } from '../../modal/dialogs'
import { useComboboxPanel } from './combobox-panel'
import { fieldValue, setFieldValue } from './dom'
import { createGroupSaveGate } from './group-save'
import { bindComboboxKeys, createSubmitKeyHandler, focusField, markComboboxRendered } from './keyboard'
import {
  createGroupDurably,
  finishGroupCreate,
  groupCreateFailureMessage,
  resolveGroupParent,
} from './submit'

interface GroupRow {
  id?: string
  name?: string
  parent_id?: string | null
}

interface GroupWindow {
  prksEnsureAllGroupsCache?: () => Promise<GroupRow[] | null>
  prksGroupRowLabel?: (group: GroupRow, all: GroupRow[]) => string
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
}

interface ParentRow {
  id: string
  label: string
  /** The explicit `Create group "…"` row: its label is the new parent's name. */
  create?: boolean
}

/** Typed parent text that was never picked is a query, not a parent (#467). */
const PARENT_NOT_PICKED = 'Pick a parent group from the list, or clear this field.'

const groupBusy = ref(false)
const parentRows = ref<ParentRow[]>([])
const parentError = ref<string | null>(null)
/** Name chosen through the Create row; empty when an existing group or nothing is picked. */
let parentCreateName = ''
const { hidden: parentHidden, open: parentOpen, show: showParent, hide: hideParent, hideNow: hideParentNow } =
  useComboboxPanel()
const gate = createGroupSaveGate()

let groups: GroupRow[] = []

function runtime(): GroupWindow {
  return window as unknown as GroupWindow
}

function rowLabel(group: GroupRow): string {
  const label = runtime().prksGroupRowLabel
  if (typeof label === 'function') return label(group, groups)
  return String(group.name || '')
}

function renderParents(): void {
  const query = fieldValue('group-parent-search').trim().toLowerCase()
  const filtered = groups.filter((group) => {
    const label = rowLabel(group).toLowerCase()
    return !query || label.includes(query) || String(group.name || '').toLowerCase().includes(query)
  })
  const typed = fieldValue('group-parent-search').trim()
  const rows: ParentRow[] = filtered.slice(0, 80).map((group) => ({
    id: String(group.id || ''),
    label: rowLabel(group),
  }))
  const exact = groups.some((group) => String(group.name || '').trim().toLowerCase() === query)
  if (typed && !exact) rows.unshift({ id: '', label: typed, create: true })
  parentRows.value = rows
  showParent()
  markComboboxRendered('group-parent-search', nextTick())
}

function parentNotPicked(): boolean {
  const typed = fieldValue('group-parent-search').trim()
  if (!typed || fieldValue('group-parent-id').trim()) return false
  return parentCreateName !== typed
}

function bindParentKeys(): void {
  bindComboboxKeys('group-parent-search', 'group-parent-results', {
    onOpen: onParentFocus,
    onEscape: hideParentNow,
    enterCreates: true,
  })
}

onMounted(bindParentKeys)

async function prepare(): Promise<void> {
  setFieldValue('group-parent-search', '')
  setFieldValue('group-parent-id', '')
  parentCreateName = ''
  parentError.value = null
  hideParentNow()
  // New opening: allow a fresh Create. An older in-flight save keeps its own
  // opening token and must not clear this busy flag when it settles.
  groupBusy.value = false
  try {
    const rows = await runtime().prksEnsureAllGroupsCache?.()
    groups = Array.isArray(rows) ? rows : []
  } catch {
    groups = []
  }
}

function onParentFocus(): void {
  cancelBlurCollapse()
  bindParentKeys()
  const load = runtime().prksEnsureAllGroupsCache?.()
  if (!load) {
    renderParents()
    return
  }
  // A failed catalogue read still shows the list, from the groups already known.
  void load.then(
    (rows) => {
      if (Array.isArray(rows)) groups = rows
      renderParents()
    },
    () => renderParents(),
  )
}

function onParentInput(): void {
  setFieldValue('group-parent-id', '')
  parentCreateName = ''
  parentError.value = null
  renderParents()
}

/** The blur collapse waits for a row's mousedown; a refocus must cancel it. */
let blurTimer: number | null = null

function cancelBlurCollapse(): void {
  if (blurTimer == null) return
  window.clearTimeout(blurTimer)
  blurTimer = null
}

function onParentBlur(): void {
  cancelBlurCollapse()
  blurTimer = window.setTimeout(() => {
    blurTimer = null
    hideParent()
  }, 200)
  parentError.value = parentNotPicked() ? PARENT_NOT_PICKED : null
}

function pickParent(row: ParentRow): void {
  setFieldValue('group-parent-id', row.id)
  setFieldValue('group-parent-search', row.label)
  parentCreateName = row.create ? row.label : ''
  parentError.value = null
  hideParentNow()
}

async function onSave(): Promise<void> {
  const name = fieldValue('group-name')
  if (!name.trim()) {
    await runtime().prksAlertMessage?.('Group name is required.', 'Validation')
    return
  }
  if (parentNotPicked()) {
    parentError.value = PARENT_NOT_PICKED
    focusField('group-parent-search')
    return
  }
  const w = runtime()
  await gate.save({
    modal: () => {
      const el = document.getElementById('group-modal')
      return el instanceof HTMLElement ? el : null
    },
    isBusy: () => groupBusy.value,
    setBusy: (busy) => {
      groupBusy.value = busy
    },
    readDraft: () => ({
      name,
      parentId: fieldValue('group-parent-id').trim(),
      parentQuery: fieldValue('group-parent-search').trim(),
      description: fieldValue('group-description'),
    }),
    resolveParent: resolveGroupParent,
    createGroup: createGroupDurably,
    alert: async (message, title) => {
      await w.prksAlertMessage?.(message, title)
    },
    failureMessage: groupCreateFailureMessage,
    afterCreate: finishGroupCreate,
  })
}

const onKeydown = createSubmitKeyHandler(['group-name'], () => void onSave())

function focusInitial(): void {
  focusField('group-name')
}

/** A closed dialog drops its draft, so the next New Group starts blank (#373). */
function resetFields(): void {
  setFieldValue('group-name', '')
  setFieldValue('group-description', '')
  setFieldValue('group-parent-search', '')
  setFieldValue('group-parent-id', '')
  parentCreateName = ''
  parentError.value = null
  hideParentNow()
}

/** Escape's inner layer: closes an open parent list. True when one was open. */
function closeParentList(): boolean {
  if (!parentOpen.value) return false
  cancelBlurCollapse()
  hideParentNow()
  return true
}

// The groups catalogue loads in the background: the dialog is usable at once,
// and the parent list fills when it arrives.
useDialogLifecycle({
  opened: () => {
    void prepare()
  },
  focusInitial,
  closed: resetFields,
  dismissInnerLayer: closeParentList,
})
</script>

<template>
  <div class="form-pane" @keydown="onKeydown">
    <p class="modal-helper">Groups can nest (e.g. Philosophy → Frankfurt School). People can belong to multiple groups.</p>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Name" for-id="group-name">
      <input
        id="group-name"
        type="text"
        placeholder="e.g. Frankfurt School"
        autocomplete="off"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      >
    </PrksField>
    <PrksField
      v-slot="{ labelledBy, describedBy }"
      label="Parent group (optional)"
      for-id="group-parent-search"
      help="Search for an existing group, or type a new name and choose Create group to make it a top-level group when you save."
      :error="parentError"
    >
      <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
        <div class="tag-add-shell__field">
          <span class="tag-add-shell__icon"><i data-lucide="search" class="prks-icon" aria-hidden="true"></i></span>
          <input
            id="group-parent-search"
            class="tag-add-shell__input"
            type="text"
            placeholder="Search or type new parent name…"
            autocomplete="off"
            aria-label="Search or type parent group"
            :aria-labelledby="labelledBy"
            :aria-describedby="describedBy"
            :aria-invalid="parentError ? 'true' : undefined"
            @focus="onParentFocus"
            @input="onParentInput"
            @blur="onParentBlur"
          >
        </div>
        <input id="group-parent-id" type="hidden">
        <div
          id="group-parent-results"
          class="combobox-results combobox-results--tag-panel"
          :class="{ hidden: parentHidden, 'is-open': parentOpen }"
        >
          <div v-if="parentRows.length === 0" class="result-item no-results">No groups yet. Type a name to create the parent.</div>
          <div
            v-for="row in parentRows"
            :key="row.create ? `create:${row.label}` : row.id"
            class="result-item"
            :class="{ 'result-item--create': row.create }"
            @mousedown.prevent="pickParent(row)"
          >{{ row.create ? `Create group "${row.label}"` : row.label }}</div>
        </div>
      </div>
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Description" for-id="group-description">
      <textarea
        id="group-description"
        placeholder="Optional"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      ></textarea>
    </PrksField>
    <PrksButton
      id="save-group-btn"
      variant="primary"
      :busy="groupBusy"
      busy-label="Creating…"
      @click="onSave"
    >Create group</PrksButton>
  </div>
</template>
