<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import SettingsSwitch from './SettingsSwitch.vue'

const THEMES = [
  { id: 'system', label: 'System', title: 'System default', aria: 'System default theme' },
  { id: 'light', label: 'Light', title: 'Light theme', aria: 'Light theme' },
  { id: 'dark', label: 'Dark', title: 'Dark theme', aria: 'Dark theme' },
] as const

const theme = ref(window.prksReadStoredTheme?.() ?? 'system')
const hints = ref(window.prksGetHintsEnabled?.() ?? true)
const author = ref(typeof window.__prksAnnotationAuthor === 'string' ? window.__prksAnnotationAuthor : '')
let authorDirty = false
let authorTimer = 0

function commitTheme(next: string): void {
  theme.value = next
  window.prksCommitTheme?.(next)
}

function toggleHints(): void {
  hints.value = !hints.value
  window.prksSetHintsEnabled?.(hints.value)
  window.prksApplyHintsPreferenceToDocument?.()
  window.prksCloseHintPopover?.()
}

function onAuthorInput(event: Event): void {
  const el = event.target
  if (!(el instanceof HTMLInputElement)) return
  author.value = el.value
  persistAuthor()
}

function persistAuthor(): void {
  authorDirty = true
  const trimmed = author.value.trim()
  window.prksSetAnnotationAuthorCache?.(trimmed)
  window.clearTimeout(authorTimer)
  authorTimer = window.setTimeout(() => {
    void window.prksPatchAppSettings?.({ annotation_author: trimmed }).catch((error: unknown) => {
      console.warn(error)
    })
  }, 450)
}

onMounted(async () => {
  try {
    await window.prksLoadAppSettings?.()
  } catch {
    /* The field stays editable. The next change retries the patch. */
  }
  if (authorDirty) return
  author.value = typeof window.__prksAnnotationAuthor === 'string' ? window.__prksAnnotationAuthor : ''
})

onBeforeUnmount(() => {
  window.clearTimeout(authorTimer)
})
</script>

<template>
  <h4 class="prks-settings-panel__title">General</h4>
  <fieldset class="prks-theme-picker">
    <legend class="prks-theme-picker__legend">
      Appearance <span class="prks-settings-scope">This device</span>
    </legend>
    <div class="prks-theme-picker__options" role="group" aria-label="Choose app theme">
      <button
        v-for="option in THEMES"
        :key="option.id"
        type="button"
        class="prks-theme-option"
        :class="{ 'prks-theme-option--active': theme === option.id }"
        :data-prks-theme="option.id"
        :title="option.title"
        :aria-label="option.aria"
        :aria-pressed="theme === option.id ? 'true' : 'false'"
        @click="commitTheme(option.id)"
      >
        <svg
          v-if="option.id === 'system'"
          class="prks-theme-option__icon"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          width="22"
          height="22"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
          <path d="M8 21h8M12 17v4" />
        </svg>
        <svg
          v-else-if="option.id === 'light'"
          class="prks-theme-option__icon"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          width="22"
          height="22"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
        <svg
          v-else
          class="prks-theme-option__icon"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          width="22"
          height="22"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
        <span class="prks-theme-option__label">{{ option.label }}</span>
      </button>
    </div>
  </fieldset>
  <section class="prks-settings-section" aria-labelledby="prks-settings-author-heading">
    <PrksSectionHeader id="prks-settings-author-heading">
      Annotation author <span class="prks-settings-scope">Library-wide</span>
    </PrksSectionHeader>
    <label for="annotation-author-input" class="prks-sr-only">Name shown on new PDF annotations</label>
    <input
      id="annotation-author-input"
      :value="author"
      type="text"
      class="prks-settings-text-input"
      autocomplete="name"
      placeholder="Name shown on new PDF annotations"
      @input="onAuthorInput"
      @change="onAuthorInput"
    >
    <p class="prks-settings-hint">Leave empty to use “You”. Stored with your library so every device using this PRKS server sees the same name. Applies to viewers opened after you change this.</p>
  </section>
  <section class="prks-settings-section" aria-labelledby="prks-settings-help-heading">
    <PrksSectionHeader id="prks-settings-help-heading">
      Help <span class="prks-settings-scope">This device</span>
    </PrksSectionHeader>
    <div class="prks-setting-row">
      <div class="prks-setting-row__text">
        <span id="prks-lbl-ui-hints" class="prks-setting-row__label">Show help hints (? buttons)</span>
      </div>
      <SettingsSwitch id="prks-setting-ui-hints" :on="hints" labelled-by="prks-lbl-ui-hints" @click="toggleHints" />
    </div>
  </section>
</template>
