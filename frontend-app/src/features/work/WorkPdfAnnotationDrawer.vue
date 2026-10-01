<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  annotationPopupEscapeYields,
  focusAfterAnnotationPopupClose,
  rememberAnnotationPopupOpener,
} from './pdf-annotation-popup-focus'
import type { WorkPdfAnnotationDrawerItem } from './pdf-adapter'

export interface AnnotationDrawerView {
  open: boolean
  epoch: number
  viewerToken: number
  selectedId: string
  status: string
  published: boolean
  items: WorkPdfAnnotationDrawerItem[]
  generation: number | null
  pinned?: boolean
  width?: number
  minWidth?: number
  maxWidth?: number
  defaultWidth?: number
  placement?: 'closed' | 'overlay' | 'pinned' | 'sheet'
  pinEnabled?: boolean
}

const props = defineProps<{
  state: AnnotationDrawerView
  tabId: string
  onClose: (ticket: { generation: number; epoch: number; viewerToken: number }) => void
  onJump: (annId: string, ticket: { generation: number; epoch: number; viewerToken: number }) => void
  onEdit: (annId: string, ticket: { generation: number; epoch: number; viewerToken: number }) => void
  onDelete: (
    annId: string,
    ticket: { generation: number; epoch: number; viewerToken: number },
  ) => Promise<boolean> | boolean | void
  onCopy: (
    annId: string,
    ticket: { generation: number; epoch: number; viewerToken: number },
  ) => Promise<boolean>
  onPin?: (pinned: boolean, ticket: { generation: number; epoch: number; viewerToken: number }) => void
  onResize?: (
    width: number,
    ticket: { generation: number; epoch: number; viewerToken: number },
    options: { persist: boolean },
  ) => void
}>()

const panelRef = ref<HTMLElement | null>(null)
const closeRef = ref<HTMLButtonElement | null>(null)
const resizeRef = ref<HTMLElement | null>(null)
const wasOpen = ref(false)
let opener: HTMLElement | null = null
let focusRestored = false

function ticket() {
  return {
    generation: typeof props.state.generation === 'number' ? props.state.generation : Number.NaN,
    epoch: props.state.epoch,
    viewerToken: props.state.viewerToken,
  }
}

function widthBounds() {
  const min = props.state.minWidth || 240
  const max = props.state.maxWidth || 480
  return { min, max, fallback: props.state.defaultWidth || 352 }
}

function clampWidth(width: number) {
  const bounds = widthBounds()
  return Math.min(bounds.max, Math.max(bounds.min, Math.round(width)))
}

function pinDrawer() {
  if (!props.state.pinEnabled && !props.state.pinned) return
  props.onPin?.(!props.state.pinned, ticket())
}

let drag: { pointer: number; startX: number; startWidth: number; latest: number } | null = null

function onResizePointerDown(event: PointerEvent) {
  if (props.state.placement === 'sheet') return
  const handle = event.currentTarget
  if (!(handle instanceof HTMLElement)) return
  const startWidth = props.state.width || widthBounds().fallback
  drag = {
    pointer: event.pointerId,
    startX: event.clientX,
    startWidth,
    latest: startWidth,
  }
  handle.setPointerCapture(event.pointerId)
  event.preventDefault()
}

function onResizePointerMove(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointer) return
  // The handle is on the drawer's leading edge. Dragging left widens it.
  const next = clampWidth(drag.startWidth + (drag.startX - event.clientX))
  drag.latest = next
  props.onResize?.(next, ticket(), { persist: false })
}

function onResizePointerUp(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointer) return
  const handle = resizeRef.value
  const latest = drag.latest
  if (handle && handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId)
  drag = null
  props.onResize?.(latest, ticket(), { persist: true })
}

function onResizeKeydown(event: KeyboardEvent) {
  if (props.state.placement === 'sheet') return
  const bounds = widthBounds()
  const current = props.state.width || bounds.fallback
  const step = event.shiftKey ? 48 : 16
  let next: number | null = null
  if (event.key === 'ArrowLeft') next = current + step
  else if (event.key === 'ArrowRight') next = current - step
  else if (event.key === 'Home') next = bounds.min
  else if (event.key === 'End') next = bounds.max
  else if (event.key === 'Enter') next = bounds.fallback
  if (next == null) return
  event.preventDefault()
  props.onResize?.(clampWidth(next), ticket(), { persist: true })
}

function viewerHost(): HTMLElement | null {
  const pane = panelRef.value?.closest('.work-pdf-pane')
  const viewer = pane?.querySelector('[data-prks-role="pdf-viewer"]')
  return viewer instanceof HTMLElement ? viewer : null
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
  const host = panelRef.value
  const target = event.target
  if (!host || !(target instanceof Node) || !host.contains(target)) return
  event.preventDefault()
  event.stopPropagation()
  props.onClose(ticket())
}

function deleteFocusAlreadyRestored(): boolean {
  const active = document.activeElement
  return active instanceof HTMLElement && active.classList.contains('annotation-row__delete')
}

async function deleteAnnotation(event: MouseEvent, annId: string) {
  const button = event.currentTarget
  const busy = (window as Window & {
    prksSetButtonBusy?: (
      button: HTMLButtonElement,
      busy: boolean,
      options?: { busyLabel?: string },
    ) => void
  }).prksSetButtonBusy
  // The confirm dialog records document.activeElement synchronously. Disabling
  // this focused button first would move focus away and capture the wrong opener.
  const pending = props.onDelete(annId, ticket())
  if (button instanceof HTMLButtonElement && typeof busy === 'function') {
    busy(button, true, { busyLabel: 'Deleting…' })
  }
  try {
    await pending
  } finally {
    if (button instanceof HTMLButtonElement && typeof busy === 'function') {
      busy(button, false)
    }
    // Cancel restores the opener while this button is still disabled, and a
    // disabled control cannot take focus. Focus it once it is idle again,
    // unless a repaint already focused the owner-scoped replacement.
    if (button instanceof HTMLButtonElement && button.isConnected && !deleteFocusAlreadyRestored()) {
      focusAfterAnnotationPopupClose(button, null)
    }
  }
}

async function copyLink(event: MouseEvent, annId: string) {
  const button = event.currentTarget
  const ok = await props.onCopy(annId, ticket())
  if (!(button instanceof HTMLButtonElement)) return
  const flash = (window as Window & {
    prksFlashButtonLabel?: (
      button: HTMLButtonElement,
      ok: boolean,
      options: { successLabel: string; errorLabel: string; restoreMs: number },
    ) => void
  }).prksFlashButtonLabel
  if (typeof flash === 'function') {
    flash(button, ok, { successLabel: 'Copied', errorLabel: 'Copy failed', restoreMs: 1200 })
  }
}

watch(
  () => props.state.open,
  (open) => {
    if (open && !wasOpen.value) {
      focusRestored = false
      opener = rememberAnnotationPopupOpener()
      document.addEventListener('keydown', onDocumentKeydown)
      void nextTick(() => {
        if (!props.state.open) return
        closeRef.value?.focus()
      })
    } else if (!open && wasOpen.value) {
      document.removeEventListener('keydown', onDocumentKeydown)
      restoreOpener()
    }
    wasOpen.value = open
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDocumentKeydown)
  if (wasOpen.value) restoreOpener()
})
</script>

<template>
  <aside
    v-if="state.open"
    ref="panelRef"
    class="pdf-annotation-drawer"
    :role="state.placement === 'pinned' ? 'complementary' : 'dialog'"
    aria-label="PDF annotations"
    data-prks-role="pdf-annotation-drawer"
    data-no-interaction=""
    :data-prks-owner-tab-id="tabId"
    :data-prks-drawer-epoch="state.epoch"
    :data-prks-drawer-placement="state.placement || 'overlay'"
    :data-prks-list-published="state.published ? 'true' : 'false'"
    @pointerdown.stop
    @pointermove="onResizePointerMove"
    @pointerup="onResizePointerUp"
    @pointercancel="onResizePointerUp"
  >
    <div
      v-if="state.placement !== 'sheet'"
      ref="resizeRef"
      class="pdf-annotation-drawer__resize"
      role="separator"
      aria-orientation="vertical"
      tabindex="0"
      aria-label="Annotation list width"
      :aria-valuemin="state.minWidth || 240"
      :aria-valuemax="state.maxWidth || 480"
      :aria-valuenow="state.width || state.defaultWidth || 352"
      :aria-valuetext="`${state.width || state.defaultWidth || 352} pixels`"
      @pointerdown="onResizePointerDown"
      @keydown="onResizeKeydown"
    />
    <header class="pdf-annotation-drawer__header">
      <h3 class="pdf-annotation-drawer__title">Annotations</h3>
      <button
        type="button"
        class="prks-btn prks-btn--secondary prks-btn--sm"
        :aria-pressed="state.pinned ? 'true' : 'false'"
        :disabled="!state.pinEnabled && !state.pinned"
        :title="state.pinned ? 'Unpin annotations' : (state.pinEnabled ? 'Pin annotations' : 'Pin needs a wider pane')"
        @click="pinDrawer()"
      >
        {{ state.pinned ? 'Unpin' : 'Pin' }}
      </button>
      <button
        ref="closeRef"
        type="button"
        class="prks-btn prks-btn--secondary prks-btn--sm"
        @click="onClose(ticket())"
      >
        Close
      </button>
    </header>
    <div class="annotation-list-status">{{ state.status }}</div>
    <div class="pdf-annotation-drawer__list annotation-fallback-list" role="list">
      <p v-if="state.items.length === 0" class="annotations-tab__empty">No annotations loaded yet.</p>
      <div
        v-for="item in state.items"
        :key="item.id"
        class="annotation-row"
        role="listitem"
        tabindex="0"
        :data-ann-id="item.id"
        :data-selected="state.selectedId === item.id ? 'true' : 'false'"
      >
        <div class="annotation-row__header">
          <button type="button" class="annotation-row__page-jump" @click="onJump(item.id, ticket())">
            {{ item.pageLabel }}
          </button>
          <button
            type="button"
            class="annotation-row__copy-link"
            title="Copy link to this PDF annotation for your notes"
            @click="copyLink($event, item.id)"
          >Copy link</button>
          <button type="button" class="annotation-row__edit-comment" @click="onEdit(item.id, ticket())">
            Edit/Add comment
          </button>
          <button type="button" class="annotation-row__delete" @click="deleteAnnotation($event, item.id)">
            Delete
          </button>
        </div>
        <button type="button" class="annotation-row__jump" @click="onJump(item.id, ticket())">
          <span class="annotation-row__text">{{ item.text }}</span>
        </button>
        <div v-if="item.comment" class="annotation-row__comment">{{ item.comment }}</div>
        <div v-if="item.metadataLabels.length" class="annotation-row__metadata">
          <span v-for="label in item.metadataLabels" :key="label">{{ label }}</span>
        </div>
      </div>
    </div>
  </aside>
</template>
