<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { SavedViewIntents } from './intents'
import { SAVED_VIEWS_EMPTY, SAVED_VIEWS_EMPTY_HINT, type SavedViewIndexProjection } from './projection'

const props = defineProps<{
  projection: SavedViewIndexProjection
  intents: SavedViewIntents
}>()

const rootEl = ref<HTMLElement | null>(null)
const headerIcon = computed(() => window.prksPageHeaderIconHtml?.('bookmark') ?? '')
const bookmarkIcon = computed(() => window.prksIcon?.('bookmark', { size: 'sm' }) ?? '')
const rows = computed(() => props.projection.rows)

function edit(viewId: string): void {
  void props.intents.editById(viewId)
}

function remove(viewId: string): void {
  void props.intents.removeFromIndex(viewId)
}

onMounted(() => {
  window.prksRefreshIcons?.(rootEl.value)
})
</script>

<template>
  <div ref="rootEl" class="saved-views-page">
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">
        <span style="display: contents" v-html="headerIcon"></span>
        Saved Views
      </h2>
    </div>
    <div class="list-view saved-views-page__list">
      <template v-if="rows.length">
        <div v-for="row in rows" :key="row.id" class="project-card saved-views-page__list-item">
          <a class="saved-views-page__list-main" :href="row.href">
            <span class="saved-views-page__badge">
              <span style="display: contents" v-html="bookmarkIcon"></span>
              <span>{{ row.name }}</span>
            </span>
            <p class="meta-row saved-views-page__summary">{{ row.summary }}</p>
          </a>
          <div class="saved-views-page__row-actions">
            <a class="prks-btn prks-btn--secondary prks-btn--sm" :href="row.href">Open</a>
            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" @click="edit(row.id)">Edit</button>
            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" @click="remove(row.id)">Delete</button>
          </div>
        </div>
      </template>
      <template v-else>
        <p class="meta-row saved-views-page__empty">{{ SAVED_VIEWS_EMPTY }}</p>
        <p class="meta-row">{{ SAVED_VIEWS_EMPTY_HINT }}</p>
        <p>
          <button
            id="prks-saved-views-empty-search"
            type="button"
            class="prks-btn prks-btn--secondary"
            @click="intents.openSearch()"
          >
            Search or jump
          </button>
        </p>
      </template>
    </div>
  </div>
</template>
