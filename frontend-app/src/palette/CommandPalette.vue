<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { PaletteView } from './types'

const CLOSED: PaletteView = {
  open: false,
  title: 'Search or jump',
  placeholder: 'Search or jump…',
  query: '',
  scope: 'all',
  navigationTarget: 'current',
  createMode: false,
  activeIndex: 0,
  activeDescendant: '',
  rows: [],
  status: '',
  hints: '',
  showHints: false,
  revision: 0,
}

const view = ref<PaletteView>(CLOSED)
const rootEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLInputElement | null>(null)
let stopSubscribe: (() => void) | null = null

function readView(): PaletteView {
  const read = window.prksCommandPaletteView
  if (typeof read !== 'function') return CLOSED
  return read()
}

function iconHtml(name: string): string {
  return window.prksIcon?.(name || 'search', { size: 'sm' }) ?? ''
}

function refreshIcons(): void {
  window.prksRefreshIcons?.(rootEl.value)
}

function focusInput(): void {
  void nextTick(() => {
    requestAnimationFrame(() => {
      if (!view.value.open) return
      inputEl.value?.focus()
    })
  })
}

function revealActive(): void {
  if (!view.value.open || !view.value.activeDescendant) return
  const el = document.getElementById(view.value.activeDescendant)
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'nearest' })
  }
}

function onInput(event: Event): void {
  const target = event.target
  if (!(target instanceof HTMLInputElement)) return
  window.prksCommandPaletteSetQuery?.(target.value)
}

function onInputKeydown(event: KeyboardEvent): void {
  window.prksCommandPaletteHandleKey?.(event)
}

function onScrimClick(): void {
  window.prksCloseCommandPalette?.()
}

function onOptionClick(index: number, event: MouseEvent): void {
  window.prksCommandPaletteActivateFromPointer?.(index, event)
}

onMounted(() => {
  view.value = readView()
  const subscribe = window.prksCommandPaletteSubscribe
  if (typeof subscribe === 'function') {
    stopSubscribe = subscribe((next) => {
      view.value = next
    })
  }
  refreshIcons()
  if (view.value.open) focusInput()
})

onBeforeUnmount(() => {
  stopSubscribe?.()
  stopSubscribe = null
})

watch(
  () => view.value.open,
  (open, was) => {
    if (open && !was) focusInput()
  },
)

watch(
  () => [view.value.revision, view.value.activeIndex] as const,
  ([, index], previous) => {
    void nextTick(() => {
      refreshIcons()
      const indexMoved = previous != null && previous[1] !== index
      if (indexMoved) {
        revealActive()
        return
      }
      const list = document.getElementById('prks-command-palette-results')
      if (list) list.scrollTop = 0
    })
  },
)
</script>

<template>
  <Teleport to="body">
    <div
      id="prks-command-palette"
      ref="rootEl"
      class="prks-command-palette"
      :class="{ 'prks-command-palette--create': view.createMode }"
      role="dialog"
      aria-modal="true"
      aria-labelledby="prks-command-palette-title"
      :hidden="view.open ? undefined : true"
    >
      <div class="prks-command-palette__scrim" data-palette-dismiss="1" @click="onScrimClick"></div>
      <div class="prks-command-palette__panel">
        <h2 id="prks-command-palette-title" class="prks-command-palette__title" v-text="view.title"></h2>
        <div class="prks-command-palette__input-row">
          <span class="prks-command-palette__input-icon" aria-hidden="true" v-html="iconHtml('search')"></span>
          <input
            id="prks-command-palette-input"
            ref="inputEl"
            type="text"
            role="combobox"
            aria-labelledby="prks-command-palette-title"
            aria-autocomplete="list"
            :aria-expanded="view.open ? 'true' : 'false'"
            aria-controls="prks-command-palette-results"
            :aria-activedescendant="view.activeDescendant || undefined"
            autocomplete="off"
            spellcheck="false"
            :placeholder="view.placeholder"
            :value="view.query"
            @input="onInput"
            @keydown="onInputKeydown"
          />
        </div>
        <div id="prks-command-palette-results" class="prks-command-palette__results" role="listbox">
          <template v-for="row in view.rows" :key="row.index + ':' + row.id">
            <div
              v-if="row.heading"
              class="prks-command-palette__heading"
              role="presentation"
              v-text="row.heading"
            ></div>
            <div
              :id="'prks-palette-opt-' + row.index"
              class="prks-command-palette__option"
              :class="{ 'is-active': row.index === view.activeIndex }"
              role="option"
              :aria-selected="row.index === view.activeIndex ? 'true' : 'false'"
              @click="onOptionClick(row.index, $event)"
            >
              <span class="prks-command-palette__option-icon" v-html="iconHtml(row.icon)"></span>
              <span class="prks-command-palette__option-text">
                <span class="prks-command-palette__option-label" v-text="row.label"></span>
                <span v-if="row.subtitle" class="prks-command-palette__sub" v-text="row.subtitle"></span>
              </span>
            </div>
          </template>
        </div>
        <p
          id="prks-command-palette-status"
          class="prks-command-palette__status"
          role="status"
          aria-live="polite"
          v-text="view.status"
        ></p>
        <p
          id="prks-command-palette-hints"
          class="prks-command-palette__hints"
          :hidden="view.showHints ? undefined : true"
          v-text="view.hints"
        ></p>
      </div>
    </div>
  </Teleport>
</template>
