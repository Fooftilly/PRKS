<script setup lang="ts">
import { computed, ref } from 'vue'
import { fieldValue, setFieldValue } from '../dom'
import { CHOOSE_PDF, BAD_YOUTUBE_URL, type SourceKind } from './save'
import { alertMessage, runtime, type VideoMeta } from './runtime'
import { useNewFileState } from './state'

const props = defineProps<{
  /** Focus a control (opening its disclosure first). */
  focusControl: (el: HTMLElement | null) => void
  /** Kind switch: the doc type, counts and errors follow the new source. */
  onKindChanged: (kind: SourceKind, switched: boolean) => void
  /** Title or Channel was filled programmatically: the section counts follow. */
  onAutofilled: () => void
}>()

const state = useNewFileState()
const dragActive = ref(false)

const fileInput = ref<HTMLInputElement | null>(null)
const dropZone = ref<HTMLElement | null>(null)

function formatByteSize(n: number): string {
  const bytes = Number(n)
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${Math.round(bytes)} B`
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024
    return `${kb >= 10 ? Math.round(kb) : kb.toFixed(1).replace(/\.0$/, '')} KB`
  }
  const mb = bytes / (1024 * 1024)
  return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1).replace(/\.0$/, '')} MB`
}

const fileName = computed(() => (state.pdf.value ? String(state.pdf.value.name || 'PDF') : ''))
const fileSize = computed(() => (state.pdf.value ? formatByteSize(state.pdf.value.size) : ''))

function clearPdf(): void {
  state.pdf.value = null
  if (fileInput.value) fileInput.value.value = ''
}

function clearVideo(): void {
  setFieldValue('work-video-url', '')
  setFieldValue('work-video-channel', '')
  state.setLastPreviewUrl('')
  state.setVideoMeta(null)
  state.videoEmbedUrl.value = ''
}

/** Switches the source; the other source's entry is dropped, not hidden. */
function setKind(next: SourceKind, switched = true): void {
  const prev = state.kind.value
  state.kind.value = next
  if (switched && next !== prev) {
    if (next === 'video') clearPdf()
    else clearVideo()
    state.clearErrors()
  }
  props.onKindChanged(next, switched && next !== prev)
}

function handleFile(file: File | null | undefined): void {
  if (state.kind.value === 'video') {
    void alertMessage('In YouTube URL mode, paste the link in Source.', 'Notice')
    return
  }
  if (!file) return
  const name = String(file.name || '').toLowerCase()
  const isPdf = name.endsWith('.pdf') || file.type === 'application/pdf'
  if (!isPdf) {
    state.clearErrors()
    state.errors.file = CHOOSE_PDF
    props.focusControl(dropZone.value)
    return
  }
  state.pdf.value = file
  state.errors.file = ''
  const title = document.getElementById('work-title')
  if (title instanceof HTMLInputElement && !title.value) {
    title.value = file.name.replace(/\.pdf$/i, '').replace(/_/g, ' ')
  }
}

function browse(): void {
  if (!state.pdf.value) fileInput.value?.click()
}

function onZoneKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' && event.key !== ' ') return
  if (state.pdf.value) return
  event.preventDefault()
  fileInput.value?.click()
}

function onDrop(event: DragEvent): void {
  dragActive.value = false
  const files = event.dataTransfer?.files
  if (files && files.length) handleFile(files[0])
}

function onFileChange(event: Event): void {
  const input = event.target
  if (input instanceof HTMLInputElement && input.files && input.files.length) handleFile(input.files[0])
}

/** Editing the URL voids the last preview; it is fetched again on blur. */
function onVideoInput(): void {
  state.errors.videoUrl = ''
  if (fieldValue('work-video-url').trim() === state.getLastPreviewUrl().trim()) return
  state.setVideoMeta(null)
  state.videoEmbedUrl.value = ''
}

/**
 * Shows the YouTube preview and fills Title and Channel from oEmbed. The
 * fetch can outlast the dialog (Cancel stays usable) and URL A then B can
 * resolve out of order: a result lands only in this opening, for the latest
 * request. Resolves to the details, or null.
 */
async function previewVideo(rawUrl: string): Promise<VideoMeta | null> {
  const url = String(rawUrl || '').trim()
  const opening = state.currentOpening()
  const seq = state.nextPreviewSeq()
  const still = () => state.isOpening(opening) && state.isPreviewSeq(seq)
  const known = state.getVideoMeta()
  if (url && url === state.getLastPreviewUrl().trim() && known) return known

  state.setVideoMeta(null)
  state.videoEmbedUrl.value = ''
  if (!url) {
    state.setLastPreviewUrl('')
    return null
  }
  const w = runtime()
  const videoId = typeof w.prksExtractYoutubeVideoId === 'function' ? w.prksExtractYoutubeVideoId(url) : ''
  if (videoId) state.videoEmbedUrl.value = `https://www.youtube.com/embed/${encodeURIComponent(videoId)}`

  try {
    if (!videoId) throw new Error('not a recognized YouTube URL')
    const res = await fetch(`https://www.youtube.com/oembed?format=json&url=${encodeURIComponent(url)}`, {
      method: 'GET',
    })
    if (!still()) return null
    if (res.ok) {
      const meta = (await res.json().catch(() => null)) as VideoMeta | null
      if (!still()) return null
      if (meta && typeof meta === 'object') {
        state.setVideoMeta(meta)
        if (!fieldValue('work-title').trim() && meta.title) setFieldValue('work-title', String(meta.title).trim())
        if (!fieldValue('work-video-channel').trim() && meta.author_name) {
          setFieldValue('work-video-channel', String(meta.author_name).trim())
        }
        props.onAutofilled()
      }
    }
  } catch {
    /* the preview is optional */
  }
  if (!still()) return state.getVideoMeta()
  state.setLastPreviewUrl(url)
  return state.getVideoMeta()
}

function isValidYoutubeUrl(url: string): boolean {
  const extract = runtime().prksExtractYoutubeVideoId
  return typeof extract === 'function' && !!extract(url)
}

function onVideoBlur(): void {
  const url = fieldValue('work-video-url').trim()
  if (url && !isValidYoutubeUrl(url)) state.errors.videoUrl = BAD_YOUTUBE_URL
  void previewVideo(url)
}

/** Focus target when the dialog opens: the source, unless a PDF is chosen. */
function initialControl(): HTMLElement | null {
  if (state.kind.value === 'video') return document.getElementById('work-video-url')
  if (state.pdf.value) return document.getElementById('work-title')
  return dropZone.value || document.getElementById('work-title')
}

function reset(): void {
  clearPdf()
  clearVideo()
  dragActive.value = false
  setKind('pdf', false)
}

defineExpose({ previewVideo, isValidYoutubeUrl, initialControl, reset })
</script>

<template>
  <section class="prks-create-section prks-create-section--source" aria-labelledby="work-create-source-heading">
    <div class="prks-create-section__head">
      <h4 id="work-create-source-heading" class="prks-create-section__title">Source</h4>
      <span class="prks-field__required">Required</span>
      <p id="work-file-kind-label" class="prks-sr-only">Source type</p>
      <input id="work-source-kind" type="hidden" :value="state.kind.value">
      <div class="prks-kind-toggle prks-kind-toggle--compact" role="tablist" aria-labelledby="work-file-kind-label">
        <button
          type="button"
          class="prks-kind-toggle__btn"
          :class="{ 'is-active': state.kind.value === 'pdf' }"
          data-kind="pdf"
          role="tab"
          :aria-selected="state.kind.value === 'pdf' ? 'true' : 'false'"
          @click="setKind('pdf')"
        >
          <span class="prks-kind-toggle__icon"><i data-lucide="file-text" class="prks-icon" aria-hidden="true"></i></span>
          <span class="prks-kind-toggle__label">PDF</span>
        </button>
        <button
          type="button"
          class="prks-kind-toggle__btn"
          :class="{ 'is-active': state.kind.value === 'video' }"
          data-kind="video"
          role="tab"
          :aria-selected="state.kind.value === 'video' ? 'true' : 'false'"
          @click="setKind('video')"
        >
          <span class="prks-kind-toggle__icon"><i data-lucide="play" class="prks-icon" aria-hidden="true"></i></span>
          <span class="prks-kind-toggle__label">YouTube</span>
        </button>
      </div>
    </div>

    <div id="upload-source-pdf" :class="{ hidden: state.kind.value !== 'pdf' }">
      <div
        id="upload-drop-zone"
        ref="dropZone"
        class="preview-pane"
        :class="{ 'preview-pane--compact': !!state.pdf.value, active: dragActive }"
        tabindex="0"
        role="button"
        aria-labelledby="drop-zone-label"
        aria-describedby="work-file-error"
        :aria-invalid="state.errors.file ? 'true' : undefined"
        @click="browse"
        @keydown="onZoneKeydown"
        @dragover.prevent="dragActive = true"
        @dragleave="dragActive = false"
        @drop.prevent="onDrop"
      >
        <div id="drop-zone-prompt" class="drop-zone drop-zone--inline" :class="{ hidden: !!state.pdf.value }">
          <span class="drop-zone__icon"><i data-lucide="file-text" class="prks-icon" aria-hidden="true"></i></span>
          <p id="drop-zone-label">Drop a PDF here <span class="drop-zone__label-sub">or click to browse · PDF files only</span></p>
        </div>
        <div id="upload-selected-file" class="upload-selected-file" :class="{ hidden: !state.pdf.value }">
          <span class="upload-selected-file__icon" aria-hidden="true"><i data-lucide="file-text" class="prks-icon"></i></span>
          <div class="upload-selected-file__meta">
            <p class="upload-selected-file__kind">PDF</p>
            <p id="upload-selected-file-name" class="upload-selected-file__name">{{ fileName }}</p>
            <p id="upload-selected-file-size" class="upload-selected-file__size">{{ fileSize }}</p>
          </div>
          <button
            id="upload-pdf-change-btn"
            type="button"
            class="prks-btn prks-btn--secondary prks-btn--sm"
            title="Choose a different PDF"
            @click.stop="clearPdf"
          >Change</button>
        </div>
        <input
          id="work-file"
          ref="fileInput"
          type="file"
          accept=".pdf,application/pdf"
          class="file-input-hidden"
          aria-label="PDF file"
          aria-required="true"
          :disabled="state.kind.value === 'video'"
          @click.stop
          @change="onFileChange"
        >
      </div>
      <p
        id="work-file-error"
        class="folder-field-error"
        :class="{ hidden: !state.errors.file }"
        data-prks-field-error="work-file"
        role="alert"
      >{{ state.errors.file }}</p>
    </div>

    <div id="work-video-url-row" class="prks-field" :class="{ hidden: state.kind.value !== 'video' }">
      <label for="work-video-url">YouTube URL</label>
      <input
        id="work-video-url"
        type="url"
        placeholder="https://www.youtube.com/watch?v=…"
        autocomplete="off"
        aria-required="true"
        aria-describedby="work-video-url-hint work-video-url-error"
        :aria-invalid="state.errors.videoUrl ? 'true' : undefined"
        @input="onVideoInput"
        @blur="onVideoBlur"
      >
      <p
        id="work-video-url-error"
        class="folder-field-error"
        :class="{ hidden: !state.errors.videoUrl }"
        data-prks-field-error="work-video-url"
        role="alert"
      >{{ state.errors.videoUrl }}</p>
      <p id="work-video-url-hint" class="meta-row meta-row--hint meta-row--compact">Title and channel are filled in from YouTube when possible.</p>
      <div id="upload-viewer" class="upload-video-preview" :class="{ hidden: !state.videoEmbedUrl.value }">
        <div v-if="state.videoEmbedUrl.value" class="prks-video-preview">
          <div class="prks-video-preview__frame">
            <iframe
              :src="state.videoEmbedUrl.value"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowfullscreen
            ></iframe>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
