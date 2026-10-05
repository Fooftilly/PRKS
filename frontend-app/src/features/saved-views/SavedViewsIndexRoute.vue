<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksLinkButton from '../../components/PrksLinkButton.vue'
import PrksState from '../../components/PrksState.vue'
import { usePendingAction } from '../../route-surface/pending-action'
import type { SavedViewIntents } from './intents'
import { SAVED_VIEWS_EMPTY, SAVED_VIEWS_EMPTY_HINT } from './projection'
import { useSavedViewsList } from './useSavedViewsList'

const props = defineProps<{
  intents: SavedViewIntents
}>()

const { rows, loaded, loading, loadError, refreshError, retry } = useSavedViewsList()

const { actionBusy, actionBlocked, withBusy } = usePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const rowErrors = ref<Record<string, string>>({})
const headerIcon = computed(() => window.prksPageHeaderIconHtml?.('bookmark') ?? '')
const bookmarkIcon = computed(() => window.prksIcon?.('bookmark', { size: 'sm' }) ?? '')

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

function refreshIcons(): void {
  window.prksRefreshIcons?.(rootEl.value)
}

// The header icon paints on mount, whatever the first read does; row icons
// follow the rows after each read and write.
onMounted(refreshIcons)
watch(rows, refreshIcons, { flush: 'post' })
</script>

<template>
  <div ref="rootEl" class="saved-views-page">
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">
        <span class="work-html-slot" v-html="headerIcon"></span>
        Saved Views
      </h2>
    </div>
    <PrksInlineMessage v-if="refreshError" tone="error" status data-saved-views-refresh-error>
      {{ refreshError }}
    </PrksInlineMessage>
    <PrksState v-if="loading" kind="loading" message="Loading Saved Views…" data-saved-views-loading />
    <PrksState v-else-if="loadError" kind="error" :message="loadError" data-saved-views-load-error>
      <PrksButton variant="secondary" size="sm" @click="retry">Try again</PrksButton>
    </PrksState>
    <div v-if="loaded" class="list-view saved-views-page__list">
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
            <PrksLinkButton :href="row.href" size="sm">Open</PrksLinkButton>
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
          <PrksInlineMessage
            v-if="rowErrors[row.id]"
            tone="error"
            status
            class="saved-views-page__row-error"
            :data-sv-index-error="row.id"
          >
            {{ rowErrors[row.id] }}
          </PrksInlineMessage>
        </div>
      </template>
      <template v-else-if="!refreshError">
        <p class="meta-row saved-views-page__empty">{{ SAVED_VIEWS_EMPTY }}</p>
        <p class="meta-row">{{ SAVED_VIEWS_EMPTY_HINT }}</p>
        <p>
          <PrksButton id="prks-saved-views-empty-search" @click="intents.openSearch()">
            Search or jump
          </PrksButton>
        </p>
      </template>
    </div>
  </div>
</template>
