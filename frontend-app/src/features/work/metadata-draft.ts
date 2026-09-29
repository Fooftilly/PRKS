/**
 * Work metadata edit session.
 *
 * The draft and baseline are what the form opened with and what the user has
 * typed since. They are not the server base. Saves measure against the
 * acknowledged observed fields in the durable editor. A pending overlay may
 * seed a new session; it must not become the baseline later, and it must not
 * be what a save diffs against.
 */

export const WORK_META_FIELDS = [
  'title',
  'status',
  'doc_type',
  'year',
  'published_date',
  'publisher',
  'location',
  'edition',
  'journal',
  'volume',
  'issue',
  'pages',
  'isbn',
  'doi',
  'source_url',
  'abstract',
  'thumb_page',
  'author_text',
] as const

export type WorkMetaField = (typeof WORK_META_FIELDS)[number]

export type WorkMetaDraft = Record<WorkMetaField, string>

export type WorkMetaGroupName = 'identity' | 'status' | 'bib'

const PDF_BIB_FIELDS: readonly WorkMetaField[] = [
  'author_text',
  'year',
  'published_date',
  'publisher',
  'location',
  'edition',
  'journal',
  'volume',
  'issue',
  'pages',
  'isbn',
  'doi',
  'source_url',
  'abstract',
  'thumb_page',
]

export function blankWorkMetaDraft(): WorkMetaDraft {
  const draft = {} as WorkMetaDraft
  for (const field of WORK_META_FIELDS) draft[field] = ''
  return draft
}

export function cloneWorkMetaDraft(source: Partial<Record<string, unknown>> | null | undefined): WorkMetaDraft {
  const draft = blankWorkMetaDraft()
  if (!source) return draft
  for (const field of WORK_META_FIELDS) {
    const value = source[field]
    draft[field] = value == null ? '' : String(value)
  }
  return draft
}

export function workMetaGroupFields(group: string, sourceKind: string): readonly WorkMetaField[] {
  if (group === 'identity') return ['title', 'doc_type']
  if (group === 'status') return ['status']
  if (group === 'bib') return sourceKind === 'video' ? ['author_text'] : PDF_BIB_FIELDS
  return []
}

export function workMetaDraftIsDirty(
  draft: Partial<Record<string, unknown>> | null | undefined,
  baseline: Partial<Record<string, unknown>> | null | undefined,
): boolean {
  if (!draft || !baseline) return false
  return WORK_META_FIELDS.some((field) => String(draft[field] ?? '') !== String(baseline[field] ?? ''))
}

/**
 * A successful group save moves the baseline only for fields the user has not
 * typed past since the snapshot. Other groups stay dirty. The draft itself is
 * left alone.
 */
export function commitWorkMetaBaseline(
  draft: WorkMetaDraft,
  baseline: WorkMetaDraft,
  snapshot: Partial<Record<string, unknown>>,
  fields: readonly string[],
): void {
  for (const field of fields) {
    if (!(WORK_META_FIELDS as readonly string[]).includes(field)) continue
    const key = field as WorkMetaField
    const saved = snapshot[key] == null ? '' : String(snapshot[key])
    if (draft[key] === saved) baseline[key] = saved
  }
}

/** An acknowledgement may rewrite an unfocused field. A focused field stays. */
export function acceptWorkMetaField(
  draft: WorkMetaDraft,
  baseline: WorkMetaDraft,
  field: string,
  value: unknown,
  focusedField: string,
): void {
  if (!(WORK_META_FIELDS as readonly string[]).includes(field)) return
  if (focusedField === field) return
  const key = field as WorkMetaField
  const next = value == null ? '' : String(value)
  draft[key] = next
  baseline[key] = next
}

export interface WorkMetaSessionView {
  destroyed?: boolean
  mode?: string
  session?: number
  draftWorkId?: string
  /** Null while beginRoute has cleared the entity and the same Work is coming back. */
  entityWorkId?: string | null
  routeName?: string | null
  routeWorkId?: string | null
  panelOwnerTabId?: string | null
  tabId?: string
}

/**
 * True only while this edit session still owns this Work.
 * A bumped token, a different route, a different entity, or a panel that now
 * shows another tab means an in-flight save must not write and must not settle.
 */
export function workMetaSessionStill(view: WorkMetaSessionView, workId: string, session: number): boolean {
  if (!view || view.destroyed) return false
  if (view.mode !== 'metadata') return false
  if (view.session !== session) return false
  if (!workId || String(view.draftWorkId || '') !== String(workId)) return false
  if (view.entityWorkId && String(view.entityWorkId) !== String(workId)) return false
  if (view.routeName === 'work' && view.routeWorkId && String(view.routeWorkId) !== String(workId)) return false
  if (view.panelOwnerTabId && view.tabId && String(view.panelOwnerTabId) !== String(view.tabId)) return false
  return true
}
