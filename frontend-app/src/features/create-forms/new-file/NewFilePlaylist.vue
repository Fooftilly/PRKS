<script setup lang="ts">
import { ref } from 'vue'
import { fieldValue, setFieldValue } from '../dom'
import { useInlineCombobox } from './combobox'
import { alertMessage, loadList, runtime, type PlaylistRow } from './runtime'
import { useNewFileState } from './state'

/** New File (YouTube): a searchable Playlist picker with inline create. */

const state = useNewFileState()
const rows = ref<PlaylistRow[]>([])
const createTitle = ref('')
let playlists: PlaylistRow[] = []

function normalize(value: unknown): string {
  return String(value || '')
    .trim()
    .toLowerCase()
}

function render(): void {
  const raw = fieldValue('work-video-playlist-search').trim()
  const query = normalize(raw)
  rows.value = (query ? playlists.filter((row) => normalize(row.title).includes(query)) : playlists).slice(0, 40)
  createTitle.value = query && !playlists.some((row) => normalize(row.title) === query) ? raw : ''
  combobox.shown()
}

const combobox = useInlineCombobox('work-video-playlist-search', 'work-video-playlist-results', {
  onOpen: render,
  enterCreates: true,
})

async function onFocus(): Promise<void> {
  combobox.cancelBlur()
  playlists = await loadList(runtime().fetchPlaylists)
  render()
}

function onInput(): void {
  state.playlistId.value = ''
  render()
}

function pick(row: PlaylistRow): void {
  setFieldValue('work-video-playlist-search', String(row.title || ''))
  state.playlistId.value = String(row.id)
  combobox.hide()
}

function create(): void {
  const title = createTitle.value
  if (!title) return
  const w = runtime()
  void state.trackQuickCreate(
    (async () => {
      try {
        // The durable boundary: the id is minted here, so the playlist is
        // pickable at once.
        const id = typeof w.createPlaylist === 'function' ? await w.createPlaylist(title, '') : null
        if (!id) throw new Error('create failed')
        playlists = await loadList(w.fetchPlaylists)
        setFieldValue('work-video-playlist-search', title)
        state.playlistId.value = id
        combobox.hide()
        return true
      } catch {
        await alertMessage('Could not create playlist.', 'Error')
        return false
      }
    })(),
  )
}

function reset(): void {
  setFieldValue('work-video-playlist-search', '')
  state.playlistId.value = ''
  combobox.hideNow()
}

defineExpose({ reset, close: combobox.close })
</script>

<template>
  <div id="work-video-playlist-row" class="prks-field" :class="{ hidden: state.kind.value !== 'video' }">
    <label for="work-video-playlist-search">Playlist</label>
    <div class="tag-add-shell combobox-container tag-add-shell--flush">
      <div class="tag-add-shell__field">
        <span class="tag-add-shell__icon"><i data-lucide="plus" class="prks-icon" aria-hidden="true"></i></span>
        <input
          id="work-video-playlist-search"
          type="text"
          class="tag-add-shell__input"
          placeholder="Search playlists…"
          maxlength="300"
          autocomplete="off"
          aria-describedby="work-video-playlist-hint"
          @focus="onFocus"
          @input="onInput"
          @blur="combobox.onBlur"
        >
        <input id="work-video-playlist-id" type="hidden" :value="state.playlistId.value" data-prks-count>
      </div>
      <div
        id="work-video-playlist-results"
        class="combobox-results combobox-results--tag-panel"
        :class="{ hidden: combobox.hidden.value, 'is-open': combobox.open.value }"
      >
        <div
          v-if="createTitle"
          class="result-item result-item--create"
          @mousedown.prevent="create"
        >Create playlist "{{ createTitle }}"</div>
        <div v-if="rows.length === 0 && !createTitle" class="result-item no-results">No playlists found</div>
        <div
          v-for="row in rows"
          :key="row.id"
          class="result-item"
          @mousedown.prevent="pick(row)"
        >{{ row.title || 'Playlist' }}</div>
      </div>
    </div>
    <p id="work-video-playlist-hint" class="meta-row meta-row--hint meta-row--compact">The video is added to this playlist when the file is created.</p>
  </div>
</template>
