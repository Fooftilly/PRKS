<script setup lang="ts">
/**
 * The Work panel's formatted citation (Citation V1, #41).
 *
 * Shown inside the Metadata disclosure. It reads the Work's CSL-JSON item
 * from the server only while that disclosure is open, formats it in the
 * Work's style (or the Library's default) and copies it as rich text.
 * It is a read of the saved record: an edit that has not reached PRKS yet
 * shows once it has.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import { fetchCslItems } from '../../api/citations'
import { isAbortError } from '../../api/http'
import { formatCitations, type CitationEntry } from './engine'
import { copyRichText } from './export'
import { citationStyleLabel, effectiveCitationStyle } from './styles'

const props = defineProps<{
  workId: string
  /** The Work's own style id, or '' for the Library's default. */
  workStyle: string
  /** Changes when the Work's bibliographic fields change, so the preview re-reads. */
  revision: string
}>()

type State = 'idle' | 'loading' | 'ready' | 'error'

const root = ref<HTMLElement | null>(null)
const state = ref<State>('idle')
const entry = ref<CitationEntry | null>(null)
const copyLabel = ref('Copy citation')
const style = computed(() => effectiveCitationStyle(props.workStyle))
const styleLabel = computed(() => citationStyleLabel(style.value))

let disclosure: HTMLDetailsElement | null = null
let controller: AbortController | null = null
let copyTimer = 0
let loaded = ''

function key(): string {
  return `${props.workId}\n${style.value}\n${props.revision}`
}

async function load(): Promise<void> {
  if (disclosure && !disclosure.open) return
  const wanted = key()
  if (loaded === wanted && state.value === 'ready') return
  controller?.abort()
  const current = new AbortController()
  controller = current
  state.value = 'loading'
  try {
    const { items } = await fetchCslItems([props.workId], current.signal)
    const formatted = items.length ? await formatCitations(items, style.value) : null
    if (current.signal.aborted) return
    entry.value = formatted?.entries[0] ?? null
    state.value = entry.value ? 'ready' : 'error'
    loaded = wanted
  } catch (error) {
    if (isAbortError(error) || current.signal.aborted) return
    state.value = 'error'
  }
}

function onToggle(): void {
  if (disclosure?.open) void load()
}

async function copy(): Promise<void> {
  const shown = entry.value
  if (!shown) return
  window.clearTimeout(copyTimer)
  try {
    await copyRichText(shown.html, shown.text)
    copyLabel.value = 'Copied'
  } catch {
    copyLabel.value = 'Copy failed'
  }
  copyTimer = window.setTimeout(() => {
    copyLabel.value = 'Copy citation'
  }, 1500)
}

onMounted(() => {
  disclosure = root.value?.closest('details') ?? null
  disclosure?.addEventListener('toggle', onToggle)
  void load()
})

watch(key, () => {
  void load()
})

onBeforeUnmount(() => {
  disclosure?.removeEventListener('toggle', onToggle)
  controller?.abort()
  window.clearTimeout(copyTimer)
})
</script>

<template>
  <div ref="root" class="work-citation" data-prks-role="work-citation">
    <p class="meta-row work-citation__heading">
      <strong>Citation</strong>
      <span class="work-citation__style" data-prks-role="work-citation-style">{{ styleLabel }}</span>
    </p>
    <p class="work-citation__text" data-prks-role="work-citation-text" aria-live="polite">
      <template v-if="state === 'ready' && entry">
        <template v-for="(run, index) in entry.runs" :key="index">
          <i v-if="run.italic">{{ run.text }}</i>
          <template v-else>{{ run.text }}</template>
        </template>
      </template>
      <span v-else-if="state === 'error'" class="meta-row--muted-italic">The citation could not be shown. It needs a connection to PRKS.</span>
      <span v-else class="meta-row--muted-italic">Formatting…</span>
    </p>
    <PrksButton
      size="sm"
      class="work-citation__copy"
      data-prks-role="work-citation-copy"
      :disabled="state !== 'ready'"
      aria-live="polite"
      @click="copy"
    >{{ copyLabel }}</PrksButton>
  </div>
</template>
