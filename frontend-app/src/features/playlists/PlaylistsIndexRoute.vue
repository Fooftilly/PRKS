<script setup lang="ts">
import { computed, inject } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { playlistItemCountLabel } from './format'
import { playlistIntentsKey } from './intents'
import type { PlaylistIndexProjection } from './projection'

const props = defineProps<{
  projection: PlaylistIndexProjection
}>()

const intents = inject(playlistIntentsKey)
const unavailable = computed(() => props.projection.availability === 'unavailable')
const items = computed(() => props.projection.items)

function icon(name: string): string {
  return typeof window.prksIcon === 'function' ? window.prksIcon(name, { size: 'sm' }) : ''
}

function rowHref(id: string): string {
  return `#/playlists/${encodeURIComponent(id)}`
}

function onCreate(): void {
  intents?.create()
}

function activateRouteLink(event: KeyboardEvent): void {
  const target = event.currentTarget
  if (target instanceof HTMLElement) target.click()
}
</script>

<template>
  <div data-prks-playlists-index-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Playlists not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This item is not available offline.
      </PrksInlineMessage>
    </template>
    <div v-else class="playlists-page">
      <div class="prks-page-header page-header tags-page__header">
        <div class="page-header__title-row">
          <h2 class="prks-page-title">Playlists</h2>
          <div class="page-header__actions">
            <PrksButton id="prks-playlists-header-new" @click="onCreate">
              New playlist
            </PrksButton>
          </div>
        </div>
        <p class="tags-page__sub playlists-page__sub">Open playlist row to view or edit ordered items.</p>
      </div>
      <div class="list-view playlists-page__list">
        <template v-if="items.length">
          <div
            v-for="item in items"
            :key="item.id"
            class="project-card playlists-page__list-item"
            role="link"
            tabindex="0"
            :data-prks-route="rowHref(item.id)"
            data-prks-middleclick-nav="1"
            @keydown.enter.prevent="activateRouteLink"
          >
            <div class="playlists-page__list-main">
              <span class="playlists-page__badge">
                <span v-html="icon('clapperboard')"></span>
                <span>{{ item.title }}</span>
              </span>
              <p class="meta-row playlists-page__list-stats">{{ playlistItemCountLabel(item.itemCount) }}</p>
            </div>
            <span class="playlists-page__list-arrow" aria-hidden="true">
              <span v-if="icon('chevronRight')" v-html="icon('chevronRight')"></span>
              <template v-else>→</template>
            </span>
          </div>
        </template>
        <div v-else class="playlists-page__empty">
          <p class="meta-row">No playlists yet.</p>
          <p>
            <PrksButton id="prks-playlists-empty-new" variant="primary" @click="onCreate">
              New playlist
            </PrksButton>
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
