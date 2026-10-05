/**
 * Pure Work-detail presentation facts.
 * HTML helpers and viewer modules stay with the classic runtime.
 */
export type WorkDetailKind = 'pdf' | 'video' | 'empty'

export interface WorkDetailDescription {
  readonly workId: string
  readonly kind: WorkDetailKind
  readonly hasFile: boolean
  readonly pdfViewerActive: boolean
  readonly showHeader: boolean
  readonly title: string
  readonly folderTitle: string
  readonly folderId: string
  readonly peopleCount: number | null
  readonly tagCount: number | null
}

function nestedFolderField(work: Record<string, unknown>, field: 'title' | 'id'): string {
  const folder = work.folder
  if (!folder || typeof folder !== 'object') return ''
  const value = (folder as Record<string, unknown>)[field]
  return value ? String(value) : ''
}

/** People count prefers the effective role overlay, then the editor Work. */
export function peopleCountForWork(
  rolesForRel: Record<string, unknown> | null | undefined,
  work: Record<string, unknown>,
): number | null {
  if (rolesForRel && Array.isArray(rolesForRel.roles)) return rolesForRel.roles.length
  if (Array.isArray(work.roles)) return work.roles.length
  return null
}

/**
 * Header, kind, and relationship counts for one already-resolved Work.
 * A PDF with a file hides the page header. The PDF toolbar owns that title.
 */
export function describeWorkDetail(
  work: Record<string, unknown>,
  inferredKind: string,
  rolesForRel?: Record<string, unknown> | null,
): WorkDetailDescription {
  const hasFile = !!work.file_path
  const kind: WorkDetailKind = inferredKind === 'pdf' || inferredKind === 'video' ? inferredKind : 'empty'
  const pdfViewerActive = inferredKind === 'pdf' && hasFile
  const folderTitle = work.folder_title ? String(work.folder_title) : nestedFolderField(work, 'title')
  const folderId = work.folder_id ? String(work.folder_id) : nestedFolderField(work, 'id')
  return {
    workId: String(work.id ?? ''),
    kind,
    hasFile,
    pdfViewerActive,
    showHeader: !pdfViewerActive,
    title: String(work.title || '').trim() || 'Document',
    folderTitle,
    folderId,
    peopleCount: peopleCountForWork(rolesForRel, work),
    tagCount: Array.isArray(work.tags) ? work.tags.length : null,
  }
}
