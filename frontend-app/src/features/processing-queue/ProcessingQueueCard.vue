<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { processingItemPdfUrl, type ProcessingItem, type ProcessingItemEdit } from '../../api/processing-items'
import type { ProcessingQueueOption } from './queue'

const props = defineProps<{
  item: ProcessingItem
  folders: ProcessingQueueOption[]
  docTypes: ProcessingQueueOption[]
  selected: boolean
  busy: boolean
  error: string
}>()

const emit = defineEmits<{
  save: [edit: ProcessingItemEdit]
  import: []
  remove: []
  select: [on: boolean]
}>()

/** A viewer the page mounted: the PRKS PDF viewer in read-only preview mode. */
interface PdfPreview {
  destroy?: () => void
}

type MountPreview = (target: HTMLElement, src: string, title: string) => Promise<PdfPreview | null>

const previewing = ref(false)
const previewHost = ref<HTMLElement | null>(null)
const previewError = ref('')
let preview: PdfPreview | null = null
let previewRun = 0

function closePreview(): void {
  previewRun += 1
  try {
    preview?.destroy?.()
  } catch {
    // The viewer's own teardown; nothing here depends on it.
  }
  preview = null
}

async function togglePreview(): Promise<void> {
  if (previewing.value) {
    closePreview()
    previewing.value = false
    return
  }
  previewing.value = true
  previewError.value = ''
  const run = ++previewRun
  await nextTick()
  const mount = (globalThis as { prksMountPdfPreview?: MountPreview }).prksMountPdfPreview
  const host = previewHost.value
  if (typeof mount !== 'function' || !host) {
    previewError.value = 'The PDF viewer is not available.'
    return
  }
  try {
    const viewer = await mount(host, processingItemPdfUrl(props.item.id), props.item.original_filename)
    if (run !== previewRun) {
      viewer?.destroy?.()
      return
    }
    preview = viewer
  } catch {
    if (run === previewRun) previewError.value = 'The preview could not be opened.'
  }
}

onBeforeUnmount(closePreview)

/** The last save's outcome, shown beside the draft. */
const saveStatus = ref<'' | 'saving' | 'saved'>('')
watch(() => props.busy, (busy, was) => {
  if (saveStatus.value === 'saving' && was && !busy) saveStatus.value = props.error ? '' : 'saved'
})
const domId = computed(() => `prks-pq-${props.item.id}`)

function fromItem(item: ProcessingItem): Required<ProcessingItemEdit> {
  const draft = item.draft
  return {
    title: draft?.title ?? '',
    author_text: draft?.author_text ?? '',
    year: draft?.year ?? '',
    doc_type: draft?.doc_type ?? '',
    target_folder_id: draft?.target_folder_id ?? '',
  }
}

const draft = reactive(fromItem(props.item))
// A new revision (saved here, or refused with the item as it now is) repaints the draft.
watch(() => props.item.revision, () => Object.assign(draft, fromItem(props.item)))

const dirty = computed(() => {
  const base = fromItem(props.item)
  return (Object.keys(base) as (keyof typeof base)[]).some((key) => base[key] !== draft[key])
})

const size = computed(() => {
  const bytes = props.item.byte_size
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
})

const folderChoices = computed(() => {
  const id = draft.target_folder_id
  if (!id || props.folders.some((one) => one.value === id)) return props.folders
  return [...props.folders, { value: id, label: '(Unknown folder)' }]
})

const docTypeChoices = computed(() => {
  const value = draft.doc_type
  if (!value || props.docTypes.some((one) => one.value === value)) return props.docTypes
  return [...props.docTypes, { value, label: value }]
})

function save(): void {
  saveStatus.value = 'saving'
  emit('save', { ...draft })
}

watch(draft, () => {
  if (saveStatus.value === 'saved') saveStatus.value = ''
})
</script>

<template>
  <article class="project-card prks-processing-card" :data-processing-item-id="item.id">
    <header class="prks-processing-card__header">
      <label class="prks-processing-card__select">
        <input
          type="checkbox"
          :checked="selected"
          :disabled="busy"
          data-processing-select
          :aria-label="`Select ${item.original_filename} for import`"
          @change="emit('select', ($event.target as HTMLInputElement).checked)"
        >
      </label>
      <div class="card-title prks-processing-card__title">{{ item.original_filename }}</div>
      <p class="meta-row prks-processing-card__meta">{{ size }} · uploaded {{ item.created_at.slice(0, 10) }}</p>
    </header>
    <form class="form-pane form-pane--tight prks-processing-card__core" @submit.prevent="save">
      <div class="form-row">
        <label :for="`${domId}-title`">Title</label>
        <input :id="`${domId}-title`" v-model="draft.title" type="text" data-field="title" placeholder="Library title">
      </div>
      <div class="form-row">
        <label :for="`${domId}-author`">Author</label>
        <input :id="`${domId}-author`" v-model="draft.author_text" type="text" data-field="author_text">
      </div>
      <div class="form-row">
        <label :for="`${domId}-year`">Year</label>
        <input :id="`${domId}-year`" v-model="draft.year" type="text" inputmode="numeric" data-field="year">
      </div>
      <div class="form-row">
        <label :for="`${domId}-type`">Type</label>
        <select :id="`${domId}-type`" v-model="draft.doc_type" data-field="doc_type">
          <option value="">Not set</option>
          <option v-for="option in docTypeChoices" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </div>
      <div class="form-row">
        <label :for="`${domId}-folder`">Folder</label>
        <select :id="`${domId}-folder`" v-model="draft.target_folder_id" data-field="target_folder_id">
          <option value="">No folder</option>
          <option v-for="option in folderChoices" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </div>
      <PrksInlineMessage v-if="error" tone="error" status data-processing-error>{{ error }}</PrksInlineMessage>
      <div class="prks-processing-card__actions">
        <PrksButton type="submit" variant="secondary" :disabled="!dirty" :busy="busy" busy-label="Working..." data-processing-save>Save draft</PrksButton>
        <PrksButton variant="primary" :disabled="dirty" :busy="busy" busy-label="Working..." data-processing-import @click="emit('import')">Import</PrksButton>
        <PrksButton variant="secondary" :aria-expanded="previewing" data-processing-preview @click="togglePreview">
          {{ previewing ? 'Hide preview' : 'Preview' }}
        </PrksButton>
        <PrksButton variant="quiet-danger" :busy="busy" busy-label="Working..." data-processing-remove @click="emit('remove')">Remove</PrksButton>
      </div>
      <p class="meta-row" role="status" data-processing-save-status>
        <template v-if="saveStatus === 'saving'">Saving…</template>
        <template v-else-if="dirty">Unsaved changes. Save the draft before importing.</template>
        <template v-else-if="saveStatus === 'saved'">Draft saved.</template>
      </p>
    </form>
    <div
      v-if="previewing"
      ref="previewHost"
      class="prks-processing-card__preview"
      :aria-label="`Preview of ${item.original_filename}`"
      data-processing-preview-host
    ></div>
    <PrksInlineMessage v-if="previewing && previewError" tone="error" status data-processing-preview-error>{{ previewError }}</PrksInlineMessage>
  </article>
</template>

<style scoped>
.prks-processing-card__header {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.prks-processing-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.prks-processing-card__preview {
  width: 100%;
  height: 32rem;
  position: relative;
  border: 1px solid var(--border-color, currentColor);
  margin-top: 0.75rem;
}
</style>
