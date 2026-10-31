<script setup lang="ts">
import { ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import { useComboboxPanel } from './combobox-panel'
import { fieldValue, setFieldValue } from './dom'
import { submitGroupCreate } from './submit'

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

const groupBusy = ref(false)
const parentRows = ref<Array<{ id: string; label: string }>>([])
const { hidden: parentHidden, open: parentOpen, show: showParent, hide: hideParent, hideNow: hideParentNow } =
  useComboboxPanel()

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
  parentRows.value = filtered.slice(0, 80).map((group) => ({
    id: String(group.id || ''),
    label: rowLabel(group),
  }))
  showParent()
}

async function prepare(): Promise<void> {
  setFieldValue('group-parent-search', '')
  setFieldValue('group-parent-id', '')
  hideParentNow()
  try {
    const rows = await runtime().prksEnsureAllGroupsCache?.()
    groups = Array.isArray(rows) ? rows : []
  } catch {
    groups = []
  }
}

function onParentFocus(): void {
  void runtime().prksEnsureAllGroupsCache?.().then((rows) => {
    if (Array.isArray(rows)) groups = rows
    renderParents()
  })
}

function onParentInput(): void {
  setFieldValue('group-parent-id', '')
  renderParents()
}

function onParentBlur(): void {
  window.setTimeout(() => hideParent(), 200)
}

function pickParent(row: { id: string; label: string }): void {
  setFieldValue('group-parent-id', row.id)
  setFieldValue('group-parent-search', row.label)
  hideParentNow()
}

async function onSave(): Promise<void> {
  if (groupBusy.value) return
  const name = fieldValue('group-name')
  if (!name.trim()) {
    await runtime().prksAlertMessage?.('Group name is required.', 'Validation')
    return
  }
  groupBusy.value = true
  try {
    await submitGroupCreate({
      name,
      parentId: fieldValue('group-parent-id').trim(),
      parentQuery: fieldValue('group-parent-search').trim(),
      description: fieldValue('group-description'),
    })
  } finally {
    groupBusy.value = false
  }
}

defineExpose({ prepare })
</script>

<template>
  <div class="form-pane">
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
      help="Search for an existing group or type a new name — it will be created as a top-level group when you save."
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
          <div v-if="parentRows.length === 0" class="result-item no-results">No matching groups — type a new name to create the parent when you save.</div>
          <div
            v-for="row in parentRows"
            :key="row.id"
            class="result-item"
            @mousedown.prevent="pickParent(row)"
          >{{ row.label }}</div>
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
