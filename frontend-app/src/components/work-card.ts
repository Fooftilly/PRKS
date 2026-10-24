/**
 * Shared Work-card presentation helpers. Routes hand already-effective rows;
 * this module does not fetch, queue, or overlay.
 */

export interface PrksWorkCardWork {
  id?: unknown
  title?: unknown
  status?: unknown
  file_path?: unknown
  thumb_url?: unknown
  thumb_page?: unknown
  doc_type?: unknown
  year?: unknown
  published_date?: unknown
  file_size_bytes?: unknown
  linked_authors?: unknown
  author_text?: unknown
  primary_author?: unknown
  primary_editor?: unknown
  source_kind?: unknown
  source_url?: unknown
  provider?: unknown
  provider_id?: unknown
}

export interface PrksWorkCardOptions {
  subtitle?: string
  thumbPage?: unknown
  hideDocTypeBadge?: boolean
  suppressThumbnail?: boolean
}

export const WORK_THUMB_PLACEHOLDER =
  'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='

export function workCardTitle(work: PrksWorkCardWork | null | undefined): string {
  if (!work || work.title == null || work.title === '') return 'Untitled'
  return String(work.title)
}

export function workCardId(work: PrksWorkCardWork | null | undefined): string {
  if (!work || work.id == null) return ''
  return String(work.id)
}

export function workCardHref(work: PrksWorkCardWork | null | undefined): string {
  const id = workCardId(work)
  return id ? `#/works/${id}` : ''
}

export function workCardStatus(work: PrksWorkCardWork | null | undefined): string {
  return work && work.status ? String(work.status) : ''
}

export function workCardStatusClass(status: string): string {
  return status ? status.replace(/ /g, '.') : ''
}

/** Linked Author(s), else author_text, else linked Editor. Never HTML. */
export function workCardCreditText(work: PrksWorkCardWork | null | undefined): string {
  if (!work) return ''
  let name = work.linked_authors != null ? String(work.linked_authors).trim() : ''
  if (!name && work.primary_author != null) name = String(work.primary_author).trim()
  if (name) return 'Author: ' + name
  if (work.author_text != null) {
    const at = String(work.author_text).trim()
    if (at) return 'Author: ' + at
  }
  name = work.primary_editor != null ? String(work.primary_editor).trim() : ''
  if (name) return 'Editor: ' + name
  return ''
}

/** `year` field, else leading YYYY from ISO `published_date`. Never HTML. */
export function workCardYearPlain(work: PrksWorkCardWork | null | undefined): string {
  if (!work) return ''
  const y = typeof work.year === 'string' ? work.year.trim() : ''
  if (y) return y
  const pd = typeof work.published_date === 'string' ? work.published_date.trim() : ''
  if (!pd) return ''
  const m = pd.match(/^(\d{4})/)
  return m ? m[1] : pd
}

export function workCardFileSizeLabel(work: PrksWorkCardWork | null | undefined): string {
  const raw = work && work.file_size_bytes
  const n = raw != null && raw !== '' ? Number(raw) : NaN
  if (!Number.isFinite(n) || n <= 0) return ''
  const mb = n / (1024 * 1024)
  const s = mb >= 0.01 ? mb.toFixed(2) : mb.toFixed(3)
  return `${s} MB`
}

export function workCardHasPdf(work: PrksWorkCardWork | null | undefined): boolean {
  const filePath = work && work.file_path ? String(work.file_path).trim() : ''
  return !!filePath && filePath.startsWith('/api/pdfs/')
}

/**
 * Infer pdf vs video for the thumb slot. Matches `prksInferWorkSourceKind`:
 * explicit source_kind wins; otherwise a file_path is pdf and a source_url is video.
 */
export function workCardSourceKind(work: PrksWorkCardWork | null | undefined): string {
  const infer = window.prksInferWorkSourceKind
  if (typeof infer === 'function') return infer(work)
  if (!work) return ''
  const sk = String(work.source_kind || '').trim().toLowerCase()
  if (sk === 'video') return 'video'
  if (sk === 'pdf') return 'pdf'
  const fp = String(work.file_path || '').trim()
  if (fp) return 'pdf'
  if (String(work.source_url || '').trim()) return 'video'
  return sk
}

export function workCardResolvedThumbPage(
  work: PrksWorkCardWork | null | undefined,
  options: PrksWorkCardOptions = {},
): number {
  const thumbPage = options.thumbPage != null ? options.thumbPage : work?.thumb_page
  const p = thumbPage != null && String(thumbPage).trim() !== '' ? Number(thumbPage) : null
  return p && Number.isFinite(p) && p > 0 ? Math.floor(p) : 1
}

export function workCardThumbUrl(
  work: PrksWorkCardWork | null | undefined,
  options: PrksWorkCardOptions = {},
): string {
  if (!work || options.suppressThumbnail === true) return ''
  const id = workCardId(work)
  if (workCardHasPdf(work)) {
    const wid = encodeURIComponent(id.trim())
    if (!wid) return ''
    return `/api/works/${wid}/thumbnail?page=${encodeURIComponent(String(workCardResolvedThumbPage(work, options)))}`
  }
  if (workCardSourceKind(work) === 'video' && work.thumb_url) {
    return String(work.thumb_url).trim()
  }
  return ''
}

export function workCardIsVideoKind(work: PrksWorkCardWork | null | undefined): boolean {
  return !workCardHasPdf(work) && workCardSourceKind(work) === 'video'
}

export function workCardEmptyThumbTitle(
  work: PrksWorkCardWork | null | undefined,
  options: PrksWorkCardOptions = {},
): string {
  if (options.suppressThumbnail === true) return 'Preview not available offline'
  return workCardIsVideoKind(work) ? 'No video preview' : 'No preview'
}

/** Cached pages must not request thumbnails. Online pages keep them. */
export function workCardThumbOptions(
  offlineCached: boolean,
  extra: PrksWorkCardOptions = {},
): PrksWorkCardOptions {
  if (offlineCached) return { ...extra, suppressThumbnail: true }
  return extra
}

function workCardSourceField(work: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = work[key]
    if (value != null && value !== '') return String(value)
  }
  return ''
}

/**
 * Collection lifetime key: Work id plus effective thumb identity.
 * Parent noise (draft text, generation, overlay titles) must not live here.
 */
export function workCardCollectionFingerprint(
  works: readonly unknown[] | null | undefined,
  extra: { suppressThumbnail?: boolean } = {},
): string {
  const rows = Array.isArray(works) ? works : []
  const lines = rows.map((raw) => {
    const work = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
    return [
      workCardSourceField(work, 'id'),
      workCardSourceField(work, 'thumb_page', 'thumbPage'),
      workCardSourceField(work, 'thumb_url', 'thumbUrl'),
      workCardSourceField(work, 'file_path', 'filePath'),
      workCardSourceField(work, 'source_kind', 'sourceKind'),
    ].join('\t')
  })
  return `${extra.suppressThumbnail ? '1' : '0'}\n${lines.join('\n')}`
}
