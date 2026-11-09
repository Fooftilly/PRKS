<script setup lang="ts">
import { ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import { textIndexSummary } from './backup-format'

const reindexBusy = ref(false)
const reindexStatus = ref('')
const linearizeBusy = ref(false)
const linearizeStatus = ref('')

async function rebuildIndex(): Promise<void> {
  if (reindexBusy.value) return
  reindexBusy.value = true
  reindexStatus.value = ''
  try {
    if (typeof window.prksReindexPdfText !== 'function') throw new Error('PDF rebuild API unavailable.')
    const out = await window.prksReindexPdfText()
    reindexStatus.value = textIndexSummary(out)
  } catch (error) {
    const message = error instanceof Error ? error.message : ''
    reindexStatus.value = message || 'Could not rebuild PDF text index.'
  } finally {
    reindexBusy.value = false
  }
}

async function linearize(): Promise<void> {
  if (linearizeBusy.value) return
  linearizeBusy.value = true
  linearizeStatus.value = ''
  try {
    if (typeof window.prksLinearizeExistingPdfs !== 'function') {
      throw new Error('PDF linearization API unavailable.')
    }
    const out = await window.prksLinearizeExistingPdfs(true)
    const processed = Number(out.processed || 0)
    const changed = Number(out.changed || 0)
    const already = Number(out.already_linearized || 0)
    const skipped = Number(out.skipped || 0)
    const failed = Number(out.failed || 0)
    linearizeStatus.value = `Done. Processed ${processed}, linearized ${changed}, already linearized ${already}, skipped ${skipped}, failed ${failed}.`
  } catch (error) {
    const message = error instanceof Error ? error.message : ''
    linearizeStatus.value = message || 'Could not linearize existing PDFs.'
  } finally {
    linearizeBusy.value = false
  }
}
</script>

<template>
  <h4 class="prks-settings-panel__title">Maintenance</h4>
  <p class="prks-settings-hint">These tools are normally unnecessary. Use them when troubleshooting search or optimizing existing PDFs.</p>
  <section class="prks-settings-section" aria-labelledby="prks-settings-pdf-index-heading">
    <PrksSectionHeader id="prks-settings-pdf-index-heading">PDF text index</PrksSectionHeader>
    <p class="prks-settings-hint">PDF text search is maintained automatically. Use a full rebuild if PDF search results seem incomplete or stale.</p>
    <PrksButton id="prks-reindex-pdf-text-btn" :busy="reindexBusy" busy-label="Rebuilding…" @click="rebuildIndex">
      Rebuild PDF text index
    </PrksButton>
    <p id="prks-reindex-pdf-text-status" class="prks-settings-hint" aria-live="polite">{{ reindexStatus }}</p>
  </section>
  <section class="prks-settings-section prks-settings-section--sep" aria-labelledby="prks-settings-pdf-linearize-heading">
    <PrksSectionHeader id="prks-settings-pdf-linearize-heading">PDF linearization</PrksSectionHeader>
    <p class="prks-settings-hint">Optimize existing library PDFs for progressive loading. Only non-linearized PDFs are processed.</p>
    <PrksButton
      id="prks-linearize-existing-pdfs-btn"
      :busy="linearizeBusy"
      busy-label="Linearizing…"
      @click="linearize"
    >
      Linearize existing PDFs
    </PrksButton>
    <p id="prks-linearize-existing-pdfs-status" class="prks-settings-hint" aria-live="polite">{{ linearizeStatus }}</p>
  </section>
</template>
