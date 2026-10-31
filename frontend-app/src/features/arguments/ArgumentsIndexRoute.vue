<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksScopeLine from '../../components/PrksScopeLine.vue'
import { useResearchIndexList } from '../../research-index/useResearchIndexList'
import { argumentIntentsKey } from './intents'
import { useArgumentPendingAction } from './pending-action'
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
const searchInput = ref<HTMLInputElement | null>(null)
const {
  searchQuery,
  filtered,
  normalizedQuery,
  showToolbar,
  showEmptyData,
  showSearchEmpty,
  rowIconHtml,
  scopeLine,
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
  searchInput,
})

const scopeLabel = computed(() => kindUi.value.plural)
const { actionBusy, actionBlocked, withBusy } = useArgumentPendingAction()

/** Header keeps both creates. An empty state exposes one. */
const emptyActionKind = computed((): ArgumentKind => {
  const kinds = kindUi.value.creationKinds
  if (kinds.length === 1 && kinds[0]) return kinds[0]
  return 'argument'
})

const KIND_FILTERS = ['all', 'argument', 'stance'] as const
const ROWS_ID = 'prks-argument-rows'

function tabSelected(kind: 'all' | ArgumentKind): boolean {
  return props.projection.kind === kind
}

function isKindFilter(value: string | null): value is (typeof KIND_FILTERS)[number] {
  return value === 'all' || value === 'argument' || value === 'stance'
}

const activeTabId = computed(() => `prks-argument-kind-${props.projection.kind}`)

function onFilter(kind: 'all' | ArgumentKind): void {
  intents?.filterKind(kind)
}

/** Horizontal tabs: Left/Right wrap, Home/End jump, and the selected kind follows focus. */
function onTabKeydown(event: KeyboardEvent): void {
  if (!(event.target instanceof Element)) return
  const currentKind = event.target.getAttribute('data-arg-filter')
  if (!isKindFilter(currentKind)) return
  const current = KIND_FILTERS.indexOf(currentKind)
  let next = current
  if (event.key === 'ArrowRight') next = (current + 1) % KIND_FILTERS.length
  else if (event.key === 'ArrowLeft') next = (current + KIND_FILTERS.length - 1) % KIND_FILTERS.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = KIND_FILTERS.length - 1
  else return
  event.preventDefault()
  const kind = KIND_FILTERS[next]!
  if (!tabSelected(kind)) onFilter(kind)
  rootEl.value?.querySelector<HTMLButtonElement>(`#prks-argument-kind-${kind}`)?.focus()
}

function onCreate(kind: ArgumentKind): void {
  void withBusy(`create:${kind}`, () => intents?.create(kind) ?? Promise.resolve())
}
</script>

<template>
  <div ref="rootEl" data-prks-arguments-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Arguments &amp; Stances not available offline</h2>
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
            Arguments &amp; Stances
          </h2>
          <div class="page-header__actions">
            <PrksButton
              id="prks-argument-new"
              :data-prks-role="MUTATION_ROLE"
              :busy="actionBusy('create:argument')"
              :disabled="actionBlocked('create:argument')"
              busy-label="Creating…"
              @click="onCreate('argument')"
            >
              New Argument
            </PrksButton>
            <PrksButton
              id="prks-stance-new"
              :data-prks-role="MUTATION_ROLE"
              :busy="actionBusy('create:stance')"
              :disabled="actionBlocked('create:stance')"
              busy-label="Creating…"
              @click="onCreate('stance')"
            >
              New Stance
            </PrksButton>
          </div>
        </div>
        <PrksScopeLine v-bind="scopeLine" />
      </div>
      <div
        class="prks-tabs"
        role="tablist"
        aria-orientation="horizontal"
        aria-label="Argument kind"
        @keydown="onTabKeydown"
      >
        <button
          id="prks-argument-kind-all"
          type="button"
          class="prks-tab"
          :class="{ 'is-active': tabSelected('all') }"
          role="tab"
          :aria-selected="tabSelected('all') ? 'true' : 'false'"
          :aria-controls="ROWS_ID"
          :tabindex="tabSelected('all') ? 0 : -1"
          data-arg-filter="all"
          @click="onFilter('all')"
        >
          All
        </button>
        <button
          id="prks-argument-kind-argument"
          type="button"
          class="prks-tab"
          :class="{ 'is-active': tabSelected('argument') }"
          role="tab"
          :aria-selected="tabSelected('argument') ? 'true' : 'false'"
          :aria-controls="ROWS_ID"
          :tabindex="tabSelected('argument') ? 0 : -1"
          data-arg-filter="argument"
          @click="onFilter('argument')"
        >
          Arguments
        </button>
        <button
          id="prks-argument-kind-stance"
          type="button"
          class="prks-tab"
          :class="{ 'is-active': tabSelected('stance') }"
          role="tab"
          :aria-selected="tabSelected('stance') ? 'true' : 'false'"
          :aria-controls="ROWS_ID"
          :tabindex="tabSelected('stance') ? 0 : -1"
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
      <div
        id="prks-argument-rows"
        class="list-view prks-research-index"
        role="tabpanel"
        :aria-labelledby="activeTabId"
      >
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
            <PrksButton
              v-if="emptyActionKind === 'argument'"
              id="prks-argument-new-empty"
              :data-prks-role="MUTATION_ROLE"
              :busy="actionBusy('create:argument')"
              :disabled="actionBlocked('create:argument')"
              busy-label="Creating…"
              @click="onCreate('argument')"
            >
              New Argument
            </PrksButton>
            <PrksButton
              v-else
              id="prks-stance-new-empty"
              :data-prks-role="MUTATION_ROLE"
              :busy="actionBusy('create:stance')"
              :disabled="actionBlocked('create:stance')"
              busy-label="Creating…"
              @click="onCreate('stance')"
            >
              New Stance
            </PrksButton>
          </p>
        </div>
        <div v-else-if="showSearchEmpty" class="prks-research-index__empty">
          <p class="meta-row">No {{ scopeLabel }} match “{{ normalizedQuery }}”.</p>
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
