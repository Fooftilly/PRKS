/**
 * Wording for Settings → Diagnostics editor recovery drafts (#466 slice 5).
 * Same rules as `labels.ts`: a recovery copy is never "saved".
 */
import type {
  DiagnosticsDraft,
  DiagnosticsEmergency,
  DiagnosticsUnreadableEmergency,
  DiagnosticsUnreadableRecord,
  DiscardResult,
  RecoveryInventory,
  TextResult,
} from '../../lifecycle/editor-recovery/diagnostics'
import { lengthText } from './labels'

type Lineage = DiagnosticsDraft['lineage']

export function kindText(kind: string | null, entityType: string | null): string {
  if (kind === 'work-research-note') return 'Research Notes'
  if (kind === 'work-private-note') return 'Work Reminders'
  if (kind === 'folder-private-note') return 'Folder Reminders'
  if (entityType === 'folder') return 'Folder draft'
  return 'Editor draft'
}

export function entityText(entityType: string | null, entityId: string | null): string {
  const label = entityType === 'folder' ? 'Folder' : entityType === 'work' ? 'Work' : 'Item'
  return entityId ? `${label} ${entityId}` : `${label} unknown`
}

/** Same-app link to the entity the draft was typed for, or null when it cannot be named. */
export function entityHref(entityType: string | null, entityId: string | null): string | null {
  if (!entityId) return null
  if (entityType === 'work') return '#/works/' + encodeURIComponent(entityId)
  if (entityType === 'folder') return '#/folders/' + encodeURIComponent(entityId)
  return null
}

export function diagnosticsOriginText(lineage: Lineage): string {
  switch (lineage) {
    case 'self-live':
    case 'other-live':
      return 'Open in an editor now'
    case 'same-runtime-orphan':
      return 'This browser tab, from an editor that is closed'
    case 'dead-runtime':
      return 'A browser tab that was closed'
    default:
      return 'Another tab that may still be open'
  }
}

export function draftStateText(d: Pick<DiagnosticsDraft, 'status' | 'lineage'>): string {
  if (d.lineage === 'self-live' || d.lineage === 'other-live') return 'Being edited'
  return d.status === 'tail-missing' ? 'Incomplete: the newest changes were not captured' : 'Complete'
}

/** Flags worth a glance, never reasons anything was removed. */
export function draftFlags(d: DiagnosticsDraft): string[] {
  const flags: string[] = []
  if (d.superseded) flags.push('A newer version is already on the server')
  if (d.deletedHere) flags.push('Its Work was deleted from this browser')
  if (d.fromEmergency) flags.push('Captured as the tab closed')
  if (d.old) flags.push('Not changed for over 30 days')
  return flags
}

export function unreadableRecordText(u: Pick<DiagnosticsUnreadableRecord, 'reason'>): string {
  return u.reason === 'newer-version'
    ? 'Written by a newer version of PRKS. It is kept unchanged for that version.'
    : 'The recovery record is damaged. It is kept unchanged; its text may still be readable.'
}

export function emergencyText(e: Pick<DiagnosticsEmergency, 'lineage' | 'bodyLength'>): string {
  if (e.bodyLength === null) return 'Only a note that newer text existed was captured; the text itself is missing.'
  if (e.lineage === 'self-live' || e.lineage === 'other-live') return 'Held for a tab that is still open.'
  return 'Captured as a tab was hidden or closed, not yet moved into recovery storage.'
}

export function unreadableEmergencyText(u: Pick<DiagnosticsUnreadableEmergency, 'reason'>): string {
  switch (u.reason) {
    case 'read-failed':
      return 'This browser refused to read it just now. It is kept for a later try.'
    case 'newer-version':
      return 'Written by a newer version of PRKS. It is kept unchanged for that version.'
    default:
      return 'It is damaged and cannot be read as drafts. Its raw data can still be copied.'
  }
}

export function storageText(inv: Pick<RecoveryInventory, 'storage'>): string | null {
  switch (inv.storage) {
    case 'unavailable':
    case 'blocked':
      return 'Recovery storage is unavailable in this browser, so stored drafts cannot be listed right now. Copies held for closing tabs are still shown.'
    case 'failed':
      return 'Recovery storage could not be read. Nothing was changed. Refresh to try again.'
    default:
      return null
  }
}

function approxSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function summaryText(inv: RecoveryInventory): string {
  const count = inv.drafts.length + inv.emergency.length + inv.unreadable.length + inv.unreadableEmergency.length
  if (!count) return 'No editor drafts are kept in this browser.'
  const head = `${count} editor draft${count === 1 ? '' : 's'} kept in this browser`
  const about = inv.sizeIncomplete ? 'at least about' : 'about'
  return `${head}, ${about} ${approxSize(inv.approxBytes)} (${lengthText(inv.totalChars)}).`
}

export function budgetText(inv: Pick<RecoveryInventory, 'overBudget'>): string | null {
  return inv.overBudget
    ? 'Recovery copies take more than 256 MB in this browser. Copy what you need and discard drafts you no longer want; nothing is removed automatically.'
    : null
}

export function textFailureText(code: Extract<TextResult, { ok: false }>['code'], refreshed = true): string {
  switch (code) {
    case 'live':
      return 'It is open in an editor now. Continue there.'
    case 'changed':
      return 'This draft changed since the list was read.' + (refreshed ? ' The list was refreshed.' : '')
    case 'missing':
      return 'Its text could not be read.'
    default:
      return 'Reading it failed. Nothing was changed.'
  }
}

export function discardFailureText(result: Exclude<DiscardResult, 'deleted'>, refreshed = true): string {
  switch (result) {
    case 'live':
      return 'It is open in an editor now, so it was not discarded.'
    case 'unknown-owner':
      return 'Another tab may still be editing it now. Review it again before discarding.'
    case 'changed':
      return 'This draft changed since the list was read, so it was not discarded.' + (refreshed ? ' The list was refreshed.' : '')
    default:
      return 'Discarding failed. Nothing was changed.'
  }
}

/** A plain file name for a downloaded copy: no path separators or odd characters from ids. */
export function downloadName(kind: string | null, entityId: string | null, at: number): string {
  const what = (kindText(kind, null) || 'draft').toLowerCase().replace(/[^a-z0-9]+/g, '-')
  const id = (entityId || 'unknown').replace(/[^A-Za-z0-9_-]+/g, '').slice(0, 64) || 'unknown'
  const date = new Date(at)
  const stamp = Number.isNaN(date.getTime()) ? 'undated' : date.toISOString().slice(0, 16).replace(/[-:T]/g, '')
  return `prks-recovered-${what}-${id}-${stamp}.txt`
}
