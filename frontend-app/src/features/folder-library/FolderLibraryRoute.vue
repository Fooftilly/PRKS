<script setup lang="ts">
import { useDebounceFn } from '@vueuse/core'
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import FolderTree from './FolderTree.vue'
import RecentlyAddedPane from './RecentlyAddedPane.vue'
import {
  folderLibraryIntentsKey,
  persistFolderFilter,
  persistRecentlyAddedFilter,
  readFolderFilterFromStorage,
  readFolderLibraryTabFromStorage,
  readRecentlyAddedFilterFromStorage,
} from './intents'
import type { FolderLibraryProjection } from './projection'
import type { FolderLibraryTab, RecentlyAddedWork } from './types'
import { initLazyWorkThumbs, releaseWorkThumbResources } from './work-thumb-lifecycle'

const props = defineProps<{
  projection: FolderLibraryProjection
  contentRoot?: HTMLElement | null
}>()

const intents = inject(folderLibraryIntentsKey)

const activeTab = ref<FolderLibraryTab>(readFolderLibraryTabFromStorage())
const folderFilter = ref(readFolderFilterFromStorage())
const filesFilter = ref(readRecentlyAddedFilterFromStorage())
const glanceHost = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)
const recentlyAddedPane = ref<InstanceType<typeof RecentlyAddedPane> | null>(null)

const recentlyAddedWorks = ref<RecentlyAddedWork[] | null>(null)
const recentlyAddedCached = ref(false)
const recentlyAddedUnavailable = ref(false)
const recentlyAddedLoading = ref(false)

const unavailable = computed(() => props.projection.availability === 'unavailable')
const folders = computed(() => props.projection.folders)

const hasCollapsible = computed(() => {
  void props.projection.generation
  const fn = window.prksFolderTreeHasCollapsibleNodes
  return typeof fn === 'function' ? fn(folders.value) : false
})

const expandAllCollapsed = computed(() => {
  void props.projection.generation
  const fn = window.prksFolderTreeAllCollapsed
  return typeof fn === 'function' ? fn(folders.value) : false
})

const expandToggleLabel = computed(() => {
  const fn = window.prksFolderLibraryExpandToggleLabel
  return typeof fn === 'function' ? fn(folders.value) : 'Expand all'
})

const foldersActive = computed(() => activeTab.value !== 'recently-added')

const catalogParts = computed(() => {
  void props.projection.generation
  const fn = window.prksFolderLibraryCatalogGlanceParts
  return typeof fn === 'function' ? fn(folders.value) : []
})

let offlineDispose: (() => void) | null = null

function syncLegacyDashboardState(): void {
  const content = props.contentRoot
  if (!content) return
  window.__prksFolderDashboardState = {
    folders: [...folders.value],
    container: content,
    activeTab: activeTab.value,
    filterQuery: folderFilter.value,
    recentlyAddedFilterQuery: filesFilter.value,
    recentlyAddedWorks: recentlyAddedWorks.value,
    recentlyAddedGeneration: null,
    recentlyAddedPendingGeneration: null,
    recentlyAddedCached: recentlyAddedCached.value,
    recentlyAddedLoading: recentlyAddedLoading.value,
  }
}

function paintCatalogGlance(): void {
  const host = glanceHost.value
  if (!host || unavailable.value) return
  const fn = window.prksPaintFolderLibraryGlance
  if (typeof fn === 'function') fn(host, catalogParts.value)
}

const applyFolderFilter = useDebounceFn((query: string) => {
  persistFolderFilter(query)
  syncLegacyDashboardState()
}, 150)

const applyFilesFilter = useDebounceFn((query: string) => {
  persistRecentlyAddedFilter(query)
  syncLegacyDashboardState()
}, 150)

watch(folderFilter, (q) => {
  applyFolderFilter(q)
})

watch(filesFilter, (q) => {
  applyFilesFilter(q)
})

function releaseRecentlyAddedResources(): void {
  recentlyAddedPane.value?.releaseThumbs?.()
  const root = rootEl.value
  if (root) releaseWorkThumbResources(root.querySelector('#prks-folder-library-recently-added'))
}

async function setActiveTab(tab: FolderLibraryTab): Promise<void> {
  if (tab === activeTab.value) return
  if (activeTab.value === 'recently-added') {
    releaseRecentlyAddedResources()
  }
  activeTab.value = tab
  intents?.switchTab(tab)
  syncLegacyDashboardState()
  if (tab === 'recently-added') {
    await loadRecentlyAdded(false)
  }
}

async function loadRecentlyAdded(force: boolean): Promise<void> {
  recentlyAddedLoading.value = true
  const result = await intents?.loadRecentlyAdded(force)
  recentlyAddedLoading.value = false
  if (!result) return
  recentlyAddedWorks.value = result.works
  recentlyAddedCached.value = result.offlineCached
  recentlyAddedUnavailable.value = result.unavailable
  syncLegacyDashboardState()
  await nextTick()
  if (activeTab.value === 'recently-added' && !result.offlineCached) {
    const pane = rootEl.value?.querySelector('#prks-folder-library-recently-added')
    initLazyWorkThumbs(pane)
  }
}

function onExpandAll(): void {
  intents?.toggleExpandAll()
}

function onFolderSearchClear(): void {
  folderFilter.value = ''
}

function onFilesSearchClear(): void {
  filesFilter.value = ''
}

function paintBrowseMode(): void {
  const root = rootEl.value
  if (!root) return
  window.prksBindWorkBrowseMode?.(root)
}

onMounted(() => {
  syncLegacyDashboardState()
  paintCatalogGlance()
  intents?.scheduleGlance(rootEl.value)
  offlineDispose = intents?.bindFolderOfflineState(props.contentRoot ?? null) ?? null
  paintBrowseMode()
  if (activeTab.value === 'recently-added') {
    void loadRecentlyAdded(false)
  }
})

watch(
  () => props.projection.generation,
  async () => {
    syncLegacyDashboardState()
    await nextTick()
    paintCatalogGlance()
    intents?.scheduleGlance(rootEl.value)
    paintBrowseMode()
  },
)

onBeforeUnmount(() => {
  releaseRecentlyAddedResources()
  releaseWorkThumbResources(rootEl.value)
  offlineDispose?.()
  offlineDispose = null
})
</script>

<template>
  <div ref="rootEl" class="prks-folder-library" data-prks-folder-library-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Folders not available offline</h2>
      </div>
      <p class="prks-inline-message" data-prks-role="offline-unavailable">
        This list has not been cached on this device.
      </p>
    </template>
    <template v-else>
      <div class="prks-page-header page-header prks-folder-library__header">
        <h2 class="prks-page-title">Folder Library</h2>
        <div ref="glanceHost" data-prks-role="folder-library-glance-host"></div>
      </div>
      <div class="tabs prks-folder-library__tabs" role="tablist" aria-label="Folder library views">
        <button
          type="button"
          class="tab-btn prks-tab prks-folder-library__tab-btn"
          :class="{ active: foldersActive, 'is-active': foldersActive }"
          role="tab"
          data-tab="folders"
          :aria-selected="foldersActive ? 'true' : 'false'"
          @click="setActiveTab('folders')"
        >
          Folders
        </button>
        <button
          type="button"
          class="tab-btn prks-tab prks-folder-library__tab-btn"
          :class="{ active: !foldersActive, 'is-active': !foldersActive }"
          role="tab"
          data-tab="recently-added"
          :aria-selected="!foldersActive ? 'true' : 'false'"
          @click="setActiveTab('recently-added')"
        >
          Recently added
        </button>
      </div>
      <div class="prks-folder-library__folders-toolbar" :class="{ 'is-hidden': !foldersActive }">
        <div class="tag-add-shell tag-add-shell--flush prks-folder-library__search">
          <div class="tag-add-shell__field">
            <input
              id="prks-folder-library-search"
              v-model="folderFilter"
              type="text"
              class="tag-add-shell__input"
              placeholder="Search folders…"
              maxlength="300"
              autocomplete="off"
              aria-label="Filter folders"
            >
            <button
              v-show="folderFilter.trim()"
              type="button"
              class="tag-add-shell__clear"
              id="prks-folder-library-search-clear"
              aria-label="Clear search"
              title="Clear search"
              @click="onFolderSearchClear"
            >
              &times;
            </button>
          </div>
        </div>
        <div v-if="hasCollapsible" class="prks-folder-library__toolbar-actions">
          <button
            type="button"
            id="prks-folder-library-expand-toggle"
            class="prks-btn prks-btn--secondary prks-folder-library__toolbar-btn"
            :class="{ 'is-collapse-all': !expandAllCollapsed }"
            :aria-label="expandToggleLabel"
            :title="expandToggleLabel"
            @click="onExpandAll"
          >
            <span class="ribbon-btn__icon" aria-hidden="true">▾</span>
          </button>
        </div>
      </div>
      <div class="prks-folder-library__recently-added-toolbar" :class="{ 'is-hidden': foldersActive }">
        <div class="tag-add-shell tag-add-shell--flush prks-folder-library__search">
          <div class="tag-add-shell__field">
            <input
              id="prks-folder-library-files-search"
              v-model="filesFilter"
              type="text"
              class="tag-add-shell__input"
              placeholder="Search files…"
              maxlength="300"
              autocomplete="off"
              aria-label="Filter recently added files"
            >
            <button
              v-show="filesFilter.trim()"
              type="button"
              class="tag-add-shell__clear"
              id="prks-folder-library-files-search-clear"
              aria-label="Clear search"
              title="Clear search"
              @click="onFilesSearchClear"
            >
              &times;
            </button>
          </div>
        </div>
      </div>
      <div class="prks-folder-library__body">
        <div
          class="prks-folder-library__pane"
          :class="{ 'is-hidden': !foldersActive }"
          data-pane="folders"
          role="tabpanel"
          :aria-hidden="foldersActive ? 'false' : 'true'"
        >
          <FolderTree
            :folders="folders"
            :filter-query="folderFilter"
            :generation="projection.generation"
          />
        </div>
        <div
          class="prks-folder-library__pane prks-folder-library__pane--added"
          :class="{ 'is-hidden': foldersActive }"
          data-pane="recently-added"
          role="tabpanel"
          :aria-hidden="foldersActive ? 'true' : 'false'"
        >
          <RecentlyAddedPane
            ref="recentlyAddedPane"
            :folders="folders"
            :filter-query="filesFilter"
            :works="recentlyAddedWorks"
            :offline-cached="recentlyAddedCached"
            :unavailable="recentlyAddedUnavailable"
            :loading="recentlyAddedLoading"
            :generation="projection.generation"
          />
        </div>
      </div>
    </template>
  </div>
</template>
