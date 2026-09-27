<script setup lang="ts">
import { computed, inject, nextTick, onMounted, ref, watch } from 'vue'
import { positionIntentsKey } from './intents'
import { filterPositionIndexItems, normalizePositionSearchQuery } from './match'
import PositionRow from './PositionRow.vue'
import type { PositionIndexProjection } from './projection'

const MUTATION_ROLE = 'position-mutation-control'

const props = defineProps<{
  projection: PositionIndexProjection
}>()

const intents = inject(positionIntentsKey)

const searchQuery = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const scopeHost = ref<HTMLElement | null>(null)
const titleIconHost = ref<HTMLElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)

const unavailable = computed(() => props.projection.availability === 'unavailable')
const items = computed(() => props.projection.items)
const filtered = computed(() => filterPositionIndexItems(items.value, searchQuery.value))
const normalizedQuery = computed(() => normalizePositionSearchQuery(searchQuery.value))
const showToolbar = computed(() => !unavailable.value && items.value.length > 0)
const showEmptyData = computed(
  () => !unavailable.value && !filtered.value.length && !normalizedQuery.value,
)
const showSearchEmpty = computed(
  () => !unavailable.value && !filtered.value.length && !!normalizedQuery.value,
)

const rowIconHtml = computed(() => {
  void props.projection.generation
  return typeof window.prksIcon === 'function' ? window.prksIcon('flag', { size: 'sm' }) : ''
})

function paintTitleIcon(): void {
  const host = titleIconHost.value
  if (!host) return
  host.innerHTML =
    typeof window.prksPageHeaderIconHtml === 'function' ? window.prksPageHeaderIconHtml('flag') : ''
}

function paintScope(): void {
  const host = scopeHost.value
  if (!host || unavailable.value) return
  if (typeof window.prksPaintScopeHost === 'function') {
    window.prksPaintScopeHost(host, {
      shown: filtered.value.length,
      total: items.value.length,
      filter: normalizedQuery.value,
      label: 'Positions',
    })
  }
}

function refreshIcons(): void {
  const root = rootEl.value
  if (root && typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(root)
}

function clearSearch(): void {
  searchQuery.value = ''
  void nextTick(() => {
    searchInput.value?.focus()
  })
}

function onCreate(): void {
  void intents?.create()
}

onMounted(() => {
  paintTitleIcon()
  paintScope()
  refreshIcons()
})

watch(
  () =>
    [props.projection.generation, filtered.value.length, normalizedQuery.value, items.value.length] as const,
  async () => {
    await nextTick()
    paintScope()
    refreshIcons()
  },
)
</script>

<template>
  <div ref="rootEl" data-prks-positions-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Positions not available offline</h2>
      </div>
      <p class="prks-inline-message" data-prks-role="offline-unavailable">
        This list has not been cached on this device.
      </p>
    </template>
    <template v-else>
      <div class="prks-page-header page-header">
        <div class="page-header__title-row">
          <h2 class="prks-page-title">
            <span ref="titleIconHost" aria-hidden="true"></span>
            Positions
          </h2>
          <div class="page-header__actions">
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-position-new"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate"
            >
              New Position
            </button>
          </div>
        </div>
        <div ref="scopeHost" data-prks-role="index-scope-host"></div>
      </div>
      <div v-if="showToolbar" class="prks-toolbar prks-research-index__toolbar">
        <input
          id="prks-position-search"
          ref="searchInput"
          v-model="searchQuery"
          type="search"
          class="prks-input"
          autocomplete="off"
          placeholder="Search positions…"
          aria-label="Search positions…"
        >
      </div>
      <div id="prks-position-rows" class="list-view prks-research-index">
        <template v-if="filtered.length">
          <PositionRow
            v-for="item in filtered"
            :key="item.id"
            :item="item"
            :icon-html="rowIconHtml"
          />
        </template>
        <div v-else-if="showEmptyData" class="prks-research-index__empty">
          <p class="meta-row">No Positions yet.</p>
          <p>
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-position-new-empty"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate"
            >
              New Position
            </button>
          </p>
        </div>
        <div v-else-if="showSearchEmpty" class="prks-research-index__empty">
          <p class="meta-row">No Positions match “{{ normalizedQuery }}”.</p>
          <p>
            <button
              type="button"
              class="prks-btn prks-btn--ghost prks-btn--sm"
              data-research-search-clear
              @click="clearSearch"
            >
              Clear search
            </button>
          </p>
        </div>
      </div>
    </template>
  </div>
</template>
