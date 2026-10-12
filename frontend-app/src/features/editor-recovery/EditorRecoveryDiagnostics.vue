<script setup lang="ts">
/**
 * Settings → Diagnostics: browser-local editor recovery drafts (#466 slice 5).
 *
 * Separate from the durable sync queue listed above it: these are recovery
 * copies kept in this browser, not changes waiting to sync. Every draft is
 * reachable here without its Work or Folder: its text can be viewed, copied
 * and downloaded, and a stored draft no editor holds can be discarded after a
 * confirmation. Nothing here restores, enqueues or saves; restoring stays in
 * the editor's own notice and Review. The runtime (`prksEditorRecovery`)
 * reads and discards; this component only renders what it reports.
 */
import { computed, ref, shallowRef, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import type {
  DiagnosticsDraft,
  DiagnosticsEmergency,
  DiagnosticsUnreadableEmergency,
  DiagnosticsUnreadableRecord,
  RecoveryDiagnostics,
  RecoveryInventory,
  TextTarget,
} from '../../lifecycle/editor-recovery/diagnostics'
import { performanceDiagnosticsEnabled } from '../performance-diagnostics/activation'
import {
  budgetText,
  diagnosticsOriginText,
  discardFailureText,
  downloadName,
  draftFlags,
  draftStateText,
  emergencyText,
  entityHref,
  entityText,
  kindText,
  storageText,
  summaryText,
  textFailureText,
  unreadableEmergencyText,
  unreadableRecordText,
} from './diagnostics-labels'
import { lengthText, pipelineText, typedAtText } from './labels'

/** Characters shown inline; Copy and Download always carry the whole text. */
const PREVIEW_CHARS = 200_000

type Row =
  | { key: string; item: DiagnosticsDraft }
  | { key: string; item: DiagnosticsUnreadableRecord }
  | { key: string; item: DiagnosticsEmergency }
  | { key: string; item: DiagnosticsUnreadableEmergency }

interface PageHelpers {
  prksEditorRecovery?: { diagnostics?: () => RecoveryDiagnostics }
  prksCopyTextToClipboard?: (text: string) => Promise<void>
  prksConfirmDialog?: (options: { title: string; message: string; confirmLabel: string; cancelLabel?: string; danger: boolean }) => Promise<boolean>
}

function page(): PageHelpers {
  return window as unknown as PageHelpers
}

function api(): RecoveryDiagnostics | null {
  const recovery = page().prksEditorRecovery
  try {
    return recovery && typeof recovery.diagnostics === 'function' ? recovery.diagnostics() : null
  } catch {
    return null
  }
}

const inventory = shallowRef<RecoveryInventory | null>(null)
const loading = ref(false)
const loadFailed = ref(false)
const busyKey = ref<string | null>(null)
const opened = ref<Record<string, { text: string; truncated: boolean }>>({})
const rowMessage = ref<Record<string, string>>({})
const listMessage = ref('')

const rows = computed<Row[]>(() => {
  const inv = inventory.value
  if (!inv) return []
  return [
    ...inv.drafts.map((item) => ({ key: 'record:' + item.draftId, item })),
    ...inv.emergency.map((item) => ({ key: 'emergency:' + item.key + ':' + item.draftId + ':' + item.generation, item })),
    ...inv.unreadable.map((item, i) => ({ key: 'unreadable:' + (item.draftId ?? '#' + i), item })),
    ...inv.unreadableEmergency.map((item) => ({ key: 'unreadable-emergency:' + item.key, item })),
  ]
})

async function load(): Promise<void> {
  const diagnostics = api()
  loading.value = true
  try {
    if (!diagnostics) throw new Error('Editor recovery is not loaded.')
    inventory.value = await diagnostics.inventory()
    loadFailed.value = false
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
  opened.value = {}
  rowMessage.value = {}
}

const enabled = performanceDiagnosticsEnabled()
watch(
  enabled,
  (on) => {
    if (on && !inventory.value && !loading.value) void load()
  },
  { immediate: true },
)

async function refresh(): Promise<void> {
  listMessage.value = ''
  await load()
}

function textTarget(row: Row): TextTarget | null {
  const item = row.item
  switch (item.source) {
    case 'record':
      return { source: 'record', expect: item.expect }
    case 'unreadable-record':
      return item.draftId === null ? null : { source: 'unreadable-record', draftId: item.draftId, snapshot: item.snapshot }
    case 'emergency':
      return { source: 'emergency', key: item.key, draftId: item.draftId, generation: item.generation }
    case 'unreadable-emergency':
      return { source: 'unreadable-emergency', key: item.key }
  }
}

function isLive(row: Row): boolean {
  const item = row.item
  return (item.source === 'record' || item.source === 'emergency') && (item.lineage === 'self-live' || item.lineage === 'other-live')
}

function canReadText(row: Row): boolean {
  const item = row.item
  if (isLive(row) || !textTarget(row)) return false
  return item.source !== 'emergency' || item.bodyLength !== null
}

function setMessage(row: Row, text: string): void {
  rowMessage.value = { ...rowMessage.value, [row.key]: text }
}

async function readText(row: Row): Promise<string | null> {
  const diagnostics = api()
  const target = textTarget(row)
  if (!diagnostics || !target) return null
  const result = await diagnostics.readText(target)
  if (result.ok) return result.text
  if (result.code === 'changed' || result.code === 'live') {
    // What the list showed no longer holds: read it again, and say why at the top.
    await load()
    listMessage.value = textFailureText(result.code)
    return null
  }
  setMessage(row, textFailureText(result.code))
  return null
}

async function toggleText(row: Row): Promise<void> {
  if (opened.value[row.key]) {
    const next = { ...opened.value }
    delete next[row.key]
    opened.value = next
    return
  }
  busyKey.value = row.key
  try {
    const text = await readText(row)
    if (text === null) return
    const truncated = text.length > PREVIEW_CHARS
    opened.value = { ...opened.value, [row.key]: { text: truncated ? text.slice(0, PREVIEW_CHARS) : text, truncated } }
  } finally {
    busyKey.value = null
  }
}

async function copyText(row: Row): Promise<void> {
  busyKey.value = row.key
  try {
    const text = await readText(row)
    if (text === null) return
    const copy = page().prksCopyTextToClipboard
    if (!copy) throw new Error('Copy is unavailable')
    await copy(text)
    setMessage(row, 'Copied.')
  } catch {
    setMessage(row, 'Copy failed. Use Download instead.')
  } finally {
    busyKey.value = null
  }
}

function rowKind(row: Row): string | null {
  return row.item.source === 'unreadable-emergency' ? null : row.item.kind
}

function rowEntityId(row: Row): string | null {
  return row.item.source === 'unreadable-emergency' ? null : row.item.entityId
}

function rowEntityType(row: Row): string | null {
  return row.item.source === 'unreadable-emergency' ? null : row.item.entityType
}

function rowTime(row: Row): number | null {
  const item = row.item
  if (item.source === 'record') return item.updatedAt
  if (item.source === 'emergency') return item.at
  if (item.source === 'unreadable-record') return item.updatedAt
  return null
}

async function downloadText(row: Row): Promise<void> {
  busyKey.value = row.key
  try {
    const text = await readText(row)
    if (text === null) return
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = downloadName(rowKind(row), rowEntityId(row), rowTime(row) ?? Date.now())
    link.hidden = true
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch {
    setMessage(row, 'Download failed. Nothing was changed.')
  } finally {
    busyKey.value = null
  }
}

function canDiscard(row: Row): row is { key: string; item: DiagnosticsDraft } {
  return row.item.source === 'record' && !isLive(row)
}

async function discard(row: Row): Promise<void> {
  if (!canDiscard(row)) return
  const d = row.item
  const diagnostics = api()
  const confirm = page().prksConfirmDialog
  if (!diagnostics || !confirm) {
    setMessage(row, discardFailureText('failed'))
    return
  }
  const what = `${kindText(d.kind, d.entityType)} for ${entityText(d.entityType, d.entityId)}`
  const message =
    d.lineage === 'unknown'
      ? `This draft may belong to another browser tab that is still open or temporarily unresponsive. Discarding it will permanently remove its recovery copy (${lengthText(d.bodyLength)}) of ${what}. If that tab contains unsaved changes, those changes could be lost. Only discard this draft if you are certain you no longer need it.`
      : `This permanently removes the recovery copy (${lengthText(d.bodyLength)}) of ${what} from this browser. It does not change anything on the server. Copy or download the text first if you may need it.`
  const ok = await confirm({ title: 'Discard this draft?', message, confirmLabel: 'Discard draft', cancelLabel: 'Cancel', danger: true })
  if (!ok) return
  busyKey.value = row.key
  try {
    const result = await diagnostics.discard({
      ...d.expect,
      kind: d.kind,
      entityType: d.entityType,
      entityId: d.entityId,
      allowUnknownOwner: d.lineage === 'unknown',
    })
    if (result === 'deleted') {
      await load()
      listMessage.value = 'Draft discarded.'
      return
    }
    await load()
    listMessage.value = discardFailureText(result)
  } finally {
    busyKey.value = null
  }
}
</script>

<template>
  <div class="editor-recovery-diag" data-prks-role="editor-recovery-diagnostics">
    <p class="prks-settings-hint" aria-live="polite" data-prks-role="editor-recovery-diag-summary">
      <template v-if="loadFailed">Editor drafts could not be listed in this browser.</template>
      <template v-else-if="!inventory">{{ loading ? 'Loading editor drafts…' : 'Editor drafts are listed when Diagnostics opens.' }}</template>
      <template v-else>{{ summaryText(inventory) }}</template>
    </p>
    <PrksInlineMessage v-if="inventory && storageText(inventory)" tone="warning" data-prks-role="editor-recovery-diag-storage">{{ storageText(inventory) }}</PrksInlineMessage>
    <PrksInlineMessage v-if="inventory && inventory.emergencyStorage === 'unavailable'" tone="warning" data-prks-role="editor-recovery-diag-emergency-storage">
      Copies held for closing tabs could not be checked: this browser refused access to local storage.
    </PrksInlineMessage>
    <PrksInlineMessage v-if="inventory && budgetText(inventory)" tone="warning" data-prks-role="editor-recovery-diag-budget">{{ budgetText(inventory) }}</PrksInlineMessage>
    <PrksInlineMessage v-if="listMessage" status data-prks-role="editor-recovery-diag-message">{{ listMessage }}</PrksInlineMessage>
    <ul v-if="rows.length" class="editor-recovery-diag__list">
      <li
        v-for="row in rows"
        :key="row.key"
        class="editor-recovery-diag__row"
        data-prks-role="editor-recovery-diag-row"
        :data-source="row.item.source"
        :data-draft-id="row.item.source === 'unreadable-emergency' ? undefined : row.item.draftId"
        :data-entity-id="rowEntityId(row) ?? undefined"
      >
        <p class="editor-recovery-diag__title">
          <span>{{ kindText(rowKind(row), rowEntityType(row)) }}</span>
          <template v-if="row.item.source !== 'unreadable-emergency'">
            ·
            <a v-if="entityHref(rowEntityType(row), rowEntityId(row))" :href="entityHref(rowEntityType(row), rowEntityId(row))!">{{ entityText(rowEntityType(row), rowEntityId(row)) }}</a>
            <span v-else>{{ entityText(rowEntityType(row), rowEntityId(row)) }}</span>
          </template>
        </p>
        <p class="editor-recovery-diag__meta" data-prks-role="editor-recovery-diag-meta">
          <template v-if="row.item.source === 'record'">
            Last typed {{ typedAtText(row.item.updatedAt) }} · {{ lengthText(row.item.bodyLength) }} ·
            {{ diagnosticsOriginText(row.item.lineage) }} · {{ draftStateText(row.item) }} ·
            {{ pipelineText(row.item.pipelineState) }}<template v-if="row.item.baseRevision !== null">, typed on revision {{ row.item.baseRevision }}</template>
            <template v-for="flag in draftFlags(row.item)" :key="flag"> · {{ flag }}</template>
          </template>
          <template v-else-if="row.item.source === 'emergency'">
            Captured {{ typedAtText(row.item.at) }}<template v-if="row.item.bodyLength !== null"> · {{ lengthText(row.item.bodyLength) }}</template> ·
            {{ diagnosticsOriginText(row.item.lineage) }} · {{ emergencyText(row.item) }}
          </template>
          <template v-else-if="row.item.source === 'unreadable-record'">{{ unreadableRecordText(row.item) }}</template>
          <template v-else>Emergency copy · {{ unreadableEmergencyText(row.item) }}</template>
        </p>
        <PrksInlineMessage v-if="isLive(row)" data-prks-role="editor-recovery-diag-live">It is open in an editor now. Continue there.</PrksInlineMessage>
        <div class="editor-recovery-diag__actions">
          <template v-if="canReadText(row)">
            <PrksButton size="sm" :disabled="busyKey !== null" data-prks-role="editor-recovery-diag-view" @click="toggleText(row)">{{ opened[row.key] ? 'Hide text' : (row.item.source === 'unreadable-emergency' ? 'View raw data' : 'View text') }}</PrksButton>
            <PrksButton size="sm" :disabled="busyKey !== null" data-prks-role="editor-recovery-diag-copy" @click="copyText(row)">{{ row.item.source === 'unreadable-emergency' ? 'Copy raw data' : 'Copy text' }}</PrksButton>
            <PrksButton size="sm" :disabled="busyKey !== null" data-prks-role="editor-recovery-diag-download" @click="downloadText(row)">Download</PrksButton>
          </template>
          <PrksButton
            v-if="canDiscard(row)"
            size="sm"
            variant="quiet-danger"
            :busy="busyKey === row.key"
            busy-label="Discarding…"
            :disabled="busyKey !== null"
            data-prks-role="editor-recovery-diag-discard"
            @click="discard(row)"
          >Discard draft</PrksButton>
        </div>
        <PrksInlineMessage v-if="rowMessage[row.key]" status data-prks-role="editor-recovery-diag-row-message">{{ rowMessage[row.key] }}</PrksInlineMessage>
        <label v-if="opened[row.key]" class="editor-recovery-diag__text-wrap">
          <span class="prks-sr-only">Recovered text</span>
          <textarea class="prks-input editor-recovery-diag__text" readonly :value="opened[row.key]!.text" data-prks-role="editor-recovery-diag-text"></textarea>
          <span v-if="opened[row.key]!.truncated" class="prks-settings-hint">Showing the first {{ PREVIEW_CHARS.toLocaleString() }} characters. Copy or Download carries the whole text.</span>
        </label>
      </li>
    </ul>
    <div class="prks-backup-restore-row">
      <PrksButton :busy="loading" busy-label="Refreshing…" data-prks-role="editor-recovery-diag-refresh" @click="refresh">Refresh drafts</PrksButton>
    </div>
  </div>
</template>
