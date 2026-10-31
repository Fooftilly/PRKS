<script setup lang="ts">
import { onMounted } from 'vue'
import { SETTINGS_CATEGORIES } from './categories'
import {
  activateSettingsCategory,
  bindSettingsNavOrientation,
  openSettingsToLastCategory,
  settingsActiveCategory,
  settingsNavOrientation,
} from './navigation'
import SettingsBackup from './SettingsBackup.vue'
import SettingsDiagnostics from './SettingsDiagnostics.vue'
import SettingsExport from './SettingsExport.vue'
import SettingsGeneral from './SettingsGeneral.vue'
import SettingsMaintenance from './SettingsMaintenance.vue'
import SettingsPanel from './SettingsPanel.vue'
import SettingsReading from './SettingsReading.vue'

function onNavKeydown(event: KeyboardEvent): void {
  const nav = event.currentTarget
  if (!(nav instanceof HTMLElement)) return
  const tabs = Array.from(nav.querySelectorAll<HTMLButtonElement>('.prks-settings-nav__item'))
  const index = tabs.indexOf(document.activeElement as HTMLButtonElement)
  if (index === -1) return
  let nextIndex: number | null = null
  if (event.key === 'ArrowDown' || event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length
  else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length
  else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = tabs.length - 1
  if (nextIndex === null) return
  event.preventDefault()
  const next = tabs[nextIndex]
  activateSettingsCategory(next?.dataset.prksSettingsCategory, { focusTab: true })
}

onMounted(() => {
  bindSettingsNavOrientation()
  openSettingsToLastCategory()
})
</script>

<template>
  <div class="prks-settings-workspace">
    <nav
      id="prks-settings-nav"
      class="prks-settings-nav"
      role="tablist"
      :aria-orientation="settingsNavOrientation"
      aria-label="Settings categories"
      @keydown="onNavKeydown"
    >
      <button
        v-for="category in SETTINGS_CATEGORIES"
        :id="`prks-settings-tab-${category.id}`"
        :key="category.id"
        type="button"
        class="prks-settings-nav__item"
        :class="{ 'is-active': settingsActiveCategory === category.id }"
        role="tab"
        :aria-selected="settingsActiveCategory === category.id ? 'true' : 'false'"
        :tabindex="settingsActiveCategory === category.id ? 0 : -1"
        :aria-controls="`prks-settings-panel-${category.id}`"
        :data-prks-settings-category="category.id"
        @click="activateSettingsCategory(category.id)"
      >
        {{ category.label }}
      </button>
    </nav>
    <div id="prks-settings-content" class="prks-settings-content">
      <SettingsPanel category="general" :active="settingsActiveCategory === 'general'">
        <SettingsGeneral />
      </SettingsPanel>
      <SettingsPanel category="reading" :active="settingsActiveCategory === 'reading'">
        <SettingsReading />
      </SettingsPanel>
      <SettingsPanel category="export" :active="settingsActiveCategory === 'export'">
        <SettingsExport />
      </SettingsPanel>
      <SettingsPanel category="backup" :active="settingsActiveCategory === 'backup'">
        <SettingsBackup />
      </SettingsPanel>
      <SettingsPanel category="maintenance" :active="settingsActiveCategory === 'maintenance'">
        <SettingsMaintenance />
      </SettingsPanel>
      <SettingsPanel category="diagnostics" :active="settingsActiveCategory === 'diagnostics'">
        <SettingsDiagnostics />
      </SettingsPanel>
    </div>
  </div>
</template>
