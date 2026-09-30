<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { bindFloatingPosition } from '../../floating/bind-floating-position'
import { popupDraftForSession, type PopupDraftIdentity } from './pdf-annotation-popup-draft'
import {
  annotationPopupEscapeYields,
  focusAfterAnnotationPopupClose,
  rememberAnnotationPopupOpener,
} from './pdf-annotation-popup-focus'

export interface AnnotationPopupView {
  open: boolean
  annId: string
  epoch: number
  comment: string
  meta: string
  pageIndex: number | null
  generation: number | null
  deletable: boolean
}

const props = defineProps<{
  state: AnnotationPopupView
  tabId: string
  anchor: () => HTMLElement | null
  boundary: () => HTMLElement | null
  onSave: (text: string, ticket: { generation: number; annId: string; epoch: number }) => void
  onClose: (ticket: { generation: number; annId: string; epoch: number }) => void
  onDelete: (ticket: { generation: number; annId: string; epoch: number }) => void
}>()

const draft = ref('')
const previous = ref<PopupDraftIdentity | null>(null)
const floatingRef = ref<HTMLElement | null>(null)
const textRef = ref<HTMLTextAreaElement | null>(null)
let stopPosition = (): void => {}
let anchorObserver: MutationObserver | null = null
let observedRoot: Element | null = null
let boundAnchor: Element | null = null
let opener: HTMLElement | null = null
let focusRestored = false

function ticket() {
  return {
    generation: typeof props.state.generation === 'number' ? props.state.generation : Number.NaN,
    annId: props.state.annId,
    epoch: props.state.epoch,
  }
}

function viewerHost(): HTMLElement | null {
  const pane = floatingRef.value?.closest('.work-pdf-pane')
  const viewer = pane?.querySelector('[data-prks-role="pdf-viewer"]')
  return viewer instanceof HTMLElement ? viewer : null
}

function rememberOpener() {
  focusRestored = false
  opener = rememberAnnotationPopupOpener()
}

function restoreOpener() {
  if (focusRestored) return
  focusRestored = true
  focusAfterAnnotationPopupClose(opener, viewerHost())
  opener = null
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.isComposing || !props.state.open) return
  if (annotationPopupEscapeYields()) return
  event.preventDefault()
  event.stopPropagation()
  props.onClose(ticket())
}

watch(
  () => [props.state.open, props.state.annId, props.state.epoch, props.state.comment] as const,
  () => {
    const next = props.state
    const wasOpen = previous.value != null
    const identityChanged =
      !previous.value || previous.value.annId !== next.annId || previous.value.epoch !== next.epoch
    if (!wasOpen && next.open) rememberOpener()
    draft.value = popupDraftForSession(previous.value, next, draft.value)
    previous.value = next.open ? { annId: next.annId, epoch: next.epoch } : null
    if (wasOpen && !next.open) restoreOpener()
    if (identityChanged && next.open) {
      void nextTick(() => {
        textRef.value?.focus()
      })
    }
  },
  { immediate: true },
)

function releasePosition() {
  stopPosition()
  stopPosition = () => {}
  boundAnchor = null
}

function disconnectAnchorWatch() {
  if (anchorObserver) anchorObserver.disconnect()
  anchorObserver = null
  observedRoot = null
}

function bindAnchor(reference: Element) {
  if (!floatingRef.value || reference === boundAnchor) return
  stopPosition()
  boundAnchor = reference
  stopPosition = bindFloatingPosition(reference, floatingRef.value, props.boundary())
}

/**
 * The list can open the popup before the virtualized page mounts the anchor.
 * Watch the pane until that node exists, and re-bind if a later mount
 * replaces it. The watch ends when the popup closes or this surface unmounts.
 */
function syncAnchor() {
  if (!props.state.open || !floatingRef.value) {
    releasePosition()
    disconnectAnchorWatch()
    return
  }
  const reference = props.anchor()
  if (reference) bindAnchor(reference)
  else releasePosition()
  const root = props.boundary()
  if (!(root instanceof Element)) return
  if (root === observedRoot && anchorObserver) return
  disconnectAnchorWatch()
  anchorObserver = new MutationObserver(() => {
    syncAnchor()
  })
  anchorObserver.observe(root, { childList: true, subtree: true })
  observedRoot = root
}

watch(
  () => [props.state.open, props.state.annId, props.state.epoch, floatingRef.value] as const,
  () => {
    boundAnchor = null
    syncAnchor()
  },
  { flush: 'post' },
)

watch(
  () => props.state.open,
  (open) => {
    if (open) document.addEventListener('keydown', onDocumentKeydown)
    else document.removeEventListener('keydown', onDocumentKeydown)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDocumentKeydown)
  releasePosition()
  disconnectAnchorWatch()
  if (previous.value) restoreOpener()
})
</script>

<template>
  <div
    v-if="state.open"
    ref="floatingRef"
    class="pdf-annotation-popup"
    role="dialog"
    aria-label="Annotation comment"
    data-prks-role="pdf-annotation-popup"
    data-no-interaction=""
    :data-prks-owner-tab-id="tabId"
    :data-prks-annotation-id="state.annId"
    :data-prks-popup-epoch="state.epoch"
    @pointerdown.stop
  >
    <div class="pdf-annotation-popup__meta">{{ state.meta }}</div>
    <label class="pdf-annotation-popup__label">
      Comment
      <textarea
        ref="textRef"
        v-model="draft"
        class="textarea-md pdf-annotation-popup__text"
        data-prks-role="pdf-annotation-popup-text"
        placeholder="Add a note/comment for this annotation…"
      ></textarea>
    </label>
    <div class="pdf-annotation-popup__actions">
      <button type="button" class="prks-btn prks-btn--secondary" @click="onClose(ticket())">Cancel</button>
      <button v-if="state.deletable" type="button" class="prks-btn prks-btn--danger" @click="onDelete(ticket())">Delete annotation</button>
      <button type="button" class="prks-btn prks-btn--primary" @click="onSave(draft, ticket())">Save comment</button>
    </div>
  </div>
</template>
