<script setup lang="ts">
/**
 * Settings → Export → Citation style: the Library's imported styles (Citation V1, #41).
 *
 * `Import .csl style…` reads a file the owner picks and sends its text to
 * PRKS, which checks it (`backend/domain/works/custom_citation_styles.py`)
 * and says why when it refuses one. Each imported style has a Delete with
 * the destructive confirm; deleting the Library's style makes the Library
 * use the default again, and a file that used it is cited in the Library's
 * style. The links point to where styles come from. The list is read each
 * time the Export category is shown.
 */
import { ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import { MAX_CSL_BYTES, deleteImportedStyle, importStyle } from '../../api/citation-styles'
import { PrksApiError } from '../../api/http'
import { importedStyles, loadImportedStyles } from '../citations/styles'
import { settingsActiveCategory } from './navigation'

const emit = defineEmits<{
  /** An import or a delete finished, so the style picker may need to read the settings again. */
  changed: [deleted: string | null]
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const deleting = ref('')
const status = ref('')
const error = ref('')
const listError = ref('')

const UNREACHABLE = 'PRKS could not be reached. Try again when it is.'

function messageOf(caught: unknown): string {
  if (caught instanceof PrksApiError && caught.status >= 400 && caught.status < 500) return caught.message
  return UNREACHABLE
}

async function refresh(): Promise<void> {
  try {
    await loadImportedStyles(true)
    listError.value = ''
  } catch {
    listError.value = 'The imported styles could not be listed. Try again when PRKS is reachable.'
  }
}

function choose(): void {
  fileInput.value?.click()
}

async function onFile(event: Event): Promise<void> {
  const input = event.target instanceof HTMLInputElement ? event.target : null
  const file = input?.files?.[0]
  if (input) input.value = ''
  if (!file) return
  status.value = ''
  error.value = ''
  if (file.size > MAX_CSL_BYTES) {
    error.value = 'The style file is larger than 1 MB.'
    return
  }
  busy.value = true
  try {
    const imported = await importStyle(await file.text())
    status.value = `Imported “${imported.title}”. Choose it above to use it.`
    await refresh()
    emit('changed', null)
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function confirmDelete(title: string): Promise<boolean> {
  const confirm = window.prksConfirmDestructive
  if (typeof confirm !== 'function') return false
  return confirm({
    title: 'Delete citation style?',
    message: `“${title}” is removed from this library. Files that use it, and the library default if it is this style, are cited in the default style instead.`,
    confirmLabel: 'Delete style',
  })
}

async function remove(id: string, title: string): Promise<void> {
  if (deleting.value || !(await confirmDelete(title))) return
  status.value = ''
  error.value = ''
  deleting.value = id
  try {
    await deleteImportedStyle(id)
  } catch (caught) {
    // Already gone is what the delete wanted.
    if (!(caught instanceof PrksApiError && caught.status === 404)) {
      error.value = messageOf(caught)
      deleting.value = ''
      return
    }
  }
  deleting.value = ''
  await refresh()
  emit('changed', id)
}

// Read when the Export category is shown, not when Settings mounts hidden at startup.
watch(settingsActiveCategory, (category) => {
  if (category === 'export') void refresh()
}, { immediate: true })
</script>

<template>
  <div class="prks-imported-styles" data-prks-role="imported-citation-styles">
    <p class="prks-settings-hint">
      Add a style PRKS does not ship from its .csl file. Find one in the
      <a href="https://www.zotero.org/styles" target="_blank" rel="noopener noreferrer">Zotero Style Repository</a>,
      or make or adjust one in the
      <a href="https://editor.citationstyles.org/visualEditor/" target="_blank" rel="noopener noreferrer">CSL visual editor</a>.
      Dependent styles, which only point to another style, cannot be imported: import the style they name.
    </p>
    <div v-if="importedStyles.length" class="prks-imported-styles__list" role="list" aria-label="Imported citation styles">
      <div v-for="style in importedStyles" :key="style.id" class="prks-setting-row" role="listitem" :data-prks-style-id="style.id">
        <div class="prks-setting-row__text">
          <span class="prks-setting-row__label">{{ style.label }}</span>
        </div>
        <PrksButton
          size="sm"
          variant="quiet-danger"
          :disabled="deleting !== ''"
          :aria-busy="deleting === style.id ? 'true' : undefined"
          :aria-label="`Delete ${style.label}`"
          @click="remove(style.id, style.label)"
        >{{ deleting === style.id ? 'Deleting…' : 'Delete' }}</PrksButton>
      </div>
    </div>
    <input
      ref="fileInput"
      type="file"
      accept=".csl,application/vnd.citationstyles.style+xml,application/xml,text/xml"
      class="prks-sr-only"
      tabindex="-1"
      aria-hidden="true"
      data-prks-role="import-citation-style-file"
      @change="onFile"
    >
    <PrksButton
      data-prks-role="import-citation-style"
      :disabled="busy"
      :aria-busy="busy ? 'true' : undefined"
      @click="choose"
    >{{ busy ? 'Importing…' : 'Import .csl style…' }}</PrksButton>
    <p v-if="status" class="prks-settings-hint" role="status">{{ status }}</p>
    <p v-if="error" class="prks-settings-hint prks-inline-message--error" role="alert">{{ error }}</p>
    <p v-if="listError" class="prks-settings-hint prks-inline-message--error" role="alert">{{ listError }}</p>
  </div>
</template>
