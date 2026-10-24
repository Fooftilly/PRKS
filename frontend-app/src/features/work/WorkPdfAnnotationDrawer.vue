<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
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
  layoutWidth?: number
  minWidth?: number
  maxWidth?: number
  interactionMax?: number
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
    options: { persist: boolean; preview?: boolean; cancel?: boolean },
  ) => void
}>()

const panelRef = ref<HTMLElement | null>(null)
const closeRef = ref<{ focus: () => void } | null>(null)
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

function displayedWidth() {
  const layout = props.state.layoutWidth
  if (typeof layout === 'number' && Number.isFinite(layout)) return layout
  return props.state.width || props.state.defaultWidth || 352
}

function interactionMax() {
  const max = props.state.interactionMax
  if (typeof max === 'number' && Number.isFinite(max)) return max
  return props.state.maxWidth || 480
}

function actuallyPinned() {
  return props.state.placement === 'pinned'
}

function pinDrawer() {
  const pinnedNow = actuallyPinned()
  if (!pinnedNow && !props.state.pinEnabled) return
  const captured = ticket()
  props.onPin?.(!pinnedNow, captured)
}

interface DrawerWidthTicket {
  generation: number
  epoch: number
  viewerToken: number
}

type DrawerWidthBinding = {
  release: () => void
  refresh: () => void
}

let drawerWidthBinding: DrawerWidthBinding | null = null

function clearDrawerWidth() {
  if (!drawerWidthBinding) return
  const release = drawerWidthBinding.release
  drawerWidthBinding = null
  release()
}

function refreshDrawerWidth() {
  drawerWidthBinding?.refresh()
}

function bindDrawerWidth(el: unknown) {
  clearDrawerWidth()
  if (!(el instanceof HTMLElement)) return
  const bind = (
    window as Window & {
      prksBindDrawerWidthSeparator?: (
        element: HTMLElement,
        cfg: {
          getWidth: () => number
          getMin: () => number
          getMax: () => number
          getDefault: () => number
          capture: () => DrawerWidthTicket
          onPreview: (width: number, captured: DrawerWidthTicket) => void
          onCommit: (width: number, captured: DrawerWidthTicket) => void
          onCancel: (width: number, captured: DrawerWidthTicket) => void
        },
      ) => DrawerWidthBinding
    }
  ).prksBindDrawerWidthSeparator
  if (typeof bind !== 'function') return
  const binding = bind(el, {
    getWidth: () => displayedWidth(),
    getMin: () => props.state.minWidth || 240,
    getMax: () => interactionMax(),
    getDefault: () => props.state.defaultWidth || 352,
    capture: () => ticket(),
    onPreview: (width, captured) => {
      props.onResize?.(width, captured, { persist: false, preview: true })
    },
    onCommit: (width, captured) => {
      props.onResize?.(width, captured, { persist: true })
    },
    onCancel: (width, captured) => {
      props.onResize?.(width, captured, { persist: false, cancel: true })
    },
  })
  if (!binding || typeof binding.release !== 'function' || typeof binding.refresh !== 'function') return
  drawerWidthBinding = binding
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

watch(resizeRef, (el) => {
  bindDrawerWidth(el)
}, { flush: 'post' })

watch(
  () => [
    props.state.layoutWidth,
    props.state.interactionMax,
    props.state.minWidth,
    props.state.maxWidth,
  ],
  () => {
    refreshDrawerWidth()
  },
)

onBeforeUnmount(() => {
  clearDrawerWidth()
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
  >
    <div
      v-if="state.placement !== 'sheet'"
      ref="resizeRef"
      class="pdf-annotation-drawer__resize"
    />
    <header class="pdf-annotation-drawer__header">
      <h3 class="pdf-annotation-drawer__title">Annotations</h3>
      <PrksButton
        size="sm"
        :aria-pressed="actuallyPinned() ? 'true' : 'false'"
        :disabled="!actuallyPinned() && !state.pinEnabled"
        :title="actuallyPinned() ? 'Unpin annotations' : (state.pinEnabled ? 'Pin annotations' : 'Pin needs a wider pane')"
        @click="pinDrawer()"
      >
        {{ actuallyPinned() ? 'Unpin' : 'Pin' }}
      </PrksButton>
      <PrksButton
        ref="closeRef"
        size="sm"
        @click="onClose(ticket())"
      >
        Close
      </PrksButton>
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
