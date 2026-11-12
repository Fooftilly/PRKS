<script setup lang="ts">
import { ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import { fieldValue, setFieldValue } from './dom'
import { createPlaylistSaveGate } from './playlist-save'
import {
  clearPendingPlaylistAttachIfSame,
  playlistFailureMessage,
  readPendingPlaylistAttach,
  type PlaylistPending,
} from './submit'

const playlistError = ref('')
const playlistBusy = ref(false)
const gate = createPlaylistSaveGate()

interface PlaylistWindow {
  createPlaylist?: (title: string, description: string) => Promise<string | null>
  closeModals?: () => void
  prksCapturedWorkPanelStill?: (pending: PlaylistPending | null | undefined) => boolean
  addWorkToPlaylist?: (playlistId: string, workId: string, still: () => boolean) => Promise<unknown>
  __prksRefreshPlaylistSelects?: (playlistId: string) => Promise<void>
  __prksRefreshAllPlaylistSelects?: (playlistId: string) => Promise<void>
  prksTakePlaylistIndexCreateTabId?: () => string
  __prksReturnToWorkModalAfterPlaylist?: boolean
  prksNavigate?: (hash: string, options?: { tabId?: string }) => void
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
}

function runtime(): PlaylistWindow {
  return window as unknown as PlaylistWindow
}

function resetFields(): void {
  setFieldValue('playlist-title', '')
  setFieldValue('playlist-description', '')
  playlistError.value = ''
  // New opening: allow a fresh Create. An older in-flight save keeps its own
  // opening token and captured pending attachment.
  playlistBusy.value = false
}

async function onSave(): Promise<void> {
  const title = fieldValue('playlist-title').trim()
  const description = fieldValue('playlist-description').trim()
  if (!title) {
    playlistError.value = 'Playlist title is required.'
    return
  }
  playlistError.value = ''
  const w = runtime()
  await gate.save({
    modal: () => {
      const el = document.getElementById('playlist-modal')
      return el instanceof HTMLElement ? el : null
    },
    isBusy: () => playlistBusy.value,
    setBusy: (busy) => {
      playlistBusy.value = busy
    },
    readDraft: () => ({ title, description }),
    createPlaylist: (nextTitle, nextDescription) => {
      if (typeof w.createPlaylist !== 'function') return Promise.reject(new Error('Could not create playlist.'))
      return w.createPlaylist(nextTitle, nextDescription)
    },
    pendingAttach: readPendingPlaylistAttach,
    clearPendingIfSame: clearPendingPlaylistAttachIfSame,
    capturedWorkStill: (pending) =>
      typeof w.prksCapturedWorkPanelStill === 'function' && w.prksCapturedWorkPanelStill(pending),
    addWorkToPlaylist: (playlistId, workId, still) => {
      if (typeof w.addWorkToPlaylist !== 'function') return Promise.resolve()
      return w.addWorkToPlaylist(playlistId, workId, still)
    },
    refreshSelects: async (playlistId) => {
      if (typeof w.__prksRefreshPlaylistSelects === 'function') {
        await w.__prksRefreshPlaylistSelects(playlistId)
      }
    },
    refreshAllSelects: async (playlistId) => {
      if (typeof w.__prksRefreshAllPlaylistSelects === 'function') {
        await w.__prksRefreshAllPlaylistSelects(playlistId)
      }
    },
    takeIndexCreateTabId: () =>
      typeof w.prksTakePlaylistIndexCreateTabId === 'function' ? w.prksTakePlaylistIndexCreateTabId() : '',
    returnToWorkModal: () => w.__prksReturnToWorkModalAfterPlaylist === true,
    closeModals: () => {
      w.closeModals?.()
    },
    navigate: (hash, options) => {
      w.prksNavigate?.(hash, options)
    },
    onError: async (message) => {
      playlistError.value = message
      if (!document.getElementById('playlist-error')) {
        await w.prksAlertMessage?.(playlistFailureMessage(new Error(message)), 'Error')
      }
    },
  })
}

defineExpose({ resetFields })
</script>

<template>
  <div class="form-pane">
    <p class="modal-helper">Playlists group related videos (e.g. course lectures) and keep an ordered sequence.</p>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Title" for-id="playlist-title">
      <input
        id="playlist-title"
        type="text"
        placeholder="e.g. Philosophy Lectures"
        autocomplete="off"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
        :aria-invalid="playlistError ? 'true' : undefined"
      >
    </PrksField>
    <PrksField v-slot="{ labelledBy, describedBy }" label="Description" for-id="playlist-description">
      <textarea
        id="playlist-description"
        class="textarea-sm"
        placeholder="Optional description…"
        :aria-labelledby="labelledBy"
        :aria-describedby="describedBy"
      ></textarea>
    </PrksField>
    <PrksButton
      id="save-playlist-btn"
      variant="primary"
      :busy="playlistBusy"
      busy-label="Creating…"
      @click="onSave"
    >Create playlist</PrksButton>
    <p
      id="playlist-error"
      class="folder-field-error"
      :class="{ hidden: !playlistError }"
      role="alert"
      aria-live="polite"
    >{{ playlistError }}</p>
  </div>
</template>
