<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import type { ProcessingIntents } from './intents'
import {
  PROCESSING_PAGE_SIZE,
  type ProcessingFolder,
  type ProcessingPerson,
  type ProcessingProjection,
} from './projection'
import ProcessingFileCard from './ProcessingFileCard.vue'

const props = defineProps<{
  projection: ProcessingProjection
  intents: ProcessingIntents
}>()

const rootEl = ref<HTMLElement | null>(null)
const scanning = ref(false)
const refreshError = ref('')
const visibleCount = ref(props.projection.visibleCount)
const people = ref<ProcessingPerson[]>(props.projection.people)
const folders = ref<ProcessingFolder[]>(props.projection.folders)

const files = computed(() => props.projection.files)
const visibleFiles = computed(() => files.value.slice(0, visibleCount.value))
const remaining = computed(() => Math.max(0, files.value.length - visibleCount.value))
const loadMoreCount = computed(() => Math.min(PROCESSING_PAGE_SIZE, remaining.value))

function loadMore(): void {
  visibleCount.value = Math.min(files.value.length, visibleCount.value + PROCESSING_PAGE_SIZE)
}

async function refresh(): Promise<void> {
  scanning.value = true
  try {
    const outcome = await props.intents.reload({ visibleCount: visibleCount.value })
    if (!rootEl.value?.isConnected) return
    if (outcome.status === 'error') refreshError.value = outcome.message
    else if (outcome.status === 'success') refreshError.value = ''
  } catch (err) {
    if (!rootEl.value?.isConnected) return
    refreshError.value = err instanceof Error && err.message.trim()
      ? err.message.trim()
      : 'Could not refresh files for processing.'
  } finally {
    if (rootEl.value?.isConnected) scanning.value = false
  }
}

function replacePeople(next: ProcessingPerson[]): void {
  people.value = next
}

function replaceFolders(next: ProcessingFolder[]): void {
  folders.value = next
}

onMounted(() => {
  if (rootEl.value) props.intents.attachResources(rootEl.value)
})

onBeforeUnmount(() => {
  props.intents.releaseResources()
})
</script>

<template>
  <div ref="rootEl" class="prks-processing-page" data-prks-processing-page>
    <div class="prks-page-header page-header page-header--split">
      <h2 class="prks-page-title">Files for Processing</h2>
      <div class="prks-spacer"></div>
      <PrksButton
        id="prks-processing-refresh"
        variant="secondary"
        :busy="scanning"
        busy-label="Scanning..."
        @click="refresh"
      >Refresh folder scan</PrksButton>
    </div>
    <p
      v-if="refreshError"
      class="prks-inline-message prks-inline-message--error"
      role="status"
      data-prks-processing-refresh-error
    >{{ refreshError }}</p>
    <p class="meta-row meta-row--lede">
      Inbox reads PDFs recursively from <code>/data/for_processing</code>. Files here stay out of library search and graph until imported.
    </p>
    <p v-if="remaining > 0" class="meta-row">
      Showing first {{ visibleCount }} of {{ files.length }} files to keep page responsive.
    </p>
    <div class="prks-processing-main-layout">
      <div class="list-view prks-processing-main-layout__list" data-prks-processing-list>
        <p v-if="!files.length" class="meta-row">No PDF files waiting for processing.</p>
        <template v-for="file in visibleFiles" :key="file.id">
          <ProcessingFileCard
            :file="file"
            :people="people"
            :folders="folders"
            :role-types="projection.roleTypes"
            :dom-prefix="projection.domPrefix"
            :visible-count="visibleCount"
            :intents="intents"
            @people="replacePeople"
            @folders="replaceFolders"
          />
          <div v-once class="work-html-slot" :data-prks-processing-anchor="file.id"></div>
        </template>
        <div v-if="remaining > 0" class="prks-processing-load-more-row">
          <PrksButton variant="secondary" @click="loadMore">Load {{ loadMoreCount }} more</PrksButton>
        </div>
      </div>
      <div v-once class="work-html-slot" data-prks-processing-anchor="layout"></div>
    </div>
  </div>
</template>
