<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import PrksButton from '../components/PrksButton.vue'
import { restoreConfirmFocus, tabbableIn } from './focus'
import type {
  ModalConfirmOptions,
  ModalPromptOptions,
  ModalUnsavedHideOptions,
  PrksModalHostApi,
} from './types'

interface ConfirmSession {
  mode: 'confirm' | 'prompt'
  title: string
  message: string
  messageHtml: string
  confirmLabel: string
  cancelLabel: string
  danger: boolean
  alertOnly: boolean
  multiline: boolean
  rows: number
}

const session = ref<ConfirmSession | null>(null)
const promptValue = ref('')
const unsavedOpen = ref(false)
const confirmPanel = ref<HTMLElement | null>(null)
const unsavedPanel = ref<HTMLElement | null>(null)
const promptField = ref<HTMLInputElement | HTMLTextAreaElement | null>(null)

let confirmResolve: ((value: boolean) => void) | null = null
let promptResolve: ((value: string | null) => void) | null = null
let confirmOpener: HTMLElement | null = null
let confirmOwnerTabId = ''
let unsavedOpener: HTMLElement | null = null
let unsavedDiscard: (() => void) | null = null

const confirmOpen = computed(() => session.value != null)
const promptMode = computed(() => session.value?.mode === 'prompt')
const alertOnly = computed(() => session.value?.alertOnly === true)
const danger = computed(() => session.value?.danger === true)
const titleText = computed(() => session.value?.title ?? 'Confirm')
const messageText = computed(() => (promptMode.value ? '' : (session.value?.message ?? '')))
const messageHtml = computed(() => {
  if (promptMode.value) return ''
  return session.value?.messageHtml ?? ''
})
const promptHint = computed(() => (promptMode.value ? (session.value?.message ?? '') : ''))
const confirmLabel = computed(() => session.value?.confirmLabel ?? 'OK')
const cancelLabel = computed(() => session.value?.cancelLabel ?? 'Cancel')
const promptRows = computed(() => session.value?.rows ?? 8)
const descClass = computed(() => ({
  'prks-modal-confirm__desc--rich': messageHtml.value !== '',
  'prks-modal-confirm__desc--prompt': promptMode.value,
}))

function text(value: unknown, fallback: string): string {
  return value != null ? String(value) : fallback
}

function rememberConfirmOpener(): void {
  const active = document.activeElement
  confirmOpener = active instanceof HTMLElement && active !== document.body ? active : null
  confirmOwnerTabId = ''
  const drawer = confirmOpener?.closest('[data-prks-role="pdf-annotation-drawer"]')
  const ownerTabId = drawer?.getAttribute('data-prks-owner-tab-id')
  if (ownerTabId) confirmOwnerTabId = ownerTabId
}

function displace(): void {
  const confirm = confirmResolve
  const prompt = promptResolve
  confirmResolve = null
  promptResolve = null
  confirmOpener = null
  confirmOwnerTabId = ''
  session.value = null
  promptValue.value = ''
  if (confirm) confirm(false)
  if (prompt) prompt(null)
}

function queueFocus(pick: () => HTMLElement | null, selectText = false): void {
  void nextTick(() => {
    requestAnimationFrame(() => {
      const el = pick()
      if (!el) return
      el.focus()
      if (selectText && el instanceof HTMLInputElement) el.select()
    })
  })
}

function confirmFocusTarget(): HTMLElement | null {
  if (promptMode.value) return promptField.value
  const id = alertOnly.value || !danger.value
    ? 'prks-modal-confirm-ok'
    : 'prks-modal-confirm-cancel'
  const el = document.getElementById(id)
  return el instanceof HTMLElement ? el : null
}

function confirm(options: ModalConfirmOptions = {}): Promise<boolean> {
  return new Promise((resolve) => {
    displace()
    rememberConfirmOpener()
    session.value = {
      mode: 'confirm',
      title: text(options.title, 'Confirm'),
      message: text(options.message, ''),
      messageHtml: text(options.messageHtml, ''),
      confirmLabel: text(options.confirmLabel, 'OK'),
      cancelLabel: text(options.cancelLabel, 'Cancel'),
      danger: options.danger === true,
      alertOnly: options.alertOnly === true,
      multiline: false,
      rows: 8,
    }
    confirmResolve = resolve
    queueFocus(confirmFocusTarget)
  })
}

function prompt(options: ModalPromptOptions = {}): Promise<string | null> {
  return new Promise((resolve) => {
    displace()
    rememberConfirmOpener()
    const rows = options.rows != null ? Number(options.rows) || 8 : 8
    promptValue.value = text(options.defaultValue, '')
    session.value = {
      mode: 'prompt',
      title: text(options.title, 'Edit'),
      message: text(options.message, ''),
      messageHtml: '',
      confirmLabel: text(options.okLabel, 'Save'),
      cancelLabel: text(options.cancelLabel, 'Cancel'),
      danger: false,
      alertOnly: false,
      multiline: options.multiline === true,
      rows,
    }
    promptResolve = resolve
    queueFocus(confirmFocusTarget, options.multiline !== true)
  })
}

function finish(confirmed: boolean): void {
  const mode = session.value?.mode
  const textValue = confirmed ? promptValue.value.trim() : null
  const opener = confirmOpener
  const ownerTabId = confirmOwnerTabId
  const confirmDone = confirmResolve
  const promptDone = promptResolve
  confirmResolve = null
  promptResolve = null
  confirmOpener = null
  confirmOwnerTabId = ''
  session.value = null
  promptValue.value = ''
  restoreConfirmFocus(opener, ownerTabId)
  if (mode === 'prompt') {
    if (promptDone) promptDone(confirmed ? textValue : null)
    return
  }
  if (confirmDone) confirmDone(confirmed)
}

function dismissConfirm(): void {
  finish(session.value?.alertOnly === true)
}

function onPromptKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey) return
  if (session.value?.multiline) return
  event.preventDefault()
  finish(true)
}

function openUnsavedConfirm(onDiscard: (() => void) | null): void {
  unsavedDiscard = typeof onDiscard === 'function' ? onDiscard : null
  const active = document.activeElement
  unsavedOpener = active instanceof HTMLElement && active !== document.body ? active : null
  unsavedOpen.value = true
  queueFocus(() => {
    const el = document.getElementById('prks-modal-unsaved-confirm-cancel')
    return el instanceof HTMLElement ? el : null
  })
}

function hideUnsavedConfirm(options?: ModalUnsavedHideOptions): void {
  const restore = !options || options.restoreFocus !== false
  const opener = unsavedOpener
  unsavedOpener = null
  unsavedDiscard = null
  unsavedOpen.value = false
  if (!restore || !opener || opener === document.body || !document.contains(opener)) return
  const root = document.getElementById('prks-modal-unsaved-confirm')
  if (root?.contains(opener)) return
  try {
    opener.focus({ preventScroll: true })
  } catch {
    /* A detached opener can reject focus. */
  }
}

function discardUnsaved(): void {
  const fn = unsavedDiscard
  hideUnsavedConfirm({ restoreFocus: false })
  if (fn) fn()
}

function stopKey(event: KeyboardEvent): void {
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()
}

function topLayer(): HTMLElement | null {
  if (session.value && confirmPanel.value) return confirmPanel.value
  if (unsavedOpen.value && unsavedPanel.value) return unsavedPanel.value
  const modal = document.querySelector('.modal:not(.hidden)')
  return modal instanceof HTMLElement ? modal : null
}

function trapTab(event: KeyboardEvent): void {
  const layer = topLayer()
  if (!layer) return
  const items = tabbableIn(layer)
  if (!items.length) {
    stopKey(event)
    return
  }
  const first = items[0]
  const last = items[items.length - 1]
  if (!first || !last) return
  const active = document.activeElement
  const outside = !(active instanceof Node) || !layer.contains(active)
  if (event.shiftKey) {
    if (outside || active === first) {
      stopKey(event)
      last.focus()
    }
    return
  }
  if (outside || active === last) {
    stopKey(event)
    first.focus()
  }
}

/**
 * Topmost layer wins. Confirm and the unsaved prompt consume Escape and leave
 * the feature modal open. An inner menu, then a standalone page dialog, then
 * the classic dirty-close policy.
 */
function handleEscape(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || event.isComposing) return
  if (document.body.classList.contains('prks-workspace-dragging')) return
  if (session.value) {
    stopKey(event)
    dismissConfirm()
    return
  }
  if (unsavedOpen.value) {
    stopKey(event)
    hideUnsavedConfirm()
    return
  }
  const dismissInner = window.prksDismissModalInnerEscapeLayer
  if (typeof dismissInner === 'function' && dismissInner()) {
    stopKey(event)
    return
  }
  const active = document.querySelector('.modal:not(.hidden)')
  if (!(active instanceof HTMLElement)) return
  stopKey(event)
  const closeStandalone = window.prksCloseStandalonePageModal
  if (typeof closeStandalone === 'function' && closeStandalone(active)) return
  window.requestModalClose?.('escape')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Tab') {
    trapTab(event)
    return
  }
  handleEscape(event)
}

const api: PrksModalHostApi = {
  confirm,
  prompt,
  openUnsavedConfirm,
  hideUnsavedConfirm,
  isConfirmOpen: () => session.value != null,
  isUnsavedConfirmOpen: () => unsavedOpen.value,
  handleEscape,
}

onMounted(() => {
  window.prksModalHost = api
  document.addEventListener('keydown', onKeydown, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown, true)
  if (window.prksModalHost === api) delete window.prksModalHost
})
</script>

<template>
  <Teleport to="body">
    <div
      id="prks-modal-unsaved-confirm"
      class="prks-modal-unsaved-confirm"
      :class="{ hidden: !unsavedOpen }"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="prks-modal-unsaved-confirm-title"
      aria-describedby="prks-modal-unsaved-confirm-desc"
      :aria-hidden="unsavedOpen ? 'false' : 'true'"
    >
      <div
        id="prks-modal-unsaved-confirm-scrim"
        class="prks-modal-unsaved-confirm__scrim"
        aria-hidden="true"
        @click="hideUnsavedConfirm()"
      ></div>
      <div ref="unsavedPanel" class="prks-modal-unsaved-confirm__panel">
        <h3 id="prks-modal-unsaved-confirm-title" class="prks-modal-unsaved-confirm__title">Close without saving?</h3>
        <p id="prks-modal-unsaved-confirm-desc" class="prks-modal-unsaved-confirm__desc">
          Are you sure you want to close this window without saving the changes?
        </p>
        <div class="prks-modal-unsaved-confirm__actions">
          <PrksButton id="prks-modal-unsaved-confirm-cancel" variant="secondary" @click="hideUnsavedConfirm()">
            Cancel
          </PrksButton>
          <PrksButton id="prks-modal-unsaved-confirm-discard" variant="danger" @click="discardUnsaved">
            Close without saving
          </PrksButton>
        </div>
      </div>
    </div>

    <div
      id="prks-modal-confirm"
      class="prks-modal-confirm"
      :class="{ hidden: !confirmOpen }"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="prks-modal-confirm-title"
      aria-describedby="prks-modal-confirm-desc"
      :aria-hidden="confirmOpen ? 'false' : 'true'"
    >
      <div
        id="prks-modal-confirm-scrim"
        class="prks-modal-confirm__scrim"
        aria-hidden="true"
        @click="dismissConfirm"
      ></div>
      <div ref="confirmPanel" class="prks-modal-confirm__panel">
        <h3 id="prks-modal-confirm-title" class="prks-modal-confirm__title">{{ titleText }}</h3>
        <p id="prks-modal-confirm-desc" class="prks-modal-confirm__desc" :class="descClass">
          <template v-if="promptMode">
            <span v-if="promptHint" class="prks-modal-prompt__hint">{{ promptHint }}</span>
            <textarea
              v-if="session?.multiline"
              ref="promptField"
              v-model="promptValue"
              class="prks-modal-prompt__input prks-modal-prompt__input--multiline"
              :rows="promptRows"
              :aria-label="titleText"
              autocomplete="off"
              @keydown="onPromptKeydown"
            ></textarea>
            <input
              v-else
              ref="promptField"
              v-model="promptValue"
              type="text"
              class="prks-modal-prompt__input"
              :aria-label="titleText"
              autocomplete="off"
              @keydown="onPromptKeydown"
            />
          </template>
          <span v-else-if="messageHtml" v-html="messageHtml"></span>
          <template v-else>{{ messageText }}</template>
        </p>
        <div
          class="prks-modal-confirm__actions"
          :class="{ 'prks-modal-confirm__actions--alertOnly': alertOnly }"
        >
          <PrksButton
            id="prks-modal-confirm-cancel"
            variant="secondary"
            :class="{ hidden: alertOnly }"
            :aria-hidden="alertOnly ? 'true' : 'false'"
            @click="dismissConfirm"
          >
            {{ cancelLabel }}
          </PrksButton>
          <PrksButton
            id="prks-modal-confirm-ok"
            :variant="danger ? 'danger' : 'primary'"
            @click="finish(true)"
          >
            {{ confirmLabel }}
          </PrksButton>
        </div>
      </div>
    </div>
  </Teleport>
</template>
