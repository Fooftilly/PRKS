<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import PrksButton from '../components/PrksButton.vue'
import PrksIconButton from '../components/PrksIconButton.vue'
import { CREATE_ACTIONS, paletteShortcutHint, useAppChrome } from './chrome'
import ShellIcon from './ShellIcon.vue'

const chrome = useAppChrome()

const open = ref(false)
const menuIndex = ref(0)
const chevron = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const shortcutHint = paletteShortcutHint()

const online = computed(() => {
  const state = chrome.value.connectivity
  return state !== 'offline' && state !== 'reconnecting'
})
const connectivityLabel = computed(() =>
  chrome.value.connectivity === 'reconnecting' ? 'Reconnecting…' : 'Offline',
)
const syncQueue = computed(() => chrome.value.syncQueue)
const syncVisible = computed(() => {
  const queue = syncQueue.value
  return !!queue && queue.count > 0
})
const syncBadge = computed(() => {
  const queue = syncQueue.value
  if (!queue || queue.count <= 0) return ''
  return window.prksNavAttentionBadgeHtml?.({ count: queue.count, ariaLabel: queue.ariaLabel }) ?? ''
})

function itemButtons(): HTMLButtonElement[] {
  const root = menu.value
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))
}

function positionMenu(): void {
  const node = menu.value
  const anchor = chevron.value
  if (!node || !anchor) return
  const rect = anchor.getBoundingClientRect()
  const vw = document.documentElement?.clientWidth || window.innerWidth || 800
  const vh = document.documentElement?.clientHeight || window.innerHeight || 600
  const mw = node.offsetWidth || 200
  const mh = node.offsetHeight || 160
  let x = rect.left
  let y = rect.bottom
  if (x + mw > vw - 8) x = Math.max(8, vw - mw - 8)
  if (y + mh > vh - 8) y = Math.max(8, rect.top - mh)
  if (x < 8) x = 8
  if (y < 8) y = 8
  node.style.left = Math.round(x) + 'px'
  node.style.top = Math.round(y) + 'px'
}

function setActive(index: number): void {
  const items = itemButtons()
  if (!items.length) return
  let next = index
  if (next < 0) next = items.length - 1
  if (next >= items.length) next = 0
  menuIndex.value = next
  items.forEach((btn, n) => {
    btn.classList.toggle('is-active', n === next)
    btn.tabIndex = n === next ? 0 : -1
  })
  items[next]?.focus()
}

function closeMenu(restore = true): void {
  if (!open.value) return
  open.value = false
  const target = chevron.value
  if (restore && target && document.contains(target)) target.focus({ preventScroll: true })
}

async function openMenu(): Promise<void> {
  if (open.value) {
    closeMenu()
    return
  }
  open.value = true
  menuIndex.value = 0
  await nextTick()
  window.prksRefreshIcons?.(menu.value)
  positionMenu()
  setActive(0)
}

function runAction(modalId: string): void {
  closeMenu()
  window.openModal?.(modalId)
}

function newFile(): void {
  if (open.value) closeMenu(false)
  window.openModal?.('work-modal')
}

function onChevronClick(event: MouseEvent): void {
  event.preventDefault()
  event.stopPropagation()
  void openMenu()
}

function onChevronKey(event: KeyboardEvent): void {
  if (event.key !== 'ArrowDown' && event.key !== 'Enter' && event.key !== ' ') return
  if (open.value) return
  event.preventDefault()
  void openMenu()
}

function onDocPointer(event: Event): void {
  if (!open.value) return
  const target = event.target
  if (!(target instanceof Node)) return
  if (menu.value?.contains(target)) return
  if (chevron.value && (chevron.value === target || chevron.value.contains(target))) return
  closeMenu()
}

function onDocKey(event: KeyboardEvent): void {
  if (!open.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeMenu()
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    setActive(menuIndex.value + 1)
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    setActive(menuIndex.value - 1)
    return
  }
  if (event.key === 'Home') {
    event.preventDefault()
    setActive(0)
    return
  }
  if (event.key === 'End') {
    event.preventDefault()
    setActive(itemButtons().length - 1)
    return
  }
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    const items = itemButtons()
    const focused = event.target instanceof HTMLButtonElement && items.includes(event.target)
      ? event.target
      : items[menuIndex.value]
    const id = focused?.getAttribute('data-create-id')
    const action = CREATE_ACTIONS.find((item) => item.id === id)
    if (action) runAction(action.modalId)
  }
}

watch(open, (isOpen) => {
  if (isOpen) {
    document.addEventListener('pointerdown', onDocPointer, true)
    document.addEventListener('keydown', onDocKey, true)
    return
  }
  document.removeEventListener('pointerdown', onDocPointer, true)
  document.removeEventListener('keydown', onDocKey, true)
})

onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocPointer, true)
  document.removeEventListener('keydown', onDocKey, true)
})

function openPalette(): void {
  window.prksOpenCommandPalette?.({ scope: 'all' })
}

function toggleSidebar(): void {
  window.prksToggleSidebarDrawer?.()
}

function toggleDetails(): void {
  window.prksToggleRightPanelOverlay?.()
}

function openSyncDiagnostics(): void {
  window.openModal?.('settings-modal')
  const diag = document.querySelector('[data-prks-settings-category="diagnostics"]')
  if (diag instanceof HTMLElement) diag.click()
}
</script>

<template>
  <div class="top-ribbon__left">
    <PrksIconButton
      id="prks-mobile-nav-btn"
      variant="ghost"
      label="Open navigation"
      title="Menu"
      aria-controls="sidebar"
      :aria-expanded="chrome.sidebarOpen ? 'true' : 'false'"
      @click="toggleSidebar"
    >
      <ShellIcon class="ribbon-btn__icon" name="menu" size="ribbon" />
      <span class="prks-sr-only">Menu</span>
    </PrksIconButton>
  </div>
  <div class="top-ribbon__center" aria-label="Quick actions">
    <button
      type="button"
      id="prks-command-palette-launch"
      class="prks-palette-launch"
      title="Search or jump"
      aria-label="Search or jump"
      aria-haspopup="dialog"
      data-palette-bound="1"
      @click="openPalette"
    >
      <span class="prks-palette-launch__icon" aria-hidden="true">
        <ShellIcon name="search" size="ribbon" />
      </span>
      <span class="prks-palette-launch__label">Search or jump…</span>
      <kbd class="prks-palette-launch__hint" data-palette-shortcut-hint>{{ shortcutHint }}</kbd>
    </button>
    <div class="prks-split-btn" id="prks-ribbon-create">
      <PrksButton
        id="prks-ribbon-new-file"
        class="ribbon-btn prks-split-btn__main"
        variant="secondary"
        title="New File"
        @click="newFile"
      >
        <ShellIcon class="ribbon-btn__icon" name="plus" size="ribbon" />
        <span class="ribbon-btn__label">New File</span>
      </PrksButton>
      <button
        ref="chevron"
        type="button"
        id="prks-ribbon-new-more"
        class="prks-btn prks-btn--secondary ribbon-btn prks-split-btn__chevron"
        title="More"
        aria-label="More create actions"
        aria-haspopup="menu"
        :aria-expanded="open ? 'true' : 'false'"
        aria-controls="prks-create-menu"
        @click="onChevronClick"
        @keydown="onChevronKey"
      >
        <ShellIcon class="ribbon-btn__icon" name="chevron-down" size="ribbon" />
      </button>
    </div>
  </div>
  <div class="top-ribbon__right">
    <div
      id="prks-connectivity-indicator"
      class="prks-connectivity-indicator"
      :class="{
        hidden: online,
        'prks-connectivity-indicator--reconnecting': chrome.connectivity === 'reconnecting',
      }"
      role="status"
      aria-live="polite"
      :hidden="online"
    >
      <span class="prks-connectivity-indicator__icon" aria-hidden="true">
        <ShellIcon name="wifi-off" size="sm" />
      </span>
      <span class="prks-connectivity-indicator__label" id="prks-connectivity-indicator-label">{{ connectivityLabel }}</span>
    </div>
    <button
      type="button"
      id="prks-sync-queue-cue"
      class="prks-sync-queue-cue"
      :class="{ hidden: !syncVisible }"
      :hidden="!syncVisible"
      :aria-label="syncVisible && syncQueue ? syncQueue.ariaLabel : 'Queued changes'"
      title="Open Settings → Diagnostics"
      @click="openSyncDiagnostics"
    >
      <span class="prks-sync-queue-cue__icon" aria-hidden="true">
        <ShellIcon name="cloud-upload" size="sm" />
      </span>
      <span data-prks-role="sync-queue-badge" v-html="syncBadge"></span>
    </button>
    <PrksIconButton
      id="prks-mobile-details-btn"
      variant="ghost"
      label="Open details panel"
      title="Details"
      aria-controls="right-panel"
      :aria-expanded="chrome.detailsOpen ? 'true' : 'false'"
      @click="toggleDetails"
    >
      <ShellIcon class="ribbon-btn__icon" name="info" size="ribbon" />
      <span class="prks-sr-only">Details</span>
    </PrksIconButton>
  </div>
  <Teleport to="body">
    <div
      id="prks-create-menu"
      ref="menu"
      class="prks-workspace-menu"
      role="menu"
      aria-label="Create"
      :hidden="!open"
    >
      <button
        v-for="(action, index) in CREATE_ACTIONS"
        :key="action.id"
        type="button"
        class="prks-workspace-menu__item"
        role="menuitem"
        :data-create-id="action.id"
        :class="{ 'is-active': open && index === menuIndex }"
        :tabindex="open && index === menuIndex ? 0 : -1"
        @click.prevent.stop="runAction(action.modalId)"
      >
        <span class="prks-workspace-menu__icon" aria-hidden="true">
          <ShellIcon :name="action.icon" size="sm" extra-class="prks-workspace-menu__icon-svg" />
        </span>
        <span class="prks-workspace-menu__label">{{ action.label }}</span>
      </button>
    </div>
  </Teleport>
</template>
