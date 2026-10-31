<script setup lang="ts">
import { ref } from 'vue'
import SettingsSwitch from './SettingsSwitch.vue'

const forceMobile = ref(window.prksGetForceMobileEnabled?.() ?? false)
const notesBeside = ref(window.prksGetMobileWorkNotesRightEnabled?.() ?? false)
const rememberPage = ref(window.prksGetPdfRememberLastPageEnabled?.() ?? true)

function toggleForceMobile(): void {
  forceMobile.value = !forceMobile.value
  window.prksApplyForceMobileEnabled?.(forceMobile.value)
}

function toggleNotesBeside(): void {
  notesBeside.value = !notesBeside.value
  window.prksSetMobileWorkNotesRightEnabled?.(notesBeside.value)
  window.prksSyncWorkNotesMobileSideClass?.()
}

function toggleRememberPage(): void {
  rememberPage.value = !rememberPage.value
  window.prksSetPdfRememberLastPageEnabled?.(rememberPage.value)
}
</script>

<template>
  <h4 class="prks-settings-panel__title">Reading &amp; layout</h4>
  <p class="prks-settings-hint">Stored on this device.</p>
  <div class="prks-setting-row">
    <div class="prks-setting-row__text">
      <span id="prks-lbl-force-mobile" class="prks-setting-row__label">Force mobile layout</span>
      <p id="prks-hint-force-mobile" class="prks-settings-hint prks-setting-row__hint">Drawer navigation and details overlay, even on wide screens.</p>
    </div>
    <SettingsSwitch
      id="prks-setting-force-mobile"
      :on="forceMobile"
      labelled-by="prks-lbl-force-mobile"
      described-by="prks-hint-force-mobile"
      @click="toggleForceMobile"
    />
  </div>
  <div class="prks-setting-row">
    <div class="prks-setting-row__text">
      <span id="prks-lbl-mobile-work-notes" class="prks-setting-row__label">Research notes beside PDF when narrow</span>
      <p id="prks-hint-mobile-work-notes" class="prks-settings-hint prks-setting-row__hint">On narrow stacked containers, notes expand as a drawer by default. Enable this to keep a side-by-side sidecar instead (also applies with <strong>Force mobile layout</strong>). Split-view tiles always use a drawer.</p>
    </div>
    <SettingsSwitch
      id="prks-setting-mobile-work-notes-right"
      :on="notesBeside"
      labelled-by="prks-lbl-mobile-work-notes"
      described-by="prks-hint-mobile-work-notes"
      @click="toggleNotesBeside"
    />
  </div>
  <div class="prks-setting-row">
    <div class="prks-setting-row__text">
      <span id="prks-lbl-pdf-remember" class="prks-setting-row__label">Remember PDF page per file</span>
      <p id="prks-hint-pdf-remember-page" class="prks-settings-hint prks-setting-row__hint">Last viewed page per file is kept in this browser.</p>
    </div>
    <SettingsSwitch
      id="prks-setting-pdf-remember-page"
      :on="rememberPage"
      labelled-by="prks-lbl-pdf-remember"
      described-by="prks-hint-pdf-remember-page"
      @click="toggleRememberPage"
    />
  </div>
</template>
