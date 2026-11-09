<script setup lang="ts">
import { ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import EditorRecoveryDiagnostics from '../editor-recovery/EditorRecoveryDiagnostics.vue'
import PerformanceDiagnostics from '../performance-diagnostics/PerformanceDiagnostics.vue'

const clearing = ref(false)
const clearStatus = ref('')

async function refreshCache(): Promise<void> {
  await window.prksLoadOfflineCacheStatus?.()
}

async function clearCache(): Promise<void> {
  const confirmed = window.prksConfirmDestructive
    ? await window.prksConfirmDestructive({
        title: 'Clear offline cache?',
        message:
          'This removes pages and PDFs cached on this device for offline use. It never changes anything stored on the PRKS server.',
        confirmLabel: 'Clear offline cache',
      })
    : window.confirm('Clear offline cache stored on this device?')
  if (!confirmed) return
  clearing.value = true
  try {
    await window.prksOfflineClearCache?.()
    clearStatus.value = 'Offline cache cleared.'
    await window.prksLoadOfflineCacheStatus?.()
  } catch {
    clearStatus.value = 'Could not clear offline cache.'
  } finally {
    clearing.value = false
  }
}
</script>

<template>
  <h4 class="prks-settings-panel__title">Diagnostics</h4>
  <section class="prks-settings-section" aria-labelledby="prks-settings-perf-heading">
    <PrksSectionHeader id="prks-settings-perf-heading">Performance diagnostics</PrksSectionHeader>
    <p class="prks-settings-hint">Runtime-only measurements from this PRKS process. No research content, search terms or file names are recorded. Measurements reset when PRKS restarts.</p>
    <div id="prks-settings-perf-root">
      <PerformanceDiagnostics />
    </div>
  </section>
  <section class="prks-settings-section prks-settings-section--sep" aria-labelledby="prks-settings-offline-heading">
    <PrksSectionHeader id="prks-settings-offline-heading">
      Offline cache <span class="prks-settings-scope">This device</span>
    </PrksSectionHeader>
    <p class="prks-settings-hint">Read-only convenience copies of previously opened files and PDFs, kept on this device only. PRKS's own database and files are never affected by anything here.</p>
    <!-- v-once: the classic offline reader writes this node and inserts the sync list beside it. -->
    <div v-once>
      <p id="prks-offline-cache-summary" class="prks-settings-hint" aria-live="polite">Loading offline cache status…</p>
    </div>
    <div class="prks-backup-restore-row">
      <PrksButton id="prks-offline-cache-refresh-btn" @click="refreshCache">Refresh</PrksButton>
      <PrksButton id="prks-offline-cache-clear-btn" :busy="clearing" busy-label="Clearing…" @click="clearCache">
        Clear offline cache
      </PrksButton>
    </div>
    <p id="prks-offline-cache-status" class="prks-settings-hint" aria-live="polite">{{ clearStatus }}</p>
  </section>
  <section class="prks-settings-section prks-settings-section--sep" aria-labelledby="prks-settings-recovery-heading">
    <PrksSectionHeader id="prks-settings-recovery-heading">
      Editor recovery drafts <span class="prks-settings-scope">This device</span>
    </PrksSectionHeader>
    <p class="prks-settings-hint">Unsaved Research Notes and Reminders text this browser kept in case a tab closed or crashed. These are recovery copies, not changes waiting to sync, and nothing here changes the server. Clearing the offline cache keeps them; clearing this browser's site data removes them.</p>
    <div id="prks-settings-recovery-root">
      <EditorRecoveryDiagnostics />
    </div>
  </section>
  <section class="prks-settings-section prks-settings-section--sep" aria-labelledby="prks-settings-about-heading">
    <PrksSectionHeader id="prks-settings-about-heading">About PRKS</PrksSectionHeader>
    <p id="prks-copyright-notice" class="prks-settings-hint">Copyright © 2026 Nikola Perović and other PRKS contributors. Each author retains copyright in their contributions; third-party works retain their respective copyrights.</p>
    <p class="prks-settings-hint">PRKS-authored code and documentation are free software licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only). You may copy, modify, and redistribute that covered material under its terms. Third-party components retain their own licenses; no warranty is provided.</p>
    <p class="prks-settings-hint">
      <a id="prks-source-code-link" href="https://github.com/Fooftilly/PRKS" target="_blank" rel="noopener noreferrer">Upstream source code</a>
      ·
      <a id="prks-license-link" href="https://github.com/Fooftilly/PRKS/blob/master/LICENSE" target="_blank" rel="noopener noreferrer">License text</a>
      ·
      <a id="prks-third-party-link" href="https://github.com/Fooftilly/PRKS/blob/master/THIRD_PARTY_NOTICES.md" target="_blank" rel="noopener noreferrer">Third-party notices</a>
    </p>
  </section>
</template>
