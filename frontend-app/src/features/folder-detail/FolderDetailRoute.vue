<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksWorkCard from '../../components/PrksWorkCard.vue'
import { useWorkCardCollection } from '../../components/use-work-card-collection'
import { workCardCollectionFingerprint, workCardThumbOptions, type PrksWorkCardWork } from '../../components/work-card'
import FolderTree from '../folder-library/FolderTree.vue'
import { acceptFolderRows } from '../folder-library/projection'
import { folderDetailExpandIds, readFolderDetailTreeSnapshot } from '../folder-library/tree'
import type { FolderRow } from '../folder-library/types'
import FolderDetailNav from './FolderDetailNav.vue'
import FolderSubfolders from './FolderSubfolders.vue'
import { folderDetailIntentsKey } from './intents'
import type { FolderDetailProjection } from './projection'

const props = defineProps<{
  projection: FolderDetailProjection
}>()

const intents = inject(folderDetailIntentsKey)
const rootEl = ref<HTMLElement | null>(null)
const mainEl = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)
const treeHost = ref<HTMLElement | null>(null)
const hierarchyRows = ref<FolderRow[]>([])
const hierarchyStatus = ref<'loading' | 'ready' | 'error'>('loading')
const expandedIds = ref(new Set(intents?.readExpanded?.() ?? []))

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

const summaryParts = computed(() => {
  const current = folder.value
  if (!current) return []
  const childN = current.children.length
  const workN = current.works.length
  return [
    `${childN} subfolder${childN === 1 ? '' : 's'}`,
    `${workN} file${workN === 1 ? '' : 's'}`,
  ]
})

const parentRef = computed(() => {
  const parent = folder.value?.source?.parent
  if (!parent || typeof parent !== 'object') return null
  const record = parent as { id?: unknown; title?: unknown }
  const id = record.id == null ? '' : String(record.id)
  const titleText = record.title == null ? '' : String(record.title)
  if (!id || !titleText) return null
  return { id, title: titleText }
})

const navKey = computed(() => {
  const current = folder.value
  if (!current) return ''
  const parent = parentRef.value
  return `${current.id}\u0000${current.title}\u0000${parent?.id || ''}\u0000${parent?.title || ''}`
})

const tabId = computed(() => {
  const id = intents?.owner()?.tabId
  return id == null ? '' : String(id)
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

useWorkCardCollection(mainEl, {
  initWhen: () => !props.projection.offlineCached,
  source: () =>
    workCardCollectionFingerprint(works.value, {
      suppressThumbnail: props.projection.offlineCached,
    }),
})

function rememberExpanded(): void {
  intents?.writeExpanded?.([...expandedIds.value])
}

function mergeExpanded(ids: readonly string[]): void {
  if (!ids.length) return
  const next = new Set(expandedIds.value)
  let changed = false
  for (const id of ids) {
    if (!next.has(id)) {
      next.add(id)
      changed = true
    }
  }
  if (!changed) return
  expandedIds.value = next
  rememberExpanded()
}

function applyTreeSnapshot(value: unknown): void {
  const snapshot = readFolderDetailTreeSnapshot(value)
  if (!snapshot) return
  if (snapshot.status === 'error') {
    hierarchyStatus.value = 'error'
    return
  }
  const rows = acceptFolderRows(snapshot.rows)
  if (!snapshot.selectionOnly) hierarchyRows.value = rows
  hierarchyStatus.value = 'ready'
  const selected = snapshot.selectedId || folder.value?.id || ''
  const displayed = snapshot.selectionOnly ? hierarchyRows.value : rows
  mergeExpanded(folderDetailExpandIds(selected, displayed))
}

function bindTreeApply(): void {
  const host = treeHost.value as (HTMLElement & { __prksApplyFolderDetailTree?: (value: unknown) => void }) | null
  if (!host) return
  host.__prksApplyFolderDetailTree = applyTreeSnapshot
}

function rebindHierarchyNav(): void {
  const root = rootEl.value
  const current = folder.value
  if (!root || !current || !ready.value) return
  window.prksMountFolderHierarchyNav?.(intents?.owner() ?? null, current.source, root)
}

function commitSurface(): void {
  const root = rootEl.value
  const current = folder.value
  if (!root || !current || !ready.value) return
  bindTreeApply()
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

function onToggleFolder(folderId: string): void {
  const next = new Set(expandedIds.value)
  if (next.has(folderId)) next.delete(folderId)
  else next.add(folderId)
  expandedIds.value = next
  rememberExpanded()
}

onMounted(() => {
  paintMode()
  commitSurface()
})

watch(
  () => ({
    key: `${props.projection.generation}:${folder.value?.id || ''}:${props.projection.preserveWorkspace ? '1' : '0'}:${props.projection.availability}`,
    nav: navKey.value,
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
        ref="treeHost"
        class="prks-folder-detail__tree-scroll"
        data-prks-folder-tree-host
        data-prks-folder-detail-tree-host
      >
        <PrksInlineMessage v-if="hierarchyStatus === 'loading'" class="prks-folder-tree__empty">
          Loading folders…
        </PrksInlineMessage>
        <PrksInlineMessage v-else-if="hierarchyStatus === 'error'" class="prks-folder-tree__empty">
          Could not load folders.
        </PrksInlineMessage>
        <FolderTree
          v-else
          variant="detail"
          :folders="hierarchyRows"
          :expanded-ids="expandedIds"
          :selected-id="folder?.id || null"
          @toggle="onToggleFolder"
        />
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
        <p v-if="summaryParts.length" class="prks-page-summary">
          <template v-for="(part, index) in summaryParts" :key="part">
            <span v-if="index > 0" class="prks-summary-sep" aria-hidden="true"> · </span>
            {{ part }}
          </template>
        </p>
      </div>
      <FolderDetailNav
        v-if="folder"
        :folder-id="folder.id"
        :title="title"
        :parent-id="parentRef?.id"
        :parent-title="parentRef?.title"
        :tab-id="tabId"
      />
      <p class="mb-md">{{ description }}</p>
      <FolderSubfolders :children="folder?.children || []" />
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
