<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, onUpdated, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import PrksIconButton from '../../components/PrksIconButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { playlistIntentsKey, type PlaylistSaveResult, type PlaylistVideoChoice } from './intents'
import { usePlaylistPendingAction } from './pending-action'
import type { PlaylistDetailProjection } from './projection'
import type { PlaylistFieldDraft, PlaylistWorkItem } from './types'

const props = defineProps<{
  projection: PlaylistDetailProjection
}>()

const intents = inject(playlistIntentsKey)
const { actionBusy, actionBlocked, resetPending, withBusy } = usePlaylistPendingAction()
const rootEl = ref<HTMLElement | null>(null)
const draft = ref<PlaylistFieldDraft>({ title: '', description: '', original_url: '' })
const fieldBaseline = ref<PlaylistFieldDraft>({ title: '', description: '', original_url: '' })
const status = ref('')
const addQuery = ref('')
const addStatus = ref('')
const addOpen = ref(false)
const choices = ref<PlaylistVideoChoice[]>([])
const catalogueUnavailable = ref(false)
const renaming = ref<Record<string, string>>({})

const playlist = computed(() => props.projection.playlist)
const editing = computed(() => props.projection.editing && !!playlist.value)
const unavailable = computed(() => props.projection.availability === 'unavailable')
const notFound = computed(() => props.projection.availability === 'not-found' || !playlist.value)
const shown = computed<PlaylistFieldDraft>(() => ({
  title: playlist.value?.title || '',
  description: playlist.value?.description || '',
  original_url: playlist.value?.originalUrl || '',
}))

const filteredChoices = computed(() => {
  const query = addQuery.value.trim().toLowerCase()
  const rows = !query
    ? choices.value
    : choices.value.filter((row) => row.title.toLowerCase().includes(query))
  return rows.slice(0, 30)
})

function icon(name: string): string {
  return typeof window.prksIcon === 'function' ? window.prksIcon(name, { size: 'sm' }) : ''
}

function alertMessage(message: string, title: string): void {
  if (!message) return
  const alert = window.prksAlertMessage
  if (typeof alert === 'function') void alert(message, title)
}

function report(result: PlaylistSaveResult | void, title: string): void {
  if (!result || result.ok) return
  if (title === 'Save') status.value = result.message
  else alertMessage(result.message, title)
}

function isRenaming(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(renaming.value, id)
}

function settleOffline(): void {
  const root = rootEl.value
  if (!root || typeof window.prksApplyPlaylistOfflineState !== 'function') return
  window.prksApplyPlaylistOfflineState(root)
}

onUpdated(settleOffline)

watch(
  () => `${playlist.value?.id || ''}:${props.projection.editing ? '1' : '0'}`,
  (key, previous) => {
    if (key === previous) return
    intents?.invalidateEditSession()
    resetPending()
    if (!props.projection.editing || !playlist.value) {
      renaming.value = {}
      return
    }
    const session = { ...shown.value }
    draft.value = { ...session }
    fieldBaseline.value = { ...session }
    status.value = ''
    addStatus.value = ''
    addQuery.value = ''
    addOpen.value = false
    renaming.value = {}
  },
  { immediate: true },
)

watch(
  () => playlist.value?.id || '',
  (id, previous) => {
    if (!previous || id === previous) return
    resetPending()
  },
)

function itemKey(ids: readonly string[]): string {
  return ids.join('\0')
}

let choiceToken = 0
async function refreshChoices(): Promise<void> {
  const token = ++choiceToken
  const current = playlist.value
  if (!props.projection.editing || !current || !intents) {
    choices.value = []
    catalogueUnavailable.value = false
    return
  }
  const playlistId = current.id
  const itemIds = current.items.map((item) => item.id)
  const loaded = await intents.loadAddableVideos(playlistId, itemIds)
  if (token !== choiceToken) return
  const live = playlist.value
  if (
    !props.projection.editing ||
    !live ||
    live.id !== playlistId ||
    itemKey(live.items.map((item) => item.id)) !== itemKey(itemIds)
  ) {
    return
  }
  if (loaded.status === 'stale') return
  if (loaded.status === 'unavailable') {
    choices.value = []
    catalogueUnavailable.value = true
    return
  }
  catalogueUnavailable.value = false
  choices.value = loaded.choices
}

watch(
  () => {
    const current = playlist.value
    if (!props.projection.editing || !current) return ''
    return `${current.id}|${current.items.map((item) => item.id).join(',')}`
  },
  () => {
    void refreshChoices()
  },
  { immediate: true },
)

function onRuntimeState(state: string): void {
  if (state !== 'online' || !props.projection.editing) return
  void refreshChoices()
}

let stopConnectivity: (() => void) | null = null
onMounted(() => {
  const subscribe = window.prksOfflineRuntimeSubscribe
  if (typeof subscribe !== 'function') return
  stopConnectivity = subscribe(onRuntimeState)
})
onBeforeUnmount(() => {
  stopConnectivity?.()
  stopConnectivity = null
})

function viewIdentity(): { id: string; generation: number; editSession: number } | null {
  const id = playlist.value?.id
  if (!id) return null
  return {
    id,
    generation: props.projection.generation,
    editSession: intents?.editSession() ?? 0,
  }
}

function viewStill(identity: { id: string; generation: number; editSession: number } | null): boolean {
  if (!identity) return false
  return (
    playlist.value?.id === identity.id &&
    props.projection.generation === identity.generation &&
    (intents?.editSession() ?? 0) === identity.editSession
  )
}

function onCancel(): void {
  const id = playlist.value?.id
  if (!id) return
  status.value = ''
  intents?.cancelEdit(id)
}

async function onSave(): Promise<void> {
  const current = playlist.value
  const identity = viewIdentity()
  if (!current || !identity) return
  if (!draft.value.title.trim()) {
    status.value = 'Title is required.'
    return
  }
  const draftSnapshot = { ...draft.value }
  const baselineSnapshot = { ...fieldBaseline.value }
  await withBusy('save', async () => {
    const result = await intents?.saveFields(current.id, draftSnapshot, baselineSnapshot)
    if (!viewStill(identity) || !result) return
    if (!result.ok) status.value = result.message
    else status.value = ''
  })
}

async function onReorder(workId: string, direction: -1 | 1): Promise<void> {
  const current = playlist.value
  const identity = viewIdentity()
  if (!current || !identity) return
  const ids = current.items.map((item) => item.id)
  const index = ids.indexOf(workId)
  const target = index + direction
  if (index < 0 || target < 0 || target >= ids.length) return
  const next = ids.slice()
  const swapped = next[target]
  const currentId = next[index]
  if (!swapped || !currentId) return
  next[target] = currentId
  next[index] = swapped
  const key = reorderKey(workId, direction)
  await withBusy(key, async () => {
    const result = await intents?.reorder(current.id, next)
    if (!viewStill(identity)) return
    report(result, 'Reorder')
  })
}

function reorderKey(workId: string, direction: -1 | 1): string {
  return `reorder:${workId}:${direction < 0 ? 'up' : 'down'}`
}

async function onRemoveWork(workId: string): Promise<void> {
  const current = playlist.value
  const identity = viewIdentity()
  if (!current || !identity) return
  await withBusy(`remove:${workId}`, async () => {
    const result = await intents?.removeWork(current.id, workId)
    if (!viewStill(identity)) return
    report(result, 'Remove')
  })
}

async function onAdd(workId: string): Promise<void> {
  const current = playlist.value
  const identity = viewIdentity()
  if (!current || !identity || !workId) return
  await withBusy(`add:${workId}`, async () => {
    const result = await intents?.addWork(current.id, workId)
    if (!viewStill(identity) || !result) return
    if (!result.ok) addStatus.value = result.message
    else {
      addStatus.value = 'Added.'
      addQuery.value = ''
      addOpen.value = false
    }
  })
}

async function onBeginRename(item: PlaylistWorkItem): Promise<void> {
  const current = playlist.value
  if (!current) return
  renaming.value = { ...renaming.value, [item.id]: item.title }
  intents?.beginRename(current.id, item.id)
  await nextTick()
  const input = rootEl.value?.querySelector<HTMLInputElement>(`#prks-pl-rename-input-${item.id}`)
  if (!input) return
  input.focus()
  const value = String(input.value || '')
  try {
    input.setSelectionRange(value.length, value.length)
  } catch {
    /* selection is optional */
  }
}

function onCancelRename(workId: string): void {
  const current = playlist.value
  if (!current) return
  const next = { ...renaming.value }
  delete next[workId]
  renaming.value = next
  intents?.cancelRename(current.id, workId)
}

async function onSaveRename(workId: string): Promise<void> {
  const current = playlist.value
  const identity = viewIdentity()
  if (!current || !identity) return
  const title = renaming.value[workId] || ''
  await withBusy(`rename:${workId}`, async () => {
    const result = await intents?.saveWorkTitle(current.id, workId, title)
    if (!viewStill(identity) || !result) return
    if (!result.ok) {
      alertMessage(result.message, result.message.includes('cannot be renamed') ? 'Rename unavailable' : 'Error')
      return
    }
    const next = { ...renaming.value }
    delete next[workId]
    renaming.value = next
  })
}

function onDelete(): void {
  const current = playlist.value
  if (!current) return
  void withBusy('delete', async () => {
    await intents?.remove(current.id, current)
  })
}

function onAddFocus(): void {
  addOpen.value = true
  void refreshChoices()
}

function onAddInput(): void {
  addOpen.value = true
}

function activateRouteLink(event: KeyboardEvent): void {
  const target = event.currentTarget
  if (target instanceof HTMLElement) target.click()
}

function onAddBlur(): void {
  window.setTimeout(() => {
    addOpen.value = false
  }, 200)
}
</script>

<template>
  <div ref="rootEl" data-prks-playlist-detail-view>
    <template v-if="unavailable">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Playlist not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This item is not available offline.
      </PrksInlineMessage>
    </template>
    <template v-else-if="notFound || !playlist">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Playlist not found</h2>
      </div>
    </template>
    <div
      v-else
      class="prks-playlist-detail"
      :class="{ 'prks-playlist-detail--editing': editing }"
    >
      <div class="prks-page-header page-header page-header--split prks-playlist-detail__header">
        <div class="page-header__title-row">
          <h2 class="prks-page-title">{{ playlist.title }}</h2>
          <a class="route-sidebar__link" href="#/playlists">All playlists</a>
        </div>
        <div class="page-header__actions">
          <PrksButton
            id="prks-playlist-delete-btn"
            variant="danger"
            :data-playlist-id="playlist.id"
            :busy="actionBusy('delete')"
            :disabled="actionBlocked('delete')"
            busy-label="Deleting…"
            @click="onDelete"
          >
            <span v-if="icon('trash')" v-html="icon('trash')"></span>
            Delete playlist
          </PrksButton>
        </div>
      </div>
      <p v-if="playlist.description" class="meta-row prks-playlist-detail__desc">{{ playlist.description }}</p>
      <p v-if="editing" class="meta-row meta-row--compact prks-playlist-detail__hint">
        Reorder, rename, or remove items. Open Details → Done when finished.
      </p>
      <div v-if="editing" class="doc-meta-card form-pane doc-meta-card--editing prks-playlist-detail__editor">
        <div class="card-heading-row">
          <h3 class="doc-meta-card__accent-title">Edit playlist</h3>
          <PrksIconButton id="prks-playlist-edit-close" class="close-btn" label="Close" @click="onCancel">
            &times;
          </PrksIconButton>
        </div>
        <PrksField v-slot="{ labelledBy, describedBy }" label="Title" for-id="prks-playlist-edit-title">
        <input
          id="prks-playlist-edit-title"
          v-model="draft.title"
          type="text"
          autocomplete="off"
         :aria-labelledby="labelledBy" :aria-describedby="describedBy">
        </PrksField>
        <PrksField v-slot="{ labelledBy, describedBy }" label="Description" for-id="prks-playlist-edit-desc">
        <textarea id="prks-playlist-edit-desc" v-model="draft.description" class="textarea-sm" :aria-labelledby="labelledBy" :aria-describedby="describedBy"></textarea>
        </PrksField>
        <PrksField v-slot="{ labelledBy, describedBy }" label="Original playlist URL" for-id="prks-playlist-edit-original-url">
        <input
          id="prks-playlist-edit-original-url"
          v-model="draft.original_url"
          type="url"
          placeholder="https://..."
          autocomplete="off"
         :aria-labelledby="labelledBy" :aria-describedby="describedBy">
        </PrksField>
        <div class="prks-form-actions prks-form-actions--split form-actions">
          <PrksButton id="prks-playlist-edit-cancel" @click="onCancel">
            Cancel
          </PrksButton>
          <PrksButton
            id="prks-playlist-edit-save"
            variant="primary"
            :busy="actionBusy('save')"
            :disabled="actionBlocked('save')"
            busy-label="Saving…"
            @click="onSave"
          >
            Save
          </PrksButton>
        </div>
        <p id="prks-playlist-edit-status" class="meta-row meta-row--spaced" aria-live="polite">{{ status }}</p>
      </div>
      <div v-if="editing" class="doc-meta-card prks-playlist-detail__add">
        <h3>Add video</h3>
        <p class="meta-row meta-row--compact">Search for a video and click Add.</p>
        <div class="tag-add-shell combobox-container tag-add-shell--flush">
          <div class="tag-add-shell__field">
            <input
              id="prks-playlist-add-search"
              v-model="addQuery"
              type="text"
              class="tag-add-shell__input"
              placeholder="Search videos…"
              maxlength="300"
              autocomplete="off"
              aria-label="Search videos to add"
              @focus="onAddFocus"
              @input="onAddInput"
              @blur="onAddBlur"
            >
          </div>
          <div
            id="prks-playlist-add-results"
            class="combobox-results combobox-results--tag-panel"
            :class="{ hidden: !addOpen }"
          >
            <div v-if="catalogueUnavailable" class="result-item no-results">
              Videos are not available right now.
            </div>
            <div v-else-if="!filteredChoices.length" class="result-item no-results">No videos found</div>
            <div
              v-for="choice in filteredChoices"
              :key="choice.id"
              class="result-item prks-playlist-add-result"
            >
              <div class="prks-playlist-add-result__label">{{ choice.title }}</div>
              <PrksButton
                size="sm"
                :busy="actionBusy(`add:${choice.id}`)"
                :disabled="actionBlocked(`add:${choice.id}`)"
                busy-label="Adding…"
                @mousedown.prevent
                @click="onAdd(choice.id)"
              >
                Add
              </PrksButton>
            </div>
          </div>
        </div>
        <p id="prks-playlist-add-status" class="meta-row meta-row--spaced" aria-live="polite">{{ addStatus }}</p>
      </div>
      <div class="list-view prks-playlist-detail__list">
        <template v-if="playlist.items.length">
          <div
            v-for="item in playlist.items"
            :key="item.id"
            class="prks-playlist-item project-card"
            :class="{ 'prks-playlist-item--editing': editing }"
          >
            <div class="prks-playlist-item__row">
              <div
                v-if="editing && isRenaming(item.id)"
                class="prks-playlist-item__body prks-playlist-item__body--rename"
              >
                <input
                  :id="`prks-pl-rename-input-${item.id}`"
                  v-model="renaming[item.id]"
                  type="text"
                  class="prks-playlist-item__rename-input"
                  autocomplete="off"
                  aria-label="Video title"
                >
                <div class="meta-row">{{ item.subtitle }}</div>
              </div>
              <div v-else-if="editing" class="prks-playlist-item__body">
                <div class="card-title prks-playlist-item__title">{{ item.title }}</div>
                <div class="meta-row">{{ item.subtitle }}</div>
              </div>
              <div
                v-else
                class="prks-playlist-item__body prks-playlist-item__body--link"
                role="link"
                tabindex="0"
                :data-pl-nav="item.id"
                :data-prks-route="`#/works/${encodeURIComponent(item.id)}`"
                @keydown.enter.prevent="activateRouteLink"
              >
                <div class="card-title prks-playlist-item__title">{{ item.title }}</div>
                <div class="meta-row">{{ item.subtitle }}</div>
              </div>
              <div v-if="editing" class="prks-playlist-item__actions">
                <PrksButton
                  size="sm"
                  :data-pl-up="item.id"
                  :busy="actionBusy(reorderKey(item.id, -1))"
                  :disabled="actionBlocked(reorderKey(item.id, -1))"
                  busy-label="Reordering…"
                  title="Move up"
                  :aria-label="actionBusy(reorderKey(item.id, -1)) ? 'Reordering…' : 'Move up'"
                  @click="onReorder(item.id, -1)"
                >
                  <span v-if="icon('arrowUp')" v-html="icon('arrowUp')"></span>
                </PrksButton>
                <PrksButton
                  size="sm"
                  :data-pl-down="item.id"
                  :busy="actionBusy(reorderKey(item.id, 1))"
                  :disabled="actionBlocked(reorderKey(item.id, 1))"
                  busy-label="Reordering…"
                  title="Move down"
                  :aria-label="actionBusy(reorderKey(item.id, 1)) ? 'Reordering…' : 'Move down'"
                  @click="onReorder(item.id, 1)"
                >
                  <span v-if="icon('arrowDown')" v-html="icon('arrowDown')"></span>
                </PrksButton>
                <template v-if="isRenaming(item.id)">
                  <PrksButton
                    size="sm"
                    :data-pl-rename-save="item.id"
                    :busy="actionBusy(`rename:${item.id}`)"
                    :disabled="actionBlocked(`rename:${item.id}`)"
                    busy-label="Renaming…"
                    title="Save title"
                    :aria-label="actionBusy(`rename:${item.id}`) ? 'Renaming…' : 'Save title'"
                    @click="onSaveRename(item.id)"
                  >
                    <span v-if="icon('check')" v-html="icon('check')"></span>
                  </PrksButton>
                  <button
                    type="button"
                    class="prks-btn prks-btn--secondary prks-btn--sm"
                    :data-pl-rename-cancel="item.id"
                    title="Cancel rename"
                    aria-label="Cancel rename"
                    @click="onCancelRename(item.id)"
                  >
                    <span v-if="icon('x')" v-html="icon('x')"></span>
                  </button>
                </template>
                <button
                  v-else
                  type="button"
                  class="prks-btn prks-btn--secondary prks-btn--sm"
                  :data-pl-rename="item.id"
                  title="Rename title"
                  aria-label="Rename title"
                  @click="onBeginRename(item)"
                >
                  <span v-if="icon('pencil')" v-html="icon('pencil')"></span>
                </button>
                <PrksButton
                  size="sm"
                  :data-pl-remove="item.id"
                  :busy="actionBusy(`remove:${item.id}`)"
                  :disabled="actionBlocked(`remove:${item.id}`)"
                  busy-label="Removing…"
                  title="Remove from playlist"
                  :aria-label="actionBusy(`remove:${item.id}`) ? 'Removing…' : 'Remove from playlist'"
                  @click="onRemoveWork(item.id)"
                >
                  <span v-if="icon('x')" v-html="icon('x')"></span>
                </PrksButton>
              </div>
            </div>
          </div>
        </template>
        <p v-else class="meta-row prks-playlist-detail__empty">No items yet. Use Details → Edit to add videos.</p>
      </div>
    </div>
  </div>
</template>
