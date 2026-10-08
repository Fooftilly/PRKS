/**
 * Wording for editor recovery. Recovery copies are never "saved": that word
 * is reserved for what the server acknowledged, and queued work keeps the
 * existing "waiting to sync" language.
 */
import type { RecoveryCandidateView, RecoveryCurrentNote, RecoveryNoticeView } from './types'

export function noticeText(view: RecoveryNoticeView, subject: string): string {
  const complete = view.drafts - view.incomplete
  if (view.drafts === 1) {
    return view.incomplete
      ? `Incomplete unsaved ${subject} from an earlier session are available.`
      : `Unsaved ${subject} from an earlier session are available.`
  }
  const head = `${view.drafts} unsaved ${subject} drafts from earlier sessions are available.`
  if (!view.incomplete) return head
  return complete ? `${head} ${view.incomplete} of them ${view.incomplete === 1 ? 'is' : 'are'} incomplete.` : `${head} All are incomplete.`
}

export function unprotectedText(code: string): string {
  const why =
    code === 'quota'
      ? 'recovery storage on this device is full'
      : code === 'unavailable' || code === 'blocked'
        ? 'recovery storage on this device is unavailable'
        : 'the recovery copy could not be written'
  return `Not protected if the browser closes: ${why}. Keep this tab open until the note saves.`
}

export function originText(c: Pick<RecoveryCandidateView, 'lineage' | 'samePane'>): string {
  switch (c.lineage) {
    case 'same-runtime-orphan':
      return c.samePane ? 'This pane, before the page reloaded' : 'Another pane in this browser tab'
    case 'dead-runtime':
      return 'A browser tab that was closed'
    case 'self-live':
    case 'other-live':
      return 'Open in another tab or pane'
    default:
      return 'Another tab that may still be open'
  }
}

export function completenessText(c: Pick<RecoveryCandidateView, 'status' | 'body' | 'lineage'>): string {
  if (c.status === 'tail-missing') return 'Incomplete: the newest changes were not captured'
  if (c.lineage === 'self-live' || c.lineage === 'other-live') return 'Being edited'
  return c.body === null ? 'Unreadable' : 'Complete'
}

export function reasonText(c: Pick<RecoveryCandidateView, 'reason' | 'typedOnRevision'>): string {
  switch (c.reason) {
    case 'multiple-drafts':
      return 'More than one unsaved draft exists, so none was restored automatically.'
    case 'base-advanced':
      return c.typedOnRevision !== null
        ? `The note changed after this text was typed on revision ${c.typedOnRevision}.`
        : 'The note changed after this text was typed.'
    case 'foreign-queue':
      return 'Other changes to this note are waiting to sync.'
    case 'base-unverified':
      return 'The current note could not be checked with the server.'
    case 'queue-unknown':
      return 'Changes waiting to sync could not be read, so nothing can be restored now.'
    case 'ownership-unknown':
      return 'It may still be open in another tab. Close that tab, then review again.'
    case 'live-elsewhere':
      return 'It is open in another tab or pane. Continue there.'
    case 'other-draft-live':
      return 'Another tab or pane is still editing this note, so nothing was restored automatically.'
    case 'tail-missing':
      return 'Only earlier text was captured. Copy what is needed before discarding it.'
    case 'body-missing':
      return 'Its text could not be read.'
    case 'dirty-session':
      return 'This Work has unsaved changes in another pane. Finish there first.'
    case 'editor-dirty':
      return 'The editor already shows other text. Compare before choosing.'
    case 'saved':
      return 'This text is already the saved note.'
    case 'queued':
      return 'This text is already waiting to sync.'
    default:
      return 'It can be restored as it is.'
  }
}

export function pipelineText(state: string | null): string {
  switch (state) {
    case 'queued':
      return 'Was waiting to sync'
    case 'blocked':
      return 'Was waiting behind an earlier save'
    case 'saving':
      return 'Was being saved'
    case 'error':
      return 'Its save had failed'
    case 'conflict':
      return 'Its save was in conflict'
    default:
      return 'Not saved'
  }
}

export function currentNoteText(current: RecoveryCurrentNote): string {
  const revision = current.revision === null ? 'not loaded' : `revision ${current.revision}`
  const verified = current.source === 'server' ? 'checked with the server' : 'cached copy, not checked with the server'
  return `${revision}, ${verified}`
}

export function queueText(current: RecoveryCurrentNote): string {
  if (current.queue === 'unknown') return 'Changes waiting to sync could not be read'
  if (current.queue === 'none') return current.unsaved ? 'This editor has unsaved changes' : 'Nothing waiting to sync'
  const n = current.queued
  return `${n} change${n === 1 ? '' : 's'} waiting to sync${current.unsaved ? '; this editor also has unsaved changes' : ''}`
}

export function typedAtText(at: number): string {
  const date = new Date(at)
  if (Number.isNaN(date.getTime())) return 'Unknown time'
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function lengthText(length: number): string {
  return `${length.toLocaleString()} character${length === 1 ? '' : 's'}`
}

export function failureText(code: string): string {
  switch (code) {
    case 'changed':
      return 'This draft changed in another tab or pane. The list was refreshed.'
    case 'current-changed':
      return 'The current note changed while you compared. Compare again.'
    case 'stale':
      return 'The editor this review was opened for has changed. Nothing was changed.'
    default:
      return 'That did not work. Nothing was changed.'
  }
}
