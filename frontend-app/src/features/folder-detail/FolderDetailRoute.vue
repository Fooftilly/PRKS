<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { folderDetailIntentsKey } from './intents'
import { folderDetailWorksHtml } from './legacy-work-card'
import type { FolderDetailProjection } from './projection'

const props = defineProps<{
  projection: FolderDetailProjection
}>()

const intents = inject(folderDetailIntentsKey)
const rootEl = ref<HTMLElement | null>(null)
const mainEl = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)
const collectionEl = ref<HTMLElement | null>(null)

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

const collectionHtml = computed(() =>
  folderDetailWorksHtml(props.projection.effectiveWorks, props.projection.offlineCached),
)

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-folder-files') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
}

function releaseOwnedThumbResources(root: ParentNode | null): void {
  if (!root) return
  // Scoped only. Another pane may own the preview or its own lazy thumbs.
  if (typeof window.prksReleaseWorkThumbPreview === 'function') {
    window.prksReleaseWorkThumbPreview(root)
  }
  if (typeof window.prksReleaseLazyWorkThumbs === 'function') {
    window.prksReleaseLazyWorkThumbs(root)
  }
}

function paintCollection(): void {
  const main = mainEl.value
  // Folder→Folder keeps this shell and rewrites the cards. Release while the
  // previous thumbs are still inside main; scoped release leaves another pane alone.
  releaseOwnedThumbResources(main)
  const el = collectionEl.value
  if (!el) return
  el.innerHTML = collectionHtml.value
  const offlineCached = props.projection.offlineCached
  if (!offlineCached && typeof window.prksInitLazyWorkThumbs === 'function') {
    window.prksInitLazyWorkThumbs(el)
  }
  window.prksBindWorkBrowseMode?.(rootEl.value)
  window.prksRefreshIcons?.(rootEl.value)
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
  paintCollection()
  commitSurface()
})

onBeforeUnmount(() => {
  // beginRoute dismisses this tree before app.js calls prksReleaseWorkThumbPreview
  // and prksReleaseLazyWorkThumbs(contentDiv). Both only see thumbs still under this root.
  releaseOwnedThumbResources(rootEl.value || mainEl.value)
})

watch(
  () =>
    `${props.projection.generation}:${folder.value?.id || ''}:${props.projection.preserveWorkspace ? '1' : '0'}:${props.projection.availability}`,
  () => {
    paintMode()
    commitSurface()
  },
  { flush: 'post' },
)

watch(collectionHtml, () => {
  paintCollection()
}, { flush: 'post' })
</script>

<template>
  <template v-if="unavailable">
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">Folder not available offline</h2>
    </div>
    <p class="prks-inline-message" data-prks-role="offline-unavailable">This item is not available offline.</p>
  </template>
  <p v-else-if="!ready" class="prks-inline-message prks-inline-message--error">Folder not found.</p>
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
        <p class="prks-inline-message prks-folder-tree__empty">Loading folders…</p>
      </div>
    </aside>
    <div ref="mainEl" class="prks-folder-detail__main">
      <div class="prks-page-header page-header page-header--split prks-folder-detail__header">
        <div class="page-header__title-row">
          <h2 class="prks-page-title">
            <span style="display: contents" v-html="headerIcon"></span>
            {{ title }}
          </h2>
          <button
            v-if="canDelete"
            type="button"
            class="prks-btn prks-btn--danger"
            :data-delete-folder-id="encodedId"
            @click="onDelete"
          >
            <span style="display: contents" v-html="trashIcon"></span>
            Delete Folder
          </button>
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
      <div ref="collectionEl" :class="collectionClass"></div>
    </div>
  </div>
</template>
