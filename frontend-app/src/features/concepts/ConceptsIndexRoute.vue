<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { useResearchIndexList } from '../../research-index/useResearchIndexList'
import ConceptRow from './ConceptRow.vue'
import { conceptIntentsKey } from './intents'
import { filterConceptIndexItems, normalizeConceptSearchQuery } from './match'
import type { ConceptIndexProjection } from './projection'

const MUTATION_ROLE = 'concept-mutation-control'

const props = defineProps<{
  projection: ConceptIndexProjection
}>()

const intents = inject(conceptIntentsKey)
const unavailable = computed(() => props.projection.availability === 'unavailable')
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
  items: computed(() => props.projection.items),
  unavailable,
  generation: computed(() => props.projection.generation),
  filter: filterConceptIndexItems,
  normalizeQuery: normalizeConceptSearchQuery,
  icon: 'network',
  scopeLabel: 'Concepts',
  rootEl,
  titleIconHost,
  scopeHost,
  searchInput,
})

function onCreate(): void {
  void intents?.create()
}
</script>

<template>
  <div ref="rootEl" data-prks-concepts-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Concepts not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This list has not been cached on this device.
      </PrksInlineMessage>
    </template>
    <template v-else>
      <div class="prks-page-header page-header">
        <div class="page-header__title-row">
          <h2 class="prks-page-title">
            <span ref="titleIconHost" aria-hidden="true"></span>
            Concepts
          </h2>
          <div class="page-header__actions">
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-concept-new"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate"
            >
              New Concept
            </button>
          </div>
        </div>
        <div ref="scopeHost" data-prks-role="index-scope-host"></div>
      </div>
      <div v-if="showToolbar" class="prks-toolbar prks-research-index__toolbar">
        <input
          id="prks-concept-search"
          ref="searchInput"
          v-model="searchQuery"
          type="search"
          class="prks-input"
          autocomplete="off"
          placeholder="Search concepts…"
          aria-label="Search concepts…"
        >
      </div>
      <div id="prks-concept-rows" class="list-view prks-research-index">
        <template v-if="filtered.length">
          <ConceptRow
            v-for="item in filtered"
            :key="item.id"
            :item="item"
            :icon-html="rowIconHtml"
          />
        </template>
        <div v-else-if="showEmptyData" class="prks-research-index__empty">
          <p class="meta-row">No Concepts yet.</p>
          <p>
            <button
              type="button"
              class="prks-btn prks-btn--secondary"
              id="prks-concept-new-empty"
              :data-prks-role="MUTATION_ROLE"
              @click="onCreate"
            >
              New Concept
            </button>
          </p>
          <p class="meta-row prks-research-index__empty-hint">
            Concepts are also created automatically when you type
            <code>[[concept:Name]]</code> in research notes.
          </p>
        </div>
        <div v-else-if="showSearchEmpty" class="prks-research-index__empty">
          <p class="meta-row">No Concepts match “{{ normalizedQuery }}”.</p>
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
