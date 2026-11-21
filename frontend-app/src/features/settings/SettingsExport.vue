<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import { CITATION_STYLES, libraryCitationStyle } from '../citations/styles'
import { bibtexExportSummary } from './bibtex-summary'
import { createSerialSave } from './serial-save'
import SettingsSwitch from './SettingsSwitch.vue'

interface ExportField {
  id: string
  label: string
  on: boolean
}

const fields = ref<ExportField[]>([])
let dirty = false
let timer = 0
const style = ref(libraryCitationStyle())
const styleStatus = ref('')
let styleDirty = false
const styleSave = createSerialSave(style.value, {
  save: (value) => window.prksPatchAppSettings?.({ citation_style: value }) ?? Promise.resolve(),
  onFailed(saved) {
    style.value = saved
    window.prksSetCitationStyleCache?.(saved)
    styleStatus.value = 'The citation style could not be saved. Try again when PRKS is reachable.'
  },
})

function onStyleChange(event: Event): void {
  const el = event.target
  if (!(el instanceof HTMLSelectElement)) return
  style.value = el.value
  styleDirty = true
  styleStatus.value = ''
  window.prksSetCitationStyleCache?.(el.value)
  // One save at a time, newest last, so the last response is the newest
  // choice's. prksPatchAppSettings sets the cache from each response; once
  // the queue is idle the selector shows what PRKS saved, even when another
  // session saved a style in between.
  const chosen = el.value
  void styleSave.choose(chosen).then(() => {
    if (style.value === chosen && styleSave.saved() === chosen) style.value = libraryCitationStyle()
  })
}

function fieldDefs(): Array<readonly [string, string]> {
  const defs = window.PRKS_BIBTEX_EXPORT_FIELD_DEFS
  return Array.isArray(defs) ? defs : []
}

function applyMap(map: Record<string, boolean> | null | undefined): void {
  const source = map && typeof map === 'object' ? map : {}
  fields.value = fieldDefs().map(([id, label]) => ({
    id,
    label,
    on: source[id] !== false,
  }))
}

const summary = computed(() => bibtexExportSummary(fields.value.map((field) => field.on)))

function currentMap(): Record<string, boolean> {
  const next: Record<string, boolean> = {}
  for (const field of fields.value) next[field.id] = field.on
  return next
}

function persist(): void {
  dirty = true
  const next = currentMap()
  window.prksSetBibtexExportFieldsCache?.(next)
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    void window.prksPatchAppSettings?.({ bibtex_export_fields: next }).catch((error: unknown) => {
      console.warn(error)
    })
  }, 450)
}

function toggle(id: string): void {
  const field = fields.value.find((item) => item.id === id)
  if (!field) return
  field.on = !field.on
  persist()
}

async function restoreDefaults(): Promise<void> {
  const allTrue: Record<string, boolean> = {}
  for (const field of fields.value) allTrue[field.id] = true
  window.prksSetBibtexExportFieldsCache?.(allTrue)
  applyMap(allTrue)
  dirty = true
  window.clearTimeout(timer)
  try {
    await window.prksPatchAppSettings?.({ bibtex_export_fields: allTrue })
  } catch (error) {
    console.warn(error)
  }
}

onMounted(async () => {
  applyMap({})
  try {
    await window.prksLoadAppSettings?.()
  } catch {
    /* Toggles stay at the inclusive default. */
  }
  if (!styleDirty) {
    style.value = libraryCitationStyle()
    styleSave.reset(style.value)
  }
  if (dirty) return
  const stored = window.__prksBibtexExportFields
  applyMap(stored && typeof stored === 'object' ? stored : {})
})

onBeforeUnmount(() => {
  window.clearTimeout(timer)
})
</script>

<template>
  <h4 class="prks-settings-panel__title">Export</h4>
  <section class="prks-settings-section" aria-labelledby="prks-settings-citation-heading">
    <PrksSectionHeader id="prks-settings-citation-heading">
      Citation style <span class="prks-settings-scope">Library-wide</span>
    </PrksSectionHeader>
    <label for="prks-citation-style" class="prks-sr-only">Default citation style</label>
    <select id="prks-citation-style" class="prks-settings-text-input" :value="style" @change="onStyleChange">
      <option v-for="option in CITATION_STYLES" :key="option.id" :value="option.id">{{ option.label }}</option>
    </select>
    <p class="prks-settings-hint">Used when you copy a formatted citation or bibliography. A file can use its own style instead (Edit metadata).</p>
    <p v-if="styleStatus" class="prks-settings-hint prks-inline-message--error" role="alert">{{ styleStatus }}</p>
  </section>
  <section class="prks-settings-section" aria-labelledby="prks-settings-bibtex-heading">
    <PrksSectionHeader id="prks-settings-bibtex-heading">
      BibTeX / BibLaTeX export <span class="prks-settings-scope">Library-wide</span>
    </PrksSectionHeader>
    <p class="prks-settings-hint">When a toggle is on, that field is included when you copy BibTeX. Turn off to keep data in the app but omit it from export.</p>
    <p id="prks-bibtex-export-summary" class="prks-settings-hint prks-bibtex-export-summary" aria-live="polite">{{ summary }}</p>
    <div id="prks-bibtex-export-fields" class="prks-bibtex-export-toggles" role="group" aria-label="Fields to include in BibTeX export">
      <div v-for="field in fields" :key="field.id" class="prks-setting-row">
        <div class="prks-setting-row__text">
          <span class="prks-setting-row__label" :id="`prks-lbl-bibtex-export-${field.id}`">{{ field.label }}</span>
        </div>
        <SettingsSwitch
          :data-bibtex-field="field.id"
          :on="field.on"
          :labelled-by="`prks-lbl-bibtex-export-${field.id}`"
          @click="toggle(field.id)"
        />
      </div>
    </div>
    <PrksButton id="prks-bibtex-export-reset" class="prks-bibtex-export-reset" @click="restoreDefaults">
      Restore export defaults
    </PrksButton>
  </section>
</template>
