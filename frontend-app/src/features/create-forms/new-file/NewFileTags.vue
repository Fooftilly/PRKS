<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { fieldValue, setFieldValue } from '../dom'
import { useInlineCombobox } from './combobox'
import { alertMessage, loadList, runtime, type TagRow } from './runtime'
import { useNewFileState } from './state'

const state = useNewFileState()

interface TagOption {
  key: string
  tag: TagRow
  label: string
}

const rows = ref<TagOption[]>([])
const createName = ref('')
let renderSeq = 0

function attachedIds(): Set<string> {
  return new Set(state.tags.value.map((tag) => tag.id))
}

function attach(id: string, name: string): void {
  if (attachedIds().has(id)) return
  state.tags.value = state.tags.value.concat([{ id, name }])
}

async function render(): Promise<void> {
  const seq = ++renderSeq
  const w = runtime()
  const all = await loadList(() => (w.fetchTags ? w.fetchTags({ used: false }) : Promise.resolve([])))
  // A newer keystroke started its own render: never show older rows.
  if (seq !== renderSeq) return
  const value = fieldValue('upload-tag-search').trim()
  const lower = value.toLowerCase()
  const attached = attachedIds()
  const available = all.filter((tag) => !attached.has(tag.id))
  const matches = (tag: TagRow) => (w.prksTagMatchesQuery ? w.prksTagMatchesQuery(tag, lower) : true)
  const exact = available.some((tag) => (w.prksTagExactMatch ? w.prksTagExactMatch(tag, lower) : false))
  const filtered = (value ? available.filter(matches) : available).slice(0, 40)
  createName.value = value && !exact ? value : ''
  rows.value = filtered.map((tag) => ({
    key: tag.id,
    tag,
    label: w.prksTagComboboxLabel ? w.prksTagComboboxLabel(tag, lower) : String(tag.name || ''),
  }))
  if (!createName.value && rows.value.length === 0) {
    combobox.hide()
    return
  }
  combobox.shown()
}

const combobox = useInlineCombobox('upload-tag-search', 'upload-tag-results', {
  onOpen: () => void render(),
  enterCreates: true,
})

function pick(row: TagOption): void {
  attach(row.tag.id, String(row.tag.name || ''))
  setFieldValue('upload-tag-search', '')
  combobox.hide()
}

function create(): void {
  const name = createName.value
  if (!name) return
  const w = runtime()
  // Create File waits for this, so the Work is created with the tag or not at all.
  void state.trackQuickCreate(
    (async () => {
      try {
        const known = await loadList(w.fetchTags)
        if (typeof w.prksCreateTagDurably !== 'function') throw new Error('Could not create tag')
        const created = await w.prksCreateTagDurably({ name, color: '#6d6cf7' }, known)
        attach(created.entity_id, name)
        setFieldValue('upload-tag-search', '')
        combobox.hide()
        return true
      } catch (error) {
        const message =
          typeof w.prksTagVocabularyMessage === 'function'
            ? w.prksTagVocabularyMessage(error, 'create this tag')
            : 'Could not create this tag.'
        await alertMessage(message, 'Could not save')
        return false
      }
    })(),
  )
}

function remove(idx: number): void {
  state.tags.value = state.tags.value.filter((_tag, i) => i !== idx)
  void nextTick(() => document.getElementById('upload-tag-search')?.focus())
}

function reset(): void {
  setFieldValue('upload-tag-search', '')
  combobox.hideNow()
}

defineExpose({ reset, close: combobox.close })
</script>

<template>
  <div id="work-upload-tags-block" class="prks-field">
    <p class="tag-add-field__caption">Tags</p>
    <div class="tag-add-shell combobox-container tag-add-shell--flush">
      <div class="tag-add-shell__field">
        <span class="tag-add-shell__icon"><i data-lucide="plus" class="prks-icon" aria-hidden="true"></i></span>
        <input
          id="upload-tag-search"
          type="text"
          class="tag-add-shell__input"
          placeholder="Search or create tag…"
          maxlength="300"
          autocomplete="off"
          aria-label="Add tag for new file"
          @focus="combobox.cancelBlur(); render()"
          @input="render()"
          @blur="combobox.onBlur"
        >
      </div>
      <div
        id="upload-tag-results"
        class="combobox-results combobox-results--tag-panel"
        :class="{ hidden: combobox.hidden.value, 'is-open': combobox.open.value }"
      >
        <div
          v-if="createName"
          class="result-item result-item--create"
          @mousedown.prevent="create"
        >Create tag "{{ createName }}"</div>
        <div
          v-for="row in rows"
          :key="row.key"
          class="result-item"
          @mousedown.prevent="pick(row)"
        >{{ row.label }}</div>
      </div>
    </div>
    <div id="upload-tags-list" class="tag-cloud work-tags-list">
      <span v-if="state.tags.value.length === 0" class="status-chip-list__empty">No tags selected</span>
      <span v-for="(tag, idx) in state.tags.value" :key="tag.id" class="tag work-tag-chip">{{ tag.name }}
        <input type="hidden" data-prks-count data-upload-tag-id :value="tag.id">
        <button
          type="button"
          class="work-tag-remove"
          title="Remove"
          :aria-label="`Remove tag ${tag.name}`"
          @click="remove(idx)"
        >&times;</button></span>
    </div>
  </div>
</template>
