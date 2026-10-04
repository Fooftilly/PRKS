<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksWorkCard from '../../components/PrksWorkCard.vue'
import { useWorkCardCollection } from '../../components/use-work-card-collection'
import { workCardThumbOptions, type PrksWorkCardWork } from '../../components/work-card'
import { folderDetailIntentsKey } from './intents'
import type { FolderDetailProjection } from './projection'

const props = defineProps<{
  projection: FolderDetailProjection
}>()

const intents = inject(folderDetailIntentsKey)
const rootEl = ref<HTMLElement | null>(null)
const mainEl = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)

const ready = computed(() => props.projection.availability === 'ready' && !!props.projection.folder)
const unavailable = computed(() => props.projection.availability === 'unavailable')
const folder = computed(() => props.projection.folder)
const title = computed(() => folder.value?.title ?? '')
const description = computed(() => folder.value?.description || 'No description provided.')
const headerIcon = computed(() => window.prksPageHeaderIconHtml?.('folder') ?? '')
const trashIcon = computed(() => window.prksIcon?.('trash', { size: 'sm' }) ?? '')
const newFolderIcon = computed(() => window.prksIcon?.('plus', { size: 14 }) ?? '+')
const encodedId = computed(() => encodeURIComponent(String(folder.value?.id || '')))

const canDelete = computed(() => {
  const current = folder.value
  if (!current) return false
  const hasChildren = current.children.length > 0
  return current.works.length === 0 && !hasChildren
})

const summaryHtml = computed(() => {
  const current = folder.value
  if (!current) return ''
  return window.prksFolderDetailSummaryHtml?.(current.source) ?? ''
})

const navHtml = computed(() => {
  const current = folder.value
  if (!current) return ''
  return window.prksFolderDetailNavHtml?.(intents?.owner() ?? null, current.source) ?? ''
})

const subfoldersHtml = computed(() => {
  const current = folder.value
  if (!current) return ''
  return window.prksFolderDetailSubfoldersHtml?.(current.children) ?? ''
})

const collectionClass = computed(() => {
  void props.projection.generation
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn() : 'card-grid'
})

const works = computed(() => props.projection.effectiveWorks as readonly PrksWorkCardWork[])

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-folder-files') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
}

useWorkCardCollection(mainEl, { initWhen: () => !props.projection.offlineCached })


function rebindHierarchyNav(): void {
  const root = rootEl.value
  const current = folder.value
  if (!root || !current || !ready.value) return
  // v-html replaced the band. Remount listeners only. Tree refresh stays on
  // the route generation inside commitSurface.
  window.prksMountFolderHierarchyNav?.(intents?.owner() ?? null, current.source, root)
}

function commitSurface(): void {
  const root = rootEl.value
  const current = folder.value
  if (!root || !current || !ready.value) return
  window.prksCommitFolderDetailSurface?.(intents?.owner() ?? null, current.source, root, {
    preserveFolderWorkspace: props.projection.preserveWorkspace,
  })
}

function onDelete(): void {
  const id = folder.value?.id
  if (!id) return
  void intents?.remove(id)
}

function onNewFolder(): void {
  const source = folder.value?.source
  if (!source) return
  intents?.createChild(source)
}

onMounted(() => {
  paintMode()
  commitSurface()
})

watch(
  () => ({
    key: `${props.projection.generation}:${folder.value?.id || ''}:${props.projection.preserveWorkspace ? '1' : '0'}:${props.projection.availability}`,
    nav: navHtml.value,
  }),
  (current, previous) => {
    paintMode()
    if (current.key !== previous.key) {
      commitSurface()
      return
    }
    if (current.nav !== previous.nav) rebindHierarchyNav()
  },
  { flush: 'post' },
)
</script>

<template>
  <template v-if="unavailable">
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">Folder not available offline</h2>
    </div>
    <PrksInlineMessage data-prks-role="offline-unavailable">This item is not available offline.</PrksInlineMessage>
  </template>
  <PrksInlineMessage v-else-if="!ready" tone="error">Folder not found.</PrksInlineMessage>
  <div
    v-else
    ref="rootEl"
    class="prks-folder-detail"
    data-prks-role="folder-detail"
    data-prks-folder-detail-view
    data-prks-folder-layout="wide"
  >
    <aside class="prks-folder-detail__tree-pane" data-prks-role="folder-detail-tree" aria-label="Folder hierarchy">
      <div class="prks-folder-detail__tree-head">
        <a class="prks-folder-detail__tree-all" href="#/folders">All Folders</a>
        <button
          type="button"
          class="prks-btn prks-btn--secondary prks-folder-detail__tree-new"
          data-prks-role="folder-detail-new-folder"
          title="New folder"
          aria-label="New folder"
          @click="onNewFolder"
        >
          <span style="display: contents" v-html="newFolderIcon"></span>
        </button>
      </div>
      <div
        class="prks-folder-detail__tree-scroll"
        data-prks-folder-tree-host
        data-prks-folder-detail-tree-host
      >
        <PrksInlineMessage class="prks-folder-tree__empty">Loading folders…</PrksInlineMessage>
      </div>
    </aside>
    <div ref="mainEl" class="prks-folder-detail__main">
      <div class="prks-page-header page-header page-header--split prks-folder-detail__header">
        <div class="page-header__title-row">
          <h2 class="prks-page-title">
            <span style="display: contents" v-html="headerIcon"></span>
            {{ title }}
          </h2>
          <PrksButton
            v-if="canDelete"
            variant="danger"
            :data-delete-folder-id="encodedId"
            @click="onDelete"
          >
            <span style="display: contents" v-html="trashIcon"></span>
            Delete Folder
          </PrksButton>
        </div>
        <span style="display: contents" v-html="summaryHtml"></span>
      </div>
      <span style="display: contents" v-html="navHtml"></span>
      <p class="mb-md">{{ description }}</p>
      <span style="display: contents" v-html="subfoldersHtml"></span>
      <div class="prks-page-header page-header page-header--split prks-folder-detail__files-header">
        <div class="page-header__title-row">
          <h3>Files</h3>
          <div ref="modeHost" data-prks-folder-detail-mode-host style="display: contents"></div>
        </div>
      </div>
      <div :class="collectionClass">
        <PrksWorkCard
          v-for="work in works"
          :key="String(work.id ?? '')"
          :work="work"
          :options="workCardThumbOptions(projection.offlineCached)"
        />
      </div>
    </div>
  </div>
</template>
