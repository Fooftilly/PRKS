<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import { setBibtexProfileFlush, setBibtexProfileUnreconciled, trackBibtexProfileWrite } from '../citations/bibtex-profile'
import { CITATION_STYLES, importedStyles, libraryCitationStyle } from '../citations/styles'
import ImportedCitationStyles from './ImportedCitationStyles.vue'
import { bibtexExportSummary } from './bibtex-summary'
import { createSerialSave } from './serial-save'
import SettingsSwitch from './SettingsSwitch.vue'

interface ExportField {
  id: string
  label: string
  on: boolean
}

const fields = ref<ExportField[]>([])
const bibtexStatus = ref('')
/* Toggles wait for a profile PRKS answered: without one a failed save has nothing to roll back to. */
const profileKnown = ref(false)
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
    if (style.value !== chosen || styleSave.saved() !== chosen) return
    // What PRKS answered may be another session's newer style; take it as
    // saved too, so choosing ours again sends it.
    const confirmed = libraryCitationStyle()
    styleSave.reset(confirmed)
    style.value = confirmed
  })
}

/** After an import or a delete: a deleted Library style makes PRKS answer the default again. */
async function onStylesChanged(deleted: string | null): Promise<void> {
  if (!deleted || style.value !== deleted) return
  try {
    await window.prksLoadAppSettings?.()
  } catch {
    /* The picker keeps its value until the settings can be read. */
  }
  if (style.value !== deleted) return
  const current = libraryCitationStyle()
  styleSave.reset(current)
  style.value = current
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

/* Only the toggles this page changed are sent. A full map would carry this
 * page's stale view of every other toggle and undo what another tab or device
 * saved meanwhile; PRKS merges a partial map under its row lock (#889). */
let changed: Record<string, boolean> = {}
let inFlight = 0
let queue: Promise<void> = Promise.resolve()
/* Patches sent or queued whose answer has not come back. */
const waiting: Array<Record<string, boolean>> = []
/* What PRKS last answered. A failed patch leaves the cache holding the
 * toggles it carried, so the switches and Copy BibTeX return to this. */
let confirmed: Record<string, boolean> | null = null
let failed = false

function storedMap(): Record<string, boolean> | null {
  const stored = window.__prksBibtexExportFields
  return stored && typeof stored === 'object' ? { ...stored } : null
}

function idle(): boolean {
  return !inFlight && !timer && !Object.keys(changed).length
}

/* Every write of the toggles -- a debounced patch or Restore defaults -- runs
 * after the one before it, so an older patch can never land after a newer
 * one. Once nothing of ours is waiting, the switches show what PRKS holds:
 * our toggles merged with any another session saved. */
function send(patch: Record<string, boolean>): Promise<unknown> {
  inFlight += 1
  waiting.push(patch)
  const run = queue.then(() => Promise.resolve(window.prksPatchAppSettings?.({ bibtex_export_fields: patch })))
    .then(() => {
      confirmed = storedMap()
      return true
    }, (error: unknown) => {
      failed = true
      setBibtexProfileUnreconciled(true)
      bibtexStatus.value = 'The BibTeX export settings could not be saved. Try again when PRKS is reachable.'
      console.warn(error)
      return false
    })
  queue = run.then(() => undefined)
  trackBibtexProfileWrite(run)
  return run.finally(() => {
    inFlight -= 1
    waiting.splice(waiting.indexOf(patch), 1)
    const stored = window.__prksBibtexExportFields
    if (!idle()) {
      /* The answer is PRKS before our later toggles. Copy BibTeX reads this
       * cache, so it keeps the toggles the page shows until they are saved. */
      const ours = Object.assign({}, ...waiting, changed)
      window.prksSetBibtexExportFieldsCache?.({ ...(stored && typeof stored === 'object' ? stored : {}), ...ours })
      return
    }
    if (failed) {
      failed = false
      if (confirmed) {
        window.prksSetBibtexExportFieldsCache?.(confirmed)
        applyMap(confirmed)
        setBibtexProfileUnreconciled(false)
        return
      }
    }
    if (stored && typeof stored === 'object') applyMap(stored)
  })
}

function flush(): void {
  const patch = changed
  changed = {}
  if (!Object.keys(patch).length) return
  void send(patch)
}

function persist(id: string, on: boolean): void {
  dirty = true
  bibtexStatus.value = ''
  changed[id] = on
  window.prksSetBibtexExportFieldsCache?.(currentMap())
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    timer = 0
    flush()
  }, 450)
}

function toggle(id: string): void {
  if (!profileKnown.value) return
  const field = fields.value.find((item) => item.id === id)
  if (!field) return
  field.on = !field.on
  persist(id, field.on)
}

async function restoreDefaults(): Promise<void> {
  if (!profileKnown.value) return
  const allTrue: Record<string, boolean> = {}
  for (const field of fields.value) allTrue[field.id] = true
  window.prksSetBibtexExportFieldsCache?.(allTrue)
  applyMap(allTrue)
  dirty = true
  bibtexStatus.value = ''
  window.clearTimeout(timer)
  timer = 0
  changed = {}
  // Restore defaults is an explicit choice about every toggle, so it sends all of them.
  await send(allTrue)
}

function flushDebounced(): void {
  window.clearTimeout(timer)
  timer = 0
  flush()
}

onMounted(async () => {
  setBibtexProfileFlush(flushDebounced)
  applyMap({})
  let settings: unknown = null
  try {
    settings = await window.prksLoadAppSettings?.()
  } catch {
    settings = null
  }
  const profile = settings && typeof settings === 'object'
    ? (settings as { bibtex_export_fields?: unknown }).bibtex_export_fields : null
  profileKnown.value = !!profile && typeof profile === 'object'
  if (!profileKnown.value) {
    bibtexStatus.value = 'The BibTeX export settings could not be read. Reopen Settings when PRKS is reachable.'
  }
  if (!styleDirty) {
    style.value = libraryCitationStyle()
    styleSave.reset(style.value)
  }
  if (profileKnown.value && !confirmed) confirmed = storedMap()
  if (dirty) return
  const stored = window.__prksBibtexExportFields
  applyMap(stored && typeof stored === 'object' ? stored : {})
})

onBeforeUnmount(() => {
  setBibtexProfileFlush(null)
  // A toggle still waiting for its debounce is sent, not dropped.
  flushDebounced()
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
      <optgroup v-if="importedStyles.length" label="Imported">
        <option v-for="option in importedStyles" :key="option.id" :value="option.id">{{ option.label }}</option>
      </optgroup>
    </select>
    <p class="prks-settings-hint">Used when you copy a formatted citation or bibliography. A file can use its own style instead (Edit metadata).</p>
    <p v-if="styleStatus" class="prks-settings-hint prks-inline-message--error" role="alert">{{ styleStatus }}</p>
    <ImportedCitationStyles @changed="onStylesChanged" />
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
          :disabled="!profileKnown"
          @click="toggle(field.id)"
        />
      </div>
    </div>
    <!-- Mounted while empty: a screen reader announces text added to an existing live region. -->
    <div id="prks-bibtex-export-status" role="alert">
      <PrksInlineMessage v-if="bibtexStatus" tone="error">{{ bibtexStatus }}</PrksInlineMessage>
    </div>
    <PrksButton id="prks-bibtex-export-reset" class="prks-bibtex-export-reset" :disabled="!profileKnown" @click="restoreDefaults">
      Restore export defaults
    </PrksButton>
  </section>
</template>
