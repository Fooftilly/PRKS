/** Backup progress copy. The bytes and the request stay in `api.js`. */

export interface BackupProgressEvent {
  phase?: string
  percent?: number
  files_done?: number
  files_total?: number
  error?: string
  token?: string
  filename?: string
  warnings?: string[]
}

export function backupPhaseLabel(event: BackupProgressEvent | null | undefined): string {
  const phase = String(event && event.phase ? event.phase : '')
  const filesDone = Number(event && event.files_done) || 0
  const filesTotal = Number(event && event.files_total) || 0
  const filePart = filesTotal > 0 ? ` ${filesDone} of ${filesTotal} files.` : ''
  if (phase === 'snapshot') return 'Snapshotting database…'
  if (phase === 'archiving') return 'Packing files…' + filePart
  if (phase === 'verifying') return 'Verifying backup…' + filePart
  if (phase === 'ready') return 'Download starting…'
  if (phase === 'cancelled') return 'Backup cancelled.'
  if (phase === 'failed') return (event && event.error) || 'Backup could not be created.'
  return 'Preparing backup…'
}

export function formatBackupCreated(iso: unknown): string {
  const raw = String(iso || '').trim()
  if (!raw) return ''
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  try {
    return date.toLocaleString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return raw
  }
}

export function backupSummaryRows(summary: Record<string, unknown> | null | undefined): Array<{ label: string; value: string }> {
  const source = summary && typeof summary === 'object' ? summary : {}
  const pdfs = source.pdf_files ?? source.managed_pdfs ?? ''
  const rows: Array<[string, unknown]> = [
    ['Created', formatBackupCreated(source.created_at)],
    ['Works', source.works ?? ''],
    ['PDFs', pdfs],
    ['Persons', source.persons ?? ''],
    ['Annotations', source.annotations ?? ''],
  ]
  const out: Array<{ label: string; value: string }> = []
  for (const [label, raw] of rows) {
    const value = String(raw ?? '')
    if (!value && value !== '0') continue
    out.push({ label, value })
  }
  return out
}

export function textIndexSummary(out: Record<string, unknown>): string {
  const processed = Number(out.processed || 0)
  const updated = Number(out.updated || 0)
  const unchanged = Number(out.unchanged || 0)
  const empty = Number(out.empty || 0)
  const failed = Number(out.failed || 0)
  const orphans = Number(out.removed_orphans || 0)
  const parts: string[] = []
  if (updated) parts.push(`${updated} rebuilt`)
  if (unchanged) parts.push(`${unchanged} already current`)
  if (empty) parts.push(`${empty} contained no text`)
  if (failed) parts.push(`${failed} failed`)
  const pdfLabel = processed === 1 ? 'PDF' : 'PDFs'
  let message = `Done. ${processed} ${pdfLabel} checked`
  message += parts.length ? `: ${parts.join(', ')}.` : '.'
  if (orphans) {
    const entryLabel = orphans === 1 ? 'entry was' : 'entries were'
    message += ` ${orphans} stale index ${entryLabel} removed.`
  }
  return message
}
