<script setup lang="ts">
import { useDebounceFn } from '@vueuse/core'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
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
/** Debounced queries drive expensive tree / card paints (#233 useDebounceFn). */
const treeFilterQuery = ref(folderFilter.value)
const filesFilterQuery = ref(filesFilter.value)
const glanceHost = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)
const folderSearchIcon = ref<HTMLElement | null>(null)
const filesSearchIcon = ref<HTMLElement | null>(null)
const expandToggleInner = ref<HTMLElement | null>(null)
const recentlyAddedPane = ref<InstanceType<typeof RecentlyAddedPane> | null>(null)
/** Bumped after expand toggles so toolbar chrome stays authoritative. */
const collapseEpoch = ref(0)
/** Bumped when pending work-metadata overlay changes while Recently Added is mounted. */
const overlayRevision = ref(0)

const recentlyAddedWorks = ref<RecentlyAddedWork[] | null>(null)
const recentlyAddedCached = ref(false)
const recentlyAddedUnavailable = ref(false)
const recentlyAddedLoading = ref(false)
const recentlyAddedGeneration = ref<unknown>(null)
const recentlyAddedPendingGeneration = ref<unknown>(null)
/** Drop async completions after leave / remount / superseded loads. */
let surfaceAlive = true
let loadEpoch = 0
/** Invalidate in-flight debounced filter writes (VueUse has no .cancel here). */
let folderFilterEpoch = 0
let filesFilterEpoch = 0

const unavailable = computed(() => props.projection.availability === 'unavailable')
const folders = computed(() => props.projection.folders)

function surfaceStillOwned(): boolean {
  return surfaceAlive && !!rootEl.value?.isConnected
}

const hasCollapsible = computed(() => {
  void props.projection.generation
  void collapseEpoch.value
  const fn = window.prksFolderTreeHasCollapsibleNodes
  return typeof fn === 'function' ? fn(folders.value) : false
})

const expandAllCollapsed = computed(() => {
  void props.projection.generation
  void collapseEpoch.value
  const fn = window.prksFolderTreeAllCollapsed
  return typeof fn === 'function' ? fn(folders.value) : false
})

const expandToggleLabel = computed(() => {
  void collapseEpoch.value
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
let overlayDispose: (() => void) | null = null

function treeHostEl(): HTMLElement | null {
  return rootEl.value?.querySelector('[data-prks-folder-tree-host]') as HTMLElement | null
}

/**
 * Publish a thin bridge for legacy helpers that still read dashboard chrome
 * (offline banner binding, expand-toggle patch). Mark vueOwned so the legacy
 * metadata-sync subscriber does not replace Vue-owned Recently Added DOM.
 * Multi-owner: register per contentRoot so Main/Secondary do not clobber each other.
 */
function syncLegacyDashboardState(): void {
  if (!surfaceStillOwned()) return
  const content = props.contentRoot
  if (!content) return
  const state = {
    folders: [...folders.value],
    container: content,
    activeTab: activeTab.value,
    filterQuery: treeFilterQuery.value,
    recentlyAddedFilterQuery: filesFilterQuery.value,
    // Rows stay on the bridge for glance extras ("N recently added").
    // vueOwned keeps legacy metadata-sync / DOM paint from owning the pane.
    recentlyAddedWorks: recentlyAddedWorks.value ? [...recentlyAddedWorks.value] : null,
    recentlyAddedGeneration: recentlyAddedGeneration.value,
    recentlyAddedPendingGeneration: recentlyAddedPendingGeneration.value,
    recentlyAddedCached: recentlyAddedCached.value,
    recentlyAddedLoading: recentlyAddedLoading.value,
    vueOwned: true,
    // Awaitable entry for `prksSwitchFolderLibraryTab` / E2E helpers.
    switchTab: (tab: string) =>
      setActiveTab(tab === 'recently-added' ? 'recently-added' : 'folders'),
  }
  if (typeof window.prksPublishFolderDashboardState === 'function') {
    window.prksPublishFolderDashboardState(state)
  } else {
    window.__prksFolderDashboardState = state
  }
}

function scheduleOwnerGlance(): void {
  intents?.scheduleGlance(rootEl.value, {
    folders: folders.value,
    recentlyAddedWorks: recentlyAddedWorks.value,
  })
}

function paintCatalogGlance(): void {
  const host = glanceHost.value
  if (!host || unavailable.value) return
  const fn = window.prksPaintFolderLibraryGlance
  if (typeof fn === 'function') fn(host, catalogParts.value)
}

const applyFolderFilter = useDebounceFn((query: string, epoch: number) => {
  if (epoch !== folderFilterEpoch || !surfaceStillOwned()) return
  treeFilterQuery.value = query
  persistFolderFilter(query)
  syncLegacyDashboardState()
}, 150)

const applyFilesFilter = useDebounceFn((query: string, epoch: number) => {
  if (epoch !== filesFilterEpoch || !surfaceStillOwned()) return
  filesFilterQuery.value = query
  persistRecentlyAddedFilter(query)
  syncLegacyDashboardState()
}, 150)

watch(folderFilter, (q) => {
  const epoch = ++folderFilterEpoch
  applyFolderFilter(q, epoch)
})

watch(filesFilter, (q) => {
  const epoch = ++filesFilterEpoch
  applyFilesFilter(q, epoch)
})

function releaseRecentlyAddedResources(): void {
  recentlyAddedPane.value?.releaseThumbs?.()
  const root = rootEl.value
  if (root) releaseWorkThumbResources(root.querySelector('#prks-folder-library-recently-added'))
}

/**
 * Switch tabs. Re-entering Recently Added always awaits load — same as legacy
 * `prksSwitchFolderLibraryTab`, which re-ran the fetch even when already on
 * that tab. A same-tab no-op left the loading placeholder as the only child
 * and made offline metadata E2E helpers race the first paint.
 */
async function setActiveTab(tab: FolderLibraryTab): Promise<void> {
  if (tab !== activeTab.value) {
    if (activeTab.value === 'recently-added') {
      releaseRecentlyAddedResources()
    }
    activeTab.value = tab
    intents?.switchTab(tab)
    syncLegacyDashboardState()
  }
  if (tab === 'recently-added') {
    await loadRecentlyAdded(false)
  }
}

async function loadRecentlyAdded(force: boolean): Promise<void> {
  if (!surfaceStillOwned()) return
  const epoch = ++loadEpoch
  const routeGen = props.projection.generation
  recentlyAddedLoading.value = true
  syncLegacyDashboardState()
  try {
    const result = await intents?.loadRecentlyAdded(force, {
      works: recentlyAddedWorks.value,
      generation: recentlyAddedGeneration.value,
      pendingGeneration: recentlyAddedPendingGeneration.value,
      offlineCached: recentlyAddedCached.value,
    })
    // Drop obsolete responses after overlapping loads / retain refresh.
    if (!surfaceStillOwned() || epoch !== loadEpoch || props.projection.generation !== routeGen) {
      return
    }
    if (!result) {
      recentlyAddedWorks.value = null
      recentlyAddedUnavailable.value = true
      recentlyAddedGeneration.value = null
      recentlyAddedPendingGeneration.value = null
      syncLegacyDashboardState()
      return
    }
    const prevPending = recentlyAddedPendingGeneration.value
    recentlyAddedWorks.value = result.works
    recentlyAddedCached.value = result.offlineCached
    recentlyAddedUnavailable.value = result.unavailable
    recentlyAddedGeneration.value = result.generation
    recentlyAddedPendingGeneration.value = result.pendingGeneration
    if (!result.reused || result.pendingGeneration !== prevPending) {
      overlayRevision.value += 1
    }
    syncLegacyDashboardState()
    // Flush child RecentlyAddedPane paint (flush:post) before callers resume.
    await nextTick()
    if (!surfaceStillOwned() || epoch !== loadEpoch) return
    // #170: always re-init after paint (including cached) so prune runs.
    if (activeTab.value === 'recently-added') {
      const pane = rootEl.value?.querySelector('#prks-folder-library-recently-added')
      initLazyWorkThumbs(pane)
    }
  } catch {
    if (!surfaceStillOwned() || epoch !== loadEpoch) return
    recentlyAddedWorks.value = null
    recentlyAddedUnavailable.value = true
    recentlyAddedGeneration.value = null
    recentlyAddedPendingGeneration.value = null
    syncLegacyDashboardState()
  } finally {
    if (epoch === loadEpoch) {
      recentlyAddedLoading.value = false
      if (surfaceStillOwned()) syncLegacyDashboardState()
    }
  }
}

function onExpandAll(): void {
  intents?.toggleExpandAll(treeHostEl(), folders.value)
  collapseEpoch.value += 1
}

function onTreeCollapsedChanged(): void {
  collapseEpoch.value += 1
}

function onFolderSearchClear(): void {
  folderFilterEpoch += 1
  folderFilter.value = ''
  treeFilterQuery.value = ''
  persistFolderFilter('')
  syncLegacyDashboardState()
}

function onFilesSearchClear(): void {
  filesFilterEpoch += 1
  filesFilter.value = ''
  filesFilterQuery.value = ''
  persistRecentlyAddedFilter('')
  syncLegacyDashboardState()
}

function paintSearchIcons(): void {
  const html =
    typeof window.prksTagSearchIconHtml === 'function' ? window.prksTagSearchIconHtml() : ''
  if (folderSearchIcon.value) folderSearchIcon.value.innerHTML = html
  if (filesSearchIcon.value) filesSearchIcon.value.innerHTML = html
}

function paintExpandToggle(): void {
  const host = expandToggleInner.value
  if (!host) return
  const fn = window.prksFolderLibraryExpandToggleInnerHtml
  host.innerHTML = typeof fn === 'function' ? fn() : '<span class="ribbon-btn__icon" aria-hidden="true">▾</span>'
  window.prksRefreshIcons?.(host)
}

function paintBrowseMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML =
    window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-recently-added') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
}

/**
 * Brand-home clears session storage then retain-refreshes with
 * `__prksFolderLibraryBrandHomeReset`. Adopt that intentional clear without
 * clobbering an in-progress typed filter on ordinary projection refreshes.
 */
function syncFiltersFromBrandHomeReset(): void {
  if (!window.__prksFolderLibraryBrandHomeReset) return
  window.__prksFolderLibraryBrandHomeReset = false
  folderFilterEpoch += 1
  folderFilter.value = ''
  treeFilterQuery.value = ''
  persistFolderFilter('')
}

onMounted(() => {
  surfaceAlive = true
  syncLegacyDashboardState()
  paintCatalogGlance()
  paintSearchIcons()
  paintExpandToggle()
  scheduleOwnerGlance()
  offlineDispose = intents?.bindFolderOfflineState(props.contentRoot ?? null) ?? null
  overlayDispose =
    intents?.subscribeMetadataOverlay(() => {
      if (!surfaceStillOwned()) return
      overlayRevision.value += 1
      recentlyAddedPendingGeneration.value =
        typeof window.prksPendingWorkMetadataGeneration === 'function'
          ? window.prksPendingWorkMetadataGeneration()
          : recentlyAddedPendingGeneration.value
      syncLegacyDashboardState()
    }) ?? null
  paintBrowseMode()
  if (activeTab.value === 'recently-added') {
    void loadRecentlyAdded(false).catch(() => {})
  }
})

watch(
  () => props.projection.generation,
  async () => {
    // Brand-home may clear storage + legacy filter while the Vue surface is
    // retained — adopt that reset without wiping ordinary typed filters.
    syncFiltersFromBrandHomeReset()
    syncLegacyDashboardState()
    await nextTick()
    if (!surfaceStillOwned()) return
    paintCatalogGlance()
    paintSearchIcons()
    paintExpandToggle()
    scheduleOwnerGlance()
    paintBrowseMode()
    if (activeTab.value === 'recently-added') {
      void loadRecentlyAdded(false).catch(() => {})
    }
  },
)

watch(hasCollapsible, async () => {
  await nextTick()
  if (surfaceStillOwned()) paintExpandToggle()
})

watch(collapseEpoch, async () => {
  await nextTick()
  if (surfaceStillOwned()) paintExpandToggle()
})

onBeforeUnmount(() => {
  surfaceAlive = false
  loadEpoch += 1
  folderFilterEpoch += 1
  filesFilterEpoch += 1
  releaseRecentlyAddedResources()
  releaseWorkThumbResources(rootEl.value)
  offlineDispose?.()
  offlineDispose = null
  overlayDispose?.()
  overlayDispose = null
  const content = props.contentRoot
  if (content && typeof window.prksUnpublishFolderDashboardState === 'function') {
    window.prksUnpublishFolderDashboardState(content)
  } else {
    const st = window.__prksFolderDashboardState
    if (
      st &&
      st.vueOwned &&
      st.container &&
      rootEl.value &&
      typeof (st.container as ParentNode).contains === 'function' &&
      (st.container as ParentNode).contains(rootEl.value)
    ) {
      window.__prksFolderDashboardState = undefined
    }
  }
})
</script>

<template>
  <div ref="rootEl" class="prks-folder-library" data-prks-folder-library-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Folders not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This list has not been cached on this device.
      </PrksInlineMessage>
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
            <span ref="folderSearchIcon" aria-hidden="true"></span>
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
            <span ref="expandToggleInner"></span>
          </button>
        </div>
      </div>
      <div class="prks-folder-library__recently-added-toolbar" :class="{ 'is-hidden': foldersActive }">
        <div class="tag-add-shell tag-add-shell--flush prks-folder-library__search">
          <div class="tag-add-shell__field">
            <span ref="filesSearchIcon" aria-hidden="true"></span>
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
        <div ref="modeHost" data-prks-folder-library-mode-host style="display: contents"></div>
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
            :filter-query="treeFilterQuery"
            :generation="projection.generation"
            @collapsed-changed="onTreeCollapsedChanged"
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
            :filter-query="filesFilterQuery"
            :works="recentlyAddedWorks"
            :offline-cached="recentlyAddedCached"
            :unavailable="recentlyAddedUnavailable"
            :loading="recentlyAddedLoading"
            :generation="projection.generation"
            :overlay-revision="overlayRevision"
          />
        </div>
      </div>
    </template>
  </div>
</template>
