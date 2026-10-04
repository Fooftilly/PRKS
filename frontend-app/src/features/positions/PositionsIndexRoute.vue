<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { useResearchIndexList } from '../../research-index/useResearchIndexList'
import { positionIntentsKey } from './intents'
import { filterPositionIndexItems, normalizePositionSearchQuery } from './match'
import PositionRow from './PositionRow.vue'
import type { PositionIndexProjection } from './projection'

const MUTATION_ROLE = 'position-mutation-control'

const props = defineProps<{
  projection: PositionIndexProjection
}>()

const intents = inject(positionIntentsKey)
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
  icon: 'flag',
  scopeLabel: 'Positions',
  filter: filterPositionIndexItems,
  normalizeQuery: normalizePositionSearchQuery,
  items: computed(() => props.projection.items),
  unavailable,
  generation: computed(() => props.projection.generation),
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
  <div ref="rootEl" data-prks-positions-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Positions not available offline</h2>
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
            Positions
          </h2>
          <div class="page-header__actions">
            <PrksButton id="prks-position-new" :data-prks-role="MUTATION_ROLE" @click="onCreate">
              New Position
            </PrksButton>
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
            <PrksButton id="prks-position-new-empty" :data-prks-role="MUTATION_ROLE" @click="onCreate">
              New Position
            </PrksButton>
          </p>
        </div>
        <div v-else-if="showSearchEmpty" class="prks-research-index__empty">
          <p class="meta-row">No Positions match “{{ normalizedQuery }}”.</p>
          <p>
            <PrksButton variant="ghost" size="sm" data-research-search-clear @click="clearSearch">
              Clear search
            </PrksButton>
          </p>
        </div>
      </div>
    </template>
  </div>
</template>
