<script setup lang="ts">
import { ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import { fieldValue, setFieldValue } from './dom'
import { playlistFailureMessage, submitPlaylistCreate } from './submit'

const playlistError = ref('')
const playlistBusy = ref(false)

interface PlaylistWindow {
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
}

function resetFields(): void {
  setFieldValue('playlist-title', '')
  setFieldValue('playlist-description', '')
  playlistError.value = ''
  playlistBusy.value = false
}

async function onSave(): Promise<void> {
  if (playlistBusy.value) return
  const title = fieldValue('playlist-title').trim()
  const description = fieldValue('playlist-description').trim()
  if (!title) {
    playlistError.value = 'Playlist title is required.'
    return
  }
  playlistError.value = ''
  playlistBusy.value = true
  try {
    await submitPlaylistCreate(title, description)
  } catch (error) {
    console.error(error)
    const message = playlistFailureMessage(error)
    playlistError.value = message
    const w = window as unknown as PlaylistWindow
    if (!document.getElementById('playlist-error')) {
      await w.prksAlertMessage?.(message, 'Error')
    }
  } finally {
    playlistBusy.value = false
  }
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
