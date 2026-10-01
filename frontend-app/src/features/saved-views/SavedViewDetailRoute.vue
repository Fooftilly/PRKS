<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import SearchResultsCollection from '../search/SearchResultsCollection.vue'
import type { SavedViewIntents } from './intents'
import type { SavedViewDetailProjection } from './projection'

const props = defineProps<{
  projection: SavedViewDetailProjection
  intents: SavedViewIntents
}>()

const rootEl = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)
const view = computed(() => props.projection.view)

function edit(): void {
  if (view.value) props.intents.edit(view.value)
}

function remove(): void {
  if (view.value) void props.intents.remove(view.value.id)
}

function paintMode(): void {
  const host = modeHost.value
  if (!host) return
  host.innerHTML = window.prksWorkBrowseModeToggleHtml?.('prks-work-browse-mode-saved-view') ?? ''
  window.prksBindWorkBrowseMode?.(rootEl.value)
  window.prksRefreshIcons?.(rootEl.value)
}

onMounted(paintMode)
watch(() => props.projection.generation, paintMode, { flush: 'post' })
</script>

<template>
  <div v-if="view" ref="rootEl" class="saved-view-detail">
    <div class="prks-page-header page-header">
      <div class="page-header__title-row">
        <div>
          <p class="saved-view-detail__kicker">Saved View</p>
          <h2 class="prks-page-title">{{ view.name }}</h2>
        </div>
        <div class="page-header__actions">
          <div ref="modeHost" data-prks-saved-view-mode-host style="display: contents"></div>
          <a class="prks-btn prks-btn--secondary" :href="projection.searchHash">Open as Search</a>
          <button id="prks-saved-view-edit" type="button" class="prks-btn prks-btn--secondary" @click="edit">Edit</button>
          <button
            id="prks-saved-view-delete"
            type="button"
            class="prks-btn prks-btn--secondary"
            :data-sv-delete="view.id"
            @click="remove"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
    <SearchResultsCollection :projection="projection.results" />
  </div>
  <div v-else data-prks-saved-view-not-found>
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">Saved View not found.</h2>
    </div>
    <p class="meta-row"><a class="prks-btn prks-btn--secondary" href="#/views">Back to Saved Views</a></p>
  </div>
</template>
