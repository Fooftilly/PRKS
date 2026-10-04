<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { usePendingAction } from '../../route-surface/pending-action'
import SearchResultsCollection from '../search/SearchResultsCollection.vue'
import type { SavedViewIntents } from './intents'
import type { SavedViewDetailProjection } from './projection'

const props = defineProps<{
  projection: SavedViewDetailProjection
  intents: SavedViewIntents
}>()

const { actionBusy, withBusy } = usePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const modeHost = ref<HTMLElement | null>(null)
const deleteError = ref('')
const view = computed(() => props.projection.view)

function edit(): void {
  if (view.value) props.intents.edit(view.value)
}

function remove(): void {
  const current = view.value
  if (!current) return
  void withBusy('delete', async () => {
    const outcome = await props.intents.remove(current.id)
    if (outcome.status === 'error') deleteError.value = outcome.message
    else if (outcome.status === 'success') deleteError.value = ''
  })
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
          <div ref="modeHost" class="work-html-slot" data-prks-saved-view-mode-host></div>
          <a class="prks-btn prks-btn--secondary" :href="projection.searchHash">Open as Search</a>
          <PrksButton id="prks-saved-view-edit" @click="edit">Edit</PrksButton>
          <PrksButton
            id="prks-saved-view-delete"
            variant="danger"
            :data-sv-delete="view.id"
            :busy="actionBusy('delete')"
            busy-label="Deleting…"
            @click="remove"
          >
            Delete Saved View
          </PrksButton>
        </div>
      </div>
    </div>
    <PrksInlineMessage v-if="deleteError" tone="error" status data-sv-delete-error>
      {{ deleteError }}
    </PrksInlineMessage>
    <SearchResultsCollection :projection="projection.results" />
  </div>
  <div v-else-if="projection.availability === 'error'" data-prks-saved-view-load-error>
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">Could not load Saved View.</h2>
    </div>
    <p class="meta-row"><a class="prks-btn prks-btn--secondary" href="#/views">Back to Saved Views</a></p>
  </div>
  <div v-else data-prks-saved-view-not-found>
    <div class="prks-page-header page-header">
      <h2 class="prks-page-title">Saved View not found.</h2>
    </div>
    <p class="meta-row"><a class="prks-btn prks-btn--secondary" href="#/views">Back to Saved Views</a></p>
  </div>
</template>
