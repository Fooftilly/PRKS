<script setup lang="ts">
import { useDebounceFn, useEventListener } from '@vueuse/core'
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  shallowRef,
  watch,
} from 'vue'
import { folderLibraryIntentsKey } from './intents'
import {
  folderLibraryCatalogGlanceParts,
  type FolderLibraryProjection,
} from './projection'
import {
  filterRecentlyAddedWorks,
  recentlyAddedDateLabel,
} from './recently-added'
import {
  releaseFolderLibraryBrowseResources,
  replaceFolderLibraryBrowseHtml,
} from './resources'
import {
  folderLibraryExpandToggleInnerHtml,
  folderLibraryExpandToggleLabel,
  folderTreeAllCollapsed,
  folderTreeHasCollapsibleNodes,
  legacyFolderTreeHtml,
  legacyWorkCardHtml,
  pageSummaryHtml,
  tagSearchIconHtml,
  workBrowseCollectionClass,
  workBrowseModeToggleHtml,
} from './legacy-tree'
import {
  readFolderLibraryFilesFilter,
  readFolderLibraryFilter,
  readFolderLibraryTab,
  writeFolderLibraryFilesFilter,
  writeFolderLibraryFilter,
  writeFolderLibraryTab,
} from './storage'
import type { FolderLibraryTab, RecentlyAddedWork } from './types'

const props = defineProps<{
  projection: FolderLibraryProjection
}>()

const intents = inject(folderLibraryIntentsKey)

const rootEl = ref<HTMLElement | null>(null)
const glanceHost = ref<HTMLElement | null>(null)
const treeHost = ref<HTMLElement | null>(null)
const recentlyAddedPane = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)
const folderSearchInput = ref<HTMLInputElement | null>(null)
const filesSearchInput = ref<HTMLInputElement | null>(null)
const expandToggleEl = ref<HTMLButtonElement | null>(null)

const activeTab = ref<FolderLibraryTab>(readFolderLibraryTab())
const folderFilter = ref(readFolderLibraryFilter())
const filesFilter = ref(readFolderLibraryFilesFilter())

const recentlyAddedWorks = shallowRef<RecentlyAddedWork[] | null>(null)
const recentlyAddedCached = ref(false)
const recentlyAddedUnavailable = ref(false)
const recentlyAddedLoading = ref(false)
const recentlyAddedGeneration = ref<unknown>(null)
const recentlyAddedPendingGeneration = ref<unknown>(null)

const unavailable = computed(() => props.projection.availability === 'unavailable')
const folders = computed(() => props.projection.folders)
const hasCollapsible = computed(() => folderTreeHasCollapsibleNodes(folders.value))
/** Bumped when collapse map changes so expand-all chrome re-reads helpers. */
const collapseTick = ref(0)
const expandLabel = computed(() => {
  void collapseTick.value
  return folderLibraryExpandToggleLabel(folders.value)
})
const expandCollapseAll = computed(() => {
  void collapseTick.value
  return !folderTreeAllCollapsed(folders.value)
})
const searchIconHtml = computed(() => {
  void props.projection.generation
  return tagSearchIconHtml()
})
const collectionClass = computed(() => {
  void props.projection.generation
  return workBrowseCollectionClass('prks-folder-library__grid')
})

/** Drop async completions from a dismissed instance (leave / remount). */
let surfaceAlive = true
let loadEpoch = 0

function surfaceStillOwned(): boolean {
  return surfaceAlive && !!rootEl.value?.isConnected
}

function syncDashboardCompatState(): void {
  if (!surfaceStillOwned()) return
  // Legacy helpers (toggle/glance) still read this shim. Folder Library is
  // main-only, so one mounted owner is the expected production case.
  window.__prksFolderDashboardState = {
    folders: folders.value,
    container: rootEl.value?.closest('[data-prks-vue-route-host]')?.parentElement || rootEl.value,
    activeTab: activeTab.value,
    filterQuery: folderFilter.value,
    recentlyAddedFilterQuery: filesFilter.value,
    recentlyAddedWorks: recentlyAddedWorks.value,
    recentlyAddedGeneration: recentlyAddedGeneration.value,
    recentlyAddedPendingGeneration: recentlyAddedPendingGeneration.value,
    recentlyAddedCached: recentlyAddedCached.value,
    recentlyAddedLoading: recentlyAddedLoading.value,
    vueOwned: true,
  }
}

function paintGlanceCatalog(): void {
  const host = glanceHost.value
  if (!host || unavailable.value) return
  const parts = folderLibraryCatalogGlanceParts(folders.value)
  host.innerHTML = pageSummaryHtml(parts)
}

async function scheduleGlanceExtras(): Promise<void> {
  const host = glanceHost.value
  if (!host || unavailable.value) return
  const token = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  host.dataset.glanceToken = token
  const catalogParts = folderLibraryCatalogGlanceParts(folders.value)
  const schedule = window.prksCollectFolderLibraryGlanceExtras
  let extras: Array<string | { text: string; href?: string }> = []
  try {
    if (typeof schedule === 'function') extras = (await schedule()) || []
  } catch {
    extras = []
  }
  if (!host.isConnected || host.dataset.glanceToken !== token) return
  host.innerHTML = pageSummaryHtml([...catalogParts, ...extras])
}

function paintTree(): void {
  const host = treeHost.value
  if (!host) return
  host.innerHTML = legacyFolderTreeHtml(folders.value, folderFilter.value)
  window.prksRefreshIcons?.(host)
  syncExpandToggle()
}

function syncExpandToggle(): void {
  collapseTick.value += 1
  const btn = expandToggleEl.value
  if (!btn) return
  if (!btn.querySelector('.ribbon-btn__icon')) {
    btn.innerHTML = folderLibraryExpandToggleInnerHtml()
    window.prksRefreshIcons?.(btn)
  }
  btn.classList.toggle('is-collapse-all', expandCollapseAll.value)
  btn.setAttribute('aria-label', expandLabel.value)
  btn.setAttribute('title', expandLabel.value)
}

function paintBrowseMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = workBrowseModeToggleHtml('prks-work-browse-mode-recently-added')
  window.prksBindWorkBrowseMode?.(rootEl.value)
}

function paintRecentlyAdded(): void {
  const pane = recentlyAddedPane.value
  if (!pane) return
  if (recentlyAddedUnavailable.value) {
    replaceFolderLibraryBrowseHtml(
      pane,
      '<p class="prks-inline-message">Recently added is not available offline.</p>',
      { initLazyThumbs: false },
    )
    return
  }
  if (!Array.isArray(recentlyAddedWorks.value)) {
    releaseFolderLibraryBrowseResources(pane)
    pane.innerHTML = ''
    return
  }
  const list = filterRecentlyAddedWorks(recentlyAddedWorks.value, filesFilter.value, folders.value)
  let html = ''
  if (list.length > 0) {
    const cached = recentlyAddedCached.value
    list.forEach((w) => {
      const dateLabel = recentlyAddedDateLabel(w.created_at)
      const subtitle = dateLabel ? `Added ${dateLabel}` : ''
      html += legacyWorkCardHtml(
        w,
        cached ? { subtitle, suppressThumbnail: true } : { subtitle },
      )
    })
  } else if (String(filesFilter.value || '').trim()) {
    html = '<p class="prks-inline-message">No files match your search.</p>'
  } else {
    html =
      '<div class="prks-folder-tree__empty-state">' +
      '<p class="prks-inline-message">No files in the library yet.</p>' +
      '<button type="button" class="prks-btn prks-btn--primary prks-folder-tree__create-btn" data-prks-create-work="1">New File</button>' +
      '</div>'
  }
  replaceFolderLibraryBrowseHtml(pane, html, {
    initLazyThumbs: list.length > 0 && !recentlyAddedCached.value,
  })
  window.prksRefreshIcons?.(pane)
}

async function loadRecentlyAdded(force = false): Promise<void> {
  if (unavailable.value || !surfaceStillOwned()) return
  const generation =
    typeof window.prksOfflineDomainGeneration === 'function'
      ? window.prksOfflineDomainGeneration('recently-added')
      : null
  const pendingGeneration =
    typeof window.prksPendingWorkMetadataGeneration === 'function'
      ? window.prksPendingWorkMetadataGeneration()
      : null
  const memoryStale = recentlyAddedGeneration.value !== generation
  const overlayStale = recentlyAddedPendingGeneration.value !== pendingGeneration
  const shouldForce = !!force || window.__prksRecentlyAddedDirty === true || memoryStale
  if (recentlyAddedLoading.value) return
  if (!shouldForce && Array.isArray(recentlyAddedWorks.value)) {
    if (overlayStale) recentlyAddedPendingGeneration.value = pendingGeneration
    paintRecentlyAdded()
    return
  }
  const epoch = ++loadEpoch
  recentlyAddedLoading.value = true
  syncDashboardCompatState()
  try {
    if (typeof window.prksRefreshPendingWorkMetadata === 'function') {
      await window.prksRefreshPendingWorkMetadata()
    }
    if (!surfaceStillOwned() || epoch !== loadEpoch) return
    const offlineRecentlyAdded =
      typeof window.prksOfflineRecentlyAddedFetch === 'function'
        ? await window.prksOfflineRecentlyAddedFetch()
        : null
    if (!surfaceStillOwned() || epoch !== loadEpoch) return
    const works =
      typeof window.prksResolveOfflineRecentlyAdded === 'function'
        ? window.prksResolveOfflineRecentlyAdded(offlineRecentlyAdded)
        : null
    if (!works) {
      recentlyAddedWorks.value = null
      recentlyAddedGeneration.value = null
      recentlyAddedPendingGeneration.value = null
      recentlyAddedUnavailable.value = true
      paintRecentlyAdded()
      syncDashboardCompatState()
      return
    }
    recentlyAddedUnavailable.value = false
    recentlyAddedWorks.value = works as RecentlyAddedWork[]
    recentlyAddedCached.value = !!(offlineRecentlyAdded && offlineRecentlyAdded.source === 'cache')
    recentlyAddedGeneration.value =
      typeof window.prksOfflineDomainGeneration === 'function'
        ? window.prksOfflineDomainGeneration('recently-added')
        : null
    recentlyAddedPendingGeneration.value =
      typeof window.prksPendingWorkMetadataGeneration === 'function'
        ? window.prksPendingWorkMetadataGeneration()
        : null
    window.__prksRecentlyAddedDirty = false
    paintRecentlyAdded()
    syncDashboardCompatState()
  } catch {
    if (!surfaceStillOwned() || epoch !== loadEpoch) return
    recentlyAddedWorks.value = null
    recentlyAddedGeneration.value = null
    recentlyAddedPendingGeneration.value = null
    recentlyAddedUnavailable.value = true
    paintRecentlyAdded()
    syncDashboardCompatState()
  } finally {
    if (epoch === loadEpoch) {
      recentlyAddedLoading.value = false
      if (surfaceStillOwned()) syncDashboardCompatState()
    }
  }
}

function switchTab(tab: FolderLibraryTab): void {
  const want = tab === 'recently-added' ? 'recently-added' : 'folders'
  if (activeTab.value === want) return
  // #170: leaving Recently added (or any tab switch that abandons card DOM)
  // must dismiss body-mounted preview and drop lazy observations.
  releaseFolderLibraryBrowseResources(rootEl.value)
  activeTab.value = want
  writeFolderLibraryTab(want)
  intents?.persistTab(want)
  syncDashboardCompatState()
  if (want === 'recently-added') {
    void loadRecentlyAdded(false).catch(() => {})
  }
}

/** Invalidate in-flight debounced filter writes (VueUse 14 has no .cancel). */
let folderFilterEpoch = 0
let filesFilterEpoch = 0

const persistFolderFilterDebounced = useDebounceFn((q: string, epoch: number) => {
  if (epoch !== folderFilterEpoch || !surfaceStillOwned()) return
  writeFolderLibraryFilter(q)
  intents?.persistFolderFilter(q)
  paintTree()
  syncDashboardCompatState()
}, 150)

const persistFilesFilterDebounced = useDebounceFn((q: string, epoch: number) => {
  if (epoch !== filesFilterEpoch || !surfaceStillOwned()) return
  writeFolderLibraryFilesFilter(q)
  intents?.persistFilesFilter(q)
  paintRecentlyAdded()
  syncDashboardCompatState()
}, 150)

function onFolderFilterInput(): void {
  const q = String(folderSearchInput.value?.value || '')
  folderFilter.value = q
  const epoch = ++folderFilterEpoch
  void persistFolderFilterDebounced(q, epoch)
}

function onFilesFilterInput(): void {
  const q = String(filesSearchInput.value?.value || '')
  filesFilter.value = q
  const epoch = ++filesFilterEpoch
  void persistFilesFilterDebounced(q, epoch)
}

function clearFolderFilter(): void {
  folderFilterEpoch += 1
  folderFilter.value = ''
  if (folderSearchInput.value) folderSearchInput.value.value = ''
  writeFolderLibraryFilter('')
  paintTree()
  syncDashboardCompatState()
  void nextTick(() => folderSearchInput.value?.focus())
}

function clearFilesFilter(): void {
  filesFilterEpoch += 1
  filesFilter.value = ''
  if (filesSearchInput.value) filesSearchInput.value.value = ''
  writeFolderLibraryFilesFilter('')
  paintRecentlyAdded()
  syncDashboardCompatState()
  void nextTick(() => filesSearchInput.value?.focus())
}

function onRootClick(event: Event): void {
  const target = event.target as HTMLElement | null
  if (!target) return
  const createFolder = target.closest('[data-prks-create-folder-query]')
  if (createFolder) {
    event.preventDefault()
    const q = createFolder.getAttribute('data-prks-create-folder-query') || folderFilter.value
    intents?.createFolder(q)
    return
  }
  if (target.closest('[data-prks-create-work]')) {
    event.preventDefault()
    intents?.createWork()
    return
  }
  const toggle = target.closest('.prks-folder-tree__toggle')
  if (toggle) {
    const row = toggle.closest('[data-folder-id]')
    const id = row?.getAttribute('data-folder-id')
    if (id) {
      event.preventDefault()
      intents?.toggleFolderNode(id)
      syncExpandToggle()
    }
  }
}

function onExpandAll(): void {
  intents?.toggleAllFolderNodes()
  syncExpandToggle()
}

async function refreshRecentlyAddedOverlay(): Promise<void> {
  if (!surfaceStillOwned() || !Array.isArray(recentlyAddedWorks.value)) return
  if (typeof window.prksRefreshPendingWorkMetadata !== 'function') return
  const epoch = loadEpoch
  await window.prksRefreshPendingWorkMetadata()
  if (!surfaceStillOwned() || epoch !== loadEpoch) return
  recentlyAddedPendingGeneration.value =
    typeof window.prksPendingWorkMetadataGeneration === 'function'
      ? window.prksPendingWorkMetadataGeneration()
      : null
  paintRecentlyAdded()
  syncDashboardCompatState()
}

let unsubscribeSync: (() => void) | null = null

onMounted(() => {
  surfaceAlive = true
  paintGlanceCatalog()
  void scheduleGlanceExtras()
  paintTree()
  paintBrowseMode()
  void nextTick(() => {
    syncExpandToggle()
  })
  syncDashboardCompatState()
  if (activeTab.value === 'recently-added') {
    void loadRecentlyAdded(false).catch(() => {})
  }
  window.prksRefreshIcons?.(rootEl.value)
  if (window.prksSync && typeof window.prksSync.subscribe === 'function') {
    unsubscribeSync = window.prksSync.subscribe(() => {
      void refreshRecentlyAddedOverlay().catch(() => {})
    })
  }
})

onBeforeUnmount(() => {
  surfaceAlive = false
  loadEpoch += 1
  folderFilterEpoch += 1
  filesFilterEpoch += 1
  releaseFolderLibraryBrowseResources(rootEl.value)
  if (typeof unsubscribeSync === 'function') {
    try {
      unsubscribeSync()
    } catch {
      /* ignore */
    }
  }
  const st = window.__prksFolderDashboardState
  if (st && st.vueOwned && st.container && rootEl.value && st.container.contains(rootEl.value)) {
    window.__prksFolderDashboardState = null
  }
})

// Same-route projection refresh: keep local tab/filter/recently-added runtime;
// only re-paint tree/glance from the new folders list.
watch(
  () => [props.projection.generation, props.projection.folders] as const,
  async () => {
    if (unavailable.value) return
    await nextTick()
    paintGlanceCatalog()
    void scheduleGlanceExtras()
    paintTree()
    syncDashboardCompatState()
    if (activeTab.value === 'recently-added' && Array.isArray(recentlyAddedWorks.value)) {
      paintRecentlyAdded()
    }
  },
)

useEventListener(rootEl, 'click', onRootClick)
</script>

<template>
  <div
    v-if="unavailable"
    ref="rootEl"
    class="prks-folder-library"
    data-prks-folder-library-view
    data-prks-role="folder-library"
  >
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">Folders not available offline</h2>
    </div>
    <p class="prks-inline-message" data-prks-role="offline-unavailable">
      This list has not been cached on this device.
    </p>
  </div>
  <div
    v-else
    ref="rootEl"
    class="prks-folder-library"
    data-prks-folder-library-view
    data-prks-role="folder-library"
  >
    <div class="prks-page-header page-header prks-folder-library__header">
      <h2 class="prks-page-title">Folder Library</h2>
      <div ref="glanceHost" data-prks-role="folder-library-glance-host"></div>
    </div>
    <div class="tabs prks-folder-library__tabs" role="tablist" aria-label="Folder library views">
      <button
        type="button"
        class="tab-btn prks-tab prks-folder-library__tab-btn"
        :class="{ active: activeTab === 'folders', 'is-active': activeTab === 'folders' }"
        role="tab"
        data-tab="folders"
        :aria-selected="activeTab === 'folders' ? 'true' : 'false'"
        @click="switchTab('folders')"
      >
        Folders
      </button>
      <button
        type="button"
        class="tab-btn prks-tab prks-folder-library__tab-btn"
        :class="{ active: activeTab === 'recently-added', 'is-active': activeTab === 'recently-added' }"
        role="tab"
        data-tab="recently-added"
        :aria-selected="activeTab === 'recently-added' ? 'true' : 'false'"
        @click="switchTab('recently-added')"
      >
        Recently added
      </button>
    </div>
    <div
      class="prks-folder-library__folders-toolbar"
      :class="{ 'is-hidden': activeTab !== 'folders' }"
    >
      <div class="tag-add-shell tag-add-shell--flush prks-folder-library__search">
        <div class="tag-add-shell__field">
          <span aria-hidden="true" v-html="searchIconHtml"></span>
          <input
            id="prks-folder-library-search"
            ref="folderSearchInput"
            type="text"
            class="tag-add-shell__input"
            placeholder="Search folders…"
            :value="folderFilter"
            maxlength="300"
            autocomplete="off"
            aria-label="Filter folders"
            @input="onFolderFilterInput"
          >
          <button
            type="button"
            class="tag-add-shell__clear"
            id="prks-folder-library-search-clear"
            aria-label="Clear search"
            title="Clear search"
            :hidden="!String(folderFilter || '').trim()"
            :disabled="!String(folderFilter || '').trim()"
            @click="clearFolderFilter"
          >
            &times;
          </button>
        </div>
      </div>
      <div v-if="hasCollapsible" class="prks-folder-library__toolbar-actions">
        <button
          ref="expandToggleEl"
          type="button"
          id="prks-folder-library-expand-toggle"
          class="prks-btn prks-btn--secondary prks-folder-library__toolbar-btn"
          :class="{ 'is-collapse-all': expandCollapseAll }"
          :aria-label="expandLabel"
          :title="expandLabel"
          @click="onExpandAll"
        ></button>
      </div>
    </div>
    <div
      class="prks-folder-library__recently-added-toolbar"
      :class="{ 'is-hidden': activeTab !== 'recently-added' }"
    >
      <div class="tag-add-shell tag-add-shell--flush prks-folder-library__search">
        <div class="tag-add-shell__field">
          <span aria-hidden="true" v-html="searchIconHtml"></span>
          <input
            id="prks-folder-library-files-search"
            ref="filesSearchInput"
            type="text"
            class="tag-add-shell__input"
            placeholder="Search files…"
            :value="filesFilter"
            maxlength="300"
            autocomplete="off"
            aria-label="Filter recently added files"
            @input="onFilesFilterInput"
          >
          <button
            type="button"
            class="tag-add-shell__clear"
            id="prks-folder-library-files-search-clear"
            aria-label="Clear search"
            title="Clear search"
            :hidden="!String(filesFilter || '').trim()"
            :disabled="!String(filesFilter || '').trim()"
            @click="clearFilesFilter"
          >
            &times;
          </button>
        </div>
      </div>
      <div ref="modeHost" style="display: contents"></div>
    </div>
    <div class="prks-folder-library__body">
      <div
        class="prks-folder-library__pane"
        :class="{ 'is-hidden': activeTab !== 'folders' }"
        data-pane="folders"
        role="tabpanel"
        :aria-hidden="activeTab === 'folders' ? 'false' : 'true'"
      >
        <div class="prks-folder-library__scroll" data-prks-folder-tree-host ref="treeHost"></div>
      </div>
      <div
        class="prks-folder-library__pane prks-folder-library__pane--added"
        :class="{ 'is-hidden': activeTab !== 'recently-added' }"
        data-pane="recently-added"
        role="tabpanel"
        :aria-hidden="activeTab === 'recently-added' ? 'false' : 'true'"
      >
        <div class="prks-folder-library__scroll prks-folder-library__scroll--added">
          <div
            id="prks-folder-library-recently-added"
            ref="recentlyAddedPane"
            :class="collectionClass"
          ></div>
        </div>
      </div>
    </div>
  </div>
</template>
