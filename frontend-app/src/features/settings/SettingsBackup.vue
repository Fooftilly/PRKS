<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksSectionHeader from '../../components/PrksSectionHeader.vue'
import {
  backupPhaseLabel,
  backupSummaryRows,
  type BackupProgressEvent,
} from './backup-format'

const downloadBusy = ref(false)
const progressVisible = ref(false)
const progressPercent = ref(0)
const progressLabel = ref('')
const progressBusy = ref(false)
const downloadStatus = ref('')
const fileLabel = ref('No file selected')
const restoreBusy = ref(false)
const restoreStatus = ref('')
const verified = ref(false)
const summaryRows = ref<Array<{ label: string; value: string }>>([])

let stagedToken = ''
let backupAbort: AbortController | null = null
let confirmInput: HTMLInputElement | null = null
let confirmSubmit: HTMLButtonElement | null = null
let confirmStatus: HTMLElement | null = null

function setBackupProgress(event: BackupProgressEvent): void {
  progressPercent.value = Math.max(0, Math.min(100, Number(event.percent) || 0))
  progressLabel.value = backupPhaseLabel(event)
  progressVisible.value = true
  progressBusy.value = event.phase !== 'ready'
}

function hideBackupProgress(): void {
  progressVisible.value = false
  progressBusy.value = false
  progressPercent.value = 0
  progressLabel.value = ''
}

function hideVerified(): void {
  stagedToken = ''
  verified.value = false
  summaryRows.value = []
}

function syncConfirm(): void {
  if (!confirmSubmit || !confirmInput) return
  confirmSubmit.disabled = restoreBusy.value || confirmInput.value.trim() !== 'RESTORE'
}

function setRestoreBusy(busy: boolean): void {
  restoreBusy.value = busy
  if (confirmInput) confirmInput.disabled = busy
  syncConfirm()
}

function startBackupDownload(token: string, filename: string | undefined): void {
  const link = document.createElement('a')
  const query = new URLSearchParams({ token: String(token || '') })
  link.href = '/api/backups/download?' + query.toString()
  link.setAttribute('download', filename || '')
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function readBackupProgress(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<BackupProgressEvent | null> {
  const decoder = new TextDecoder()
  let buf = ''
  let last: BackupProgressEvent | null = null
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() || ''
    for (const line of lines) {
      const raw = line.trim()
      if (!raw) continue
      let event: BackupProgressEvent
      try {
        event = JSON.parse(raw) as BackupProgressEvent
      } catch {
        continue
      }
      last = event
      setBackupProgress(event)
      if (event.phase === 'ready' && event.token) startBackupDownload(event.token, event.filename)
    }
  }
  if (buf.trim()) {
    try {
      last = JSON.parse(buf.trim()) as BackupProgressEvent
      setBackupProgress(last)
      if (last.phase === 'ready' && last.token) startBackupDownload(last.token, last.filename)
    } catch {
      /* ignore trailing fragment */
    }
  }
  return last
}

async function downloadBackup(): Promise<void> {
  if (backupAbort) return
  downloadBusy.value = true
  hideBackupProgress()
  setBackupProgress({ phase: 'snapshot', percent: 1 })
  downloadStatus.value = ''
  backupAbort = new AbortController()
  try {
    if (typeof window.prksStartBackupProgress !== 'function') {
      throw new Error('Backup API unavailable.')
    }
    const reader = await window.prksStartBackupProgress(backupAbort.signal)
    const last = await readBackupProgress(reader)
    if (!last || last.phase === 'cancelled') {
      downloadStatus.value = 'Backup cancelled.'
      hideBackupProgress()
      return
    }
    if (last.phase === 'failed') {
      hideBackupProgress()
      downloadStatus.value = last.error || 'Backup could not be created.'
      return
    }
    hideBackupProgress()
    const extra = Array.isArray(last.warnings) && last.warnings.length ? ` ${last.warnings.join(' ')}` : ''
    downloadStatus.value = 'Download starting…' + extra
  } catch (error) {
    const name = error && typeof error === 'object' && 'name' in error ? String(error.name) : ''
    if (name === 'AbortError') {
      downloadStatus.value = 'Backup cancelled.'
      hideBackupProgress()
    } else {
      const message = error instanceof Error ? error.message : ''
      downloadStatus.value = message || 'Backup could not be created.'
    }
  } finally {
    backupAbort = null
    downloadBusy.value = false
  }
}

function cancelBackup(): void {
  if (!backupAbort) return
  backupAbort.abort()
}

function onFileChange(event: Event): void {
  hideVerified()
  const input = event.target
  const file = input instanceof HTMLInputElement ? input.files?.[0] : undefined
  fileLabel.value = file ? file.name : 'No file selected'
  restoreStatus.value = ''
}

function chooseFile(): void {
  if (restoreBusy.value) return
  const input = document.getElementById('prks-backup-file-input')
  if (input instanceof HTMLInputElement) input.click()
}

async function verifyBackup(): Promise<void> {
  const input = document.getElementById('prks-backup-file-input')
  const file = input instanceof HTMLInputElement ? input.files?.[0] : undefined
  if (!file) {
    restoreStatus.value = 'Choose a backup file first.'
    return
  }
  setRestoreBusy(true)
  hideVerified()
  restoreStatus.value = 'Uploading backup… Verifying backup…'
  try {
    if (typeof window.prksStageBackup !== 'function') throw new Error('Backup API unavailable.')
    const out = await window.prksStageBackup(file)
    stagedToken = String(out.token || '')
    const summary = out.summary && typeof out.summary === 'object' ? out.summary : {}
    summaryRows.value = backupSummaryRows(summary)
    verified.value = true
    const warnings = Array.isArray(out.warnings) ? out.warnings : []
    restoreStatus.value = warnings.length ? `Backup verified. ${warnings.join(' ')}` : 'Backup verified'
  } catch (error) {
    hideVerified()
    const message = error instanceof Error ? error.message : ''
    restoreStatus.value = message || 'Backup could not be verified. Current PRKS data was not changed.'
  } finally {
    setRestoreBusy(false)
  }
}

function openRestoreConfirm(): void {
  if (!stagedToken || restoreBusy.value) return
  if (confirmInput) confirmInput.value = ''
  if (confirmStatus) confirmStatus.textContent = ''
  syncConfirm()
  window.openModal?.('prks-restore-confirm-modal')
}

async function submitRestore(): Promise<void> {
  if (!confirmInput || confirmInput.value.trim() !== 'RESTORE' || !stagedToken || restoreBusy.value) return
  setRestoreBusy(true)
  const running = 'Restoring library… Rebuilding search index…'
  if (confirmStatus) confirmStatus.textContent = running
  restoreStatus.value = running
  try {
    if (typeof window.prksRestoreBackup !== 'function') throw new Error('Backup API unavailable.')
    const out = await window.prksRestoreBackup(stagedToken)
    stagedToken = ''
    const extra = Array.isArray(out.warnings) && out.warnings.length ? ` ${out.warnings.join(' ')}` : ''
    const done = 'Restore complete — reloading…' + extra
    if (confirmStatus) confirmStatus.textContent = done
    restoreStatus.value = done
    window.location.reload()
  } catch (error) {
    const message = error instanceof Error ? error.message : ''
    const failed = message || 'Restore failed. Current PRKS data was not changed.'
    if (confirmStatus) confirmStatus.textContent = failed
    restoreStatus.value = failed
    setRestoreBusy(false)
  }
}

function onConfirmInput(): void {
  syncConfirm()
}

function onConfirmClick(): void {
  void submitRestore()
}

onMounted(() => {
  confirmInput = document.getElementById('prks-restore-confirm-input') as HTMLInputElement | null
  confirmSubmit = document.getElementById('prks-restore-confirm-submit') as HTMLButtonElement | null
  confirmStatus = document.getElementById('prks-restore-confirm-status')
  confirmInput?.addEventListener('input', onConfirmInput)
  confirmSubmit?.addEventListener('click', onConfirmClick)
})

onBeforeUnmount(() => {
  confirmInput?.removeEventListener('input', onConfirmInput)
  confirmSubmit?.removeEventListener('click', onConfirmClick)
})
</script>

<template>
  <h4 class="prks-settings-panel__title">Backup</h4>
  <section class="prks-settings-section" aria-labelledby="prks-settings-backup-create-heading">
    <PrksSectionHeader id="prks-settings-backup-create-heading">Create backup</PrksSectionHeader>
    <p class="prks-settings-hint">Create a verified backup of your PRKS library. Includes database, PDFs and managed person files. Search index and thumbnails are rebuilt automatically.</p>
    <div class="prks-backup-restore-row">
      <PrksButton id="prks-backup-download-btn" :disabled="downloadBusy" @click="downloadBackup">Download backup</PrksButton>
      <PrksButton id="prks-backup-cancel-btn" :class="{ hidden: !downloadBusy }" :disabled="!downloadBusy" @click="cancelBackup">Stop backup</PrksButton>
    </div>
    <div id="prks-backup-progress" class="prks-backup-progress" :class="{ hidden: !progressVisible }" :aria-busy="progressBusy ? 'true' : 'false'">
      <progress id="prks-backup-progress-bar" max="100" :value="progressPercent"></progress>
      <p id="prks-backup-progress-label" class="prks-settings-hint">{{ progressLabel }}</p>
    </div>
    <p id="prks-backup-download-status" class="prks-settings-hint" aria-live="polite">{{ downloadStatus }}</p>
  </section>
  <section class="prks-settings-section prks-settings-section--sep" aria-labelledby="prks-settings-backup-restore-heading">
    <PrksSectionHeader id="prks-settings-backup-restore-heading">Restore backup</PrksSectionHeader>
    <p class="prks-settings-hint">Choose a PRKS backup and verify it before restoring. Before restoring, download a backup of the current library if you may want to return to it.</p>
    <div class="prks-backup-restore-row">
      <input
        id="prks-backup-file-input"
        class="prks-backup-file-input"
        type="file"
        accept=".prks-backup,application/octet-stream,application/zip"
        aria-label="PRKS backup file"
        :disabled="restoreBusy"
        @change="onFileChange"
      >
      <PrksButton id="prks-backup-choose-btn" :disabled="restoreBusy" @click="chooseFile">Choose backup…</PrksButton>
      <span id="prks-backup-file-label" class="prks-settings-hint">{{ fileLabel }}</span>
    </div>
    <PrksButton id="prks-backup-verify-btn" :disabled="restoreBusy" @click="verifyBackup">Verify backup</PrksButton>
    <p id="prks-backup-restore-status" class="prks-settings-hint" aria-live="polite">{{ restoreStatus }}</p>
    <div id="prks-backup-verified-panel" class="prks-backup-verified-panel" :class="{ hidden: !verified }">
      <p class="prks-backup-verified-title">Backup verified</p>
      <ul id="prks-backup-summary" class="prks-backup-summary">
        <li v-for="row in summaryRows" :key="row.label">{{ row.label }}: {{ row.value }}</li>
      </ul>
      <PrksButton id="prks-backup-restore-btn" :disabled="restoreBusy" @click="openRestoreConfirm">Restore this backup</PrksButton>
    </div>
  </section>
</template>
