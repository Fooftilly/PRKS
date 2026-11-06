<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import type { ProcessingQueue, ProcessingQueueOption } from './queue'
import ProcessingQueueCard from './ProcessingQueueCard.vue'

const props = defineProps<{
  queue: ProcessingQueue
  folders: ProcessingQueueOption[]
  docTypes: ProcessingQueueOption[]
}>()

const state = props.queue.state
const picker = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
let dragDepth = 0

const selectedCount = computed(() => state.items.filter((one) => state.selected.has(one.id)).length)
const allSelected = computed(() => state.items.length > 0 && selectedCount.value === state.items.length)

function pick(): void {
  picker.value?.click()
}

function picked(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = input.files ? Array.from(input.files) : []
  input.value = ''
  if (files.length) void props.queue.upload(files)
}

function dragEnter(event: DragEvent): void {
  if (!event.dataTransfer?.types.includes('Files')) return
  event.preventDefault()
  dragDepth += 1
  dragging.value = true
}

function dragLeave(): void {
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragging.value = false
}

function dropped(event: DragEvent): void {
  event.preventDefault()
  dragDepth = 0
  dragging.value = false
  const files = event.dataTransfer?.files ? Array.from(event.dataTransfer.files) : []
  if (files.length) void props.queue.upload(files)
}

function selectAll(on: boolean): void {
  for (const item of state.items) props.queue.toggle(item.id, on)
}

onMounted(() => {
  void props.queue.load()
})
</script>

<template>
  <div class="prks-processing-page" data-prks-processing-queue>
    <div class="prks-page-header page-header page-header--split">
      <h2 class="prks-page-title">Files for Processing</h2>
      <div class="prks-spacer"></div>
      <PrksButton variant="secondary" :busy="state.loading" busy-label="Loading..." data-processing-refresh @click="queue.load()">Refresh</PrksButton>
      <PrksButton variant="primary" :busy="state.uploading > 0" busy-label="Uploading..." data-processing-upload @click="pick">Upload PDFs</PrksButton>
      <input
        ref="picker"
        class="hidden"
        type="file"
        accept="application/pdf,.pdf"
        multiple
        data-processing-picker
        aria-label="Choose PDFs to upload"
        @change="picked"
      >
    </div>
    <div
      class="prks-processing-drop"
      :class="{ 'prks-processing-drop--active': dragging }"
      data-processing-drop
      @dragenter="dragEnter"
      @dragover.prevent
      @dragleave="dragLeave"
      @drop="dropped"
    >
      <p class="meta-row meta-row--lede">
        Drop PDFs here, or use Upload PDFs. Items wait here, outside search and the graph, until you import them.
      </p>
    </div>
    <ul v-if="state.uploads.length" class="prks-processing-uploads" data-processing-uploads aria-live="polite">
      <li
        v-for="(note, index) in state.uploads"
        :key="index"
        :class="{ 'meta-row--error': note.state === 'failed' }"
        class="meta-row"
        :data-processing-upload-state="note.state"
      >
        <strong>{{ note.filename }}</strong>:
        <template v-if="note.state === 'sending'">
          Uploading…
          <progress
            max="100"
            :value="note.percent"
            :aria-label="`Uploading ${note.filename}`"
            data-processing-upload-progress
          >{{ note.percent }}%</progress>
          {{ note.percent }}%
        </template>
        <template v-else>{{ note.message }}</template>
        <template v-if="note.duplicates">
          Same file as
          <template v-for="work in note.duplicates.works" :key="`w-${work.id}`">
            <a :href="`#/works/${encodeURIComponent(work.id)}`">{{ work.title || 'a Work' }}</a>{{ ' ' }}
          </template>
          <template v-for="other in note.duplicates.items" :key="`i-${other.id}`">“{{ other.original_filename }}” </template>
          <template v-if="note.duplicates.more_items || note.duplicates.more_works">and more</template>.
        </template>
      </li>
    </ul>
    <ul v-if="state.imported.length" class="prks-processing-imported" data-processing-imported aria-live="polite">
      <li v-for="done in state.imported" :key="done.itemId" class="meta-row">
        Imported <a :href="`#/works/${encodeURIComponent(done.workId)}`">{{ done.filename || 'the Work' }}</a>.
      </li>
    </ul>
    <PrksInlineMessage v-if="state.loadError" tone="error" status data-processing-load-error>{{ state.loadError }}</PrksInlineMessage>
    <div v-if="state.items.length" class="prks-processing-batch">
      <label class="meta-row">
        <input type="checkbox" :checked="allSelected" data-processing-select-all @change="selectAll(($event.target as HTMLInputElement).checked)">
        Select all shown
      </label>
      <PrksButton variant="primary" :disabled="!selectedCount" data-processing-import-selected @click="queue.importSelected()">
        Import selected ({{ selectedCount }})
      </PrksButton>
    </div>
    <div class="list-view" data-processing-list>
      <p v-if="state.loaded && !state.items.length" class="meta-row" data-processing-empty>No PDF files waiting for processing.</p>
      <ProcessingQueueCard
        v-for="item in state.items"
        :key="item.id"
        :item="item"
        :folders="folders"
        :doc-types="docTypes"
        :selected="state.selected.has(item.id)"
        :busy="state.busy.has(item.id)"
        :error="state.errors[item.id] || ''"
        @save="(edit) => queue.save(item.id, edit)"
        @import="queue.importOne(item.id)"
        @remove="queue.remove(item.id)"
        @select="(on) => queue.toggle(item.id, on)"
      />
      <div v-if="state.nextCursor" class="prks-processing-load-more-row">
        <PrksButton variant="secondary" :busy="state.loading" busy-label="Loading..." data-processing-more @click="queue.load(true)">Load more</PrksButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.prks-processing-drop {
  border: 2px dashed var(--border-color, currentColor);
  border-radius: 0.5rem;
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
}

.prks-processing-drop--active {
  border-style: solid;
}

.prks-processing-batch {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 0.75rem;
}

.prks-processing-uploads,
.prks-processing-imported {
  list-style: none;
  padding: 0;
}
</style>
