<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { useResearchIndexList } from '../../research-index/useResearchIndexList'
import { argumentIntentsKey } from './intents'
import { argumentKindUi, filterArgumentIndexItems, normalizeArgumentSearchQuery } from './match'
import ArgumentRow from './ArgumentRow.vue'
import type { ArgumentIndexProjection } from './projection'
import type { ArgumentKind } from './types'

const MUTATION_ROLE = 'argument-mutation-control'

const props = defineProps<{
  projection: ArgumentIndexProjection
}>()

const intents = inject(argumentIntentsKey)
const unavailable = computed(() => props.projection.availability === 'unavailable')
const kindUi = computed(() => argumentKindUi(props.projection.kind))
const rootEl = ref<HTMLElement | null>(null)
const titleIconHost = ref<HTMLElement | null>(null)
const scopeHost = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const {
  searchQuery,
  filtered,
  normalizedQuery,
  showToolbar,
  showEmptyData,
  showSearchEmpty,
  rowIconHtml,
  clearSearch,
} = useResearchIndexList({
  icon: 'messages-square',
  scopeLabel: computed(() => kindUi.value.plural),
  filter: filterArgumentIndexItems,
  normalizeQuery: normalizeArgumentSearchQuery,
  items: computed(() => props.projection.items),
  unavailable,
  generation: computed(() => props.projection.generation),
  rootEl,
  titleIconHost,
  scopeHost,
  searchInput,
})

const scopeLabel = computed(() => kindUi.value.plural)

function tabSelected(kind: 'all' | ArgumentKind): boolean {
  return props.projection.kind === kind
}

function onFilter(kind: 'all' | ArgumentKind): void {
  intents?.filterKind(kind)
}

function onCreate(kind: ArgumentKind): void {
  void intents?.create(kind)
}
</script>

<template>
  <div ref="rootEl" data-prks-arguments-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Arguments &amp; Stances not available offline</h2>
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
            Arguments &amp; Stances
          </h2>
          <div class="page-header__actions">
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-argument-new"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate('argument')"
            >
              New Argument
            </button>
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-stance-new"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate('stance')"
            >
              New Stance
            </button>
          </div>
        </div>
        <div ref="scopeHost" data-prks-role="index-scope-host"></div>
      </div>
      <div class="prks-tabs" role="tablist" aria-label="Argument kind">
        <button
          type="button"
          class="prks-tab"
          :class="{ 'is-active': tabSelected('all') }"
          :aria-selected="tabSelected('all') ? 'true' : 'false'"
          data-arg-filter="all"
          @click="onFilter('all')"
        >
          All
        </button>
        <button
          type="button"
          class="prks-tab"
          :class="{ 'is-active': tabSelected('argument') }"
          :aria-selected="tabSelected('argument') ? 'true' : 'false'"
          data-arg-filter="argument"
          @click="onFilter('argument')"
        >
          Arguments
        </button>
        <button
          type="button"
          class="prks-tab"
          :class="{ 'is-active': tabSelected('stance') }"
          :aria-selected="tabSelected('stance') ? 'true' : 'false'"
          data-arg-filter="stance"
          @click="onFilter('stance')"
        >
          Stances
        </button>
      </div>
      <div v-if="showToolbar" class="prks-toolbar prks-research-index__toolbar">
        <input
          id="prks-argument-search"
          ref="searchInput"
          v-model="searchQuery"
          type="search"
          class="prks-input"
          autocomplete="off"
          placeholder="Search arguments and stances…"
          aria-label="Search arguments and stances…"
        >
      </div>
      <div id="prks-argument-rows" class="list-view prks-research-index">
        <template v-if="filtered.length">
          <ArgumentRow
            v-for="item in filtered"
            :key="item.id"
            :item="item"
            :icon-html="rowIconHtml"
          />
        </template>
        <div v-else-if="showEmptyData" class="prks-research-index__empty">
          <p class="meta-row">{{ kindUi.empty }}</p>
          <p>
            <button
              v-if="kindUi.creationKinds.includes('argument')"
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-argument-new-empty"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate('argument')"
            >
              New Argument
            </button>
            <button
              v-if="kindUi.creationKinds.includes('stance')"
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-stance-new-empty"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate('stance')"
            >
              New Stance
            </button>
          </p>
        </div>
        <div v-else-if="showSearchEmpty" class="prks-research-index__empty">
          <p class="meta-row">No {{ scopeLabel }} match “{{ normalizedQuery }}”.</p>
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
