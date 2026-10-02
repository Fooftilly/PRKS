<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import { usePendingAction } from '../../route-surface/pending-action'
import type { SavedViewIntents } from './intents'
import { SAVED_VIEWS_EMPTY, SAVED_VIEWS_EMPTY_HINT, type SavedViewIndexProjection } from './projection'

const props = defineProps<{
  projection: SavedViewIndexProjection
  intents: SavedViewIntents
}>()

const { actionBusy, actionBlocked, withBusy } = usePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const rowErrors = ref<Record<string, string>>({})
const headerIcon = computed(() => window.prksPageHeaderIconHtml?.('bookmark') ?? '')
const bookmarkIcon = computed(() => window.prksIcon?.('bookmark', { size: 'sm' }) ?? '')
const rows = computed(() => props.projection.rows)

function deleteKey(viewId: string): string {
  return `delete:${viewId}`
}

function editKey(viewId: string): string {
  return `edit:${viewId}`
}

function showRowError(viewId: string, message: string): void {
  rowErrors.value = { ...rowErrors.value, [viewId]: message }
}

function clearRowError(viewId: string): void {
  if (!rowErrors.value[viewId]) return
  const next = { ...rowErrors.value }
  delete next[viewId]
  rowErrors.value = next
}

function edit(viewId: string): void {
  void withBusy(editKey(viewId), async () => {
    try {
      const outcome = await props.intents.editById(viewId)
      if (outcome.status === 'error') showRowError(viewId, outcome.message)
      else if (outcome.status === 'success') clearRowError(viewId)
    } catch (err) {
      showRowError(viewId, err instanceof Error && err.message.trim() ? err.message.trim() : 'Could not open Saved View.')
    }
  })
}

function remove(viewId: string): void {
  void withBusy(deleteKey(viewId), async () => {
    try {
      const outcome = await props.intents.removeFromIndex(viewId)
      if (outcome.status === 'error') showRowError(viewId, outcome.message)
      else if (outcome.status === 'success') clearRowError(viewId)
    } catch (err) {
      showRowError(viewId, err instanceof Error && err.message.trim() ? err.message.trim() : 'Could not delete Saved View.')
    }
  })
}

onMounted(() => {
  window.prksRefreshIcons?.(rootEl.value)
})
</script>

<template>
  <div ref="rootEl" class="saved-views-page">
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">
        <span class="work-html-slot" v-html="headerIcon"></span>
        Saved Views
      </h2>
    </div>
    <div class="list-view saved-views-page__list">
      <template v-if="rows.length">
        <div v-for="row in rows" :key="row.id" class="project-card saved-views-page__list-item">
          <a class="saved-views-page__list-main" :href="row.href">
            <span class="saved-views-page__badge">
              <span class="work-html-slot" v-html="bookmarkIcon"></span>
              <span>{{ row.name }}</span>
            </span>
            <p class="meta-row saved-views-page__summary">{{ row.summary }}</p>
          </a>
          <div class="saved-views-page__row-actions">
            <a class="prks-btn prks-btn--secondary prks-btn--sm" :href="row.href">Open</a>
            <PrksButton
              variant="secondary"
              size="sm"
              :data-sv-index-edit="row.id"
              :busy="actionBusy(editKey(row.id))"
              :disabled="actionBlocked(editKey(row.id))"
              busy-label="Opening…"
              @click="edit(row.id)"
            >
              Edit
            </PrksButton>
            <PrksButton
              variant="danger"
              size="sm"
              :data-sv-index-delete="row.id"
              :busy="actionBusy(deleteKey(row.id))"
              :disabled="actionBlocked(deleteKey(row.id))"
              busy-label="Deleting…"
              @click="remove(row.id)"
            >
              Delete
            </PrksButton>
          </div>
          <p
            v-if="rowErrors[row.id]"
            class="prks-inline-message prks-inline-message--error saved-views-page__row-error"
            role="status"
            :data-sv-index-error="row.id"
          >
            {{ rowErrors[row.id] }}
          </p>
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
