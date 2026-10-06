/** Inbox page size. The coordinator does not slice the scan; Vue does. */
export const PROCESSING_PAGE_SIZE = 25

export interface ProcessingRoleLink {
  person_id: string
  person_name: string
  role_type: string
}

export interface ProcessingTagLink {
  id: string
  name: string
}

export interface ProcessingFileDraft {
  title: string
  status_draft: string
  abstract: string
  source_url: string
  published_date: string
  year: string
  publisher: string
  location: string
  edition: string
  journal: string
  volume: string
  issue: string
  pages: string
  isbn: string
  doi: string
  doc_type: string
  private_notes: string
  thumb_page: string
  target_folder_id: string
  roles: ProcessingRoleLink[]
  tags: ProcessingTagLink[]
}

export interface ProcessingFileRow {
  id: string
  filename: string
  relPath: string
  status: string
  statusLabel: string
  exists: boolean
  lastError: string
  canImport: boolean
  canPreview: boolean
  sourceHint: string
  draft: ProcessingFileDraft
}

export interface ProcessingPerson {
  id: string
  name: string
  raw: Record<string, unknown>
}

export interface ProcessingFolder {
  id: string
  title: string
}

export interface ProcessingTagOption {
  id: string
  name: string
  aliases: string[]
}

export interface ProcessingResume {
  visibleCount?: number | null
}

export interface ProcessingProjectionInput {
  files: unknown
  people: unknown
  folders: unknown
  roleTypes: unknown
  domPrefix: string
  generation?: number
  resume?: ProcessingResume | null
}

export interface ProcessingProjection {
  generation: number
  domPrefix: string
  roleTypes: string[]
  files: ProcessingFileRow[]
  people: ProcessingPerson[]
  folders: ProcessingFolder[]
  visibleCount: number
}

function text(value: unknown): string {
  return value == null ? '' : String(value)
}

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object') return null
  return value as Record<string, unknown>
}

export function processingPersonName(person: Record<string, unknown> | null): string {
  if (!person) return ''
  const first = text(person.first_name).trim()
  const last = text(person.last_name).trim()
  const full = [first, last].filter(Boolean).join(' ').trim()
  return full || text(person.id).trim()
}

function normalizeRoles(value: unknown): ProcessingRoleLink[] {
  if (!Array.isArray(value)) return []
  const roles: ProcessingRoleLink[] = []
  value.forEach((entry) => {
    const row = record(entry)
    if (!row) return
    const personId = text(row.person_id).trim()
    if (!personId) return
    roles.push({
      person_id: personId,
      person_name: text(row.person_name).trim() || personId,
      role_type: text(row.role_type).trim() || 'Author',
    })
  })
  return roles
}

function normalizeTags(value: unknown): ProcessingTagLink[] {
  if (!Array.isArray(value)) return []
  const tags: ProcessingTagLink[] = []
  value.forEach((entry) => {
    const row = record(entry)
    if (!row) return
    const id = text(row.id).trim()
    const name = text(row.name).trim()
    if (!id && !name) return
    tags.push({ id, name })
  })
  return tags
}

export function normalizeProcessingFile(value: unknown): ProcessingFileRow | null {
  const row = record(value)
  if (!row) return null
  const id = text(row.id).trim()
  if (!id) return null
  const status = text(row.status).trim() || 'pending'
  const exists = !!row.exists
  const canImport = status !== 'missing' && status !== 'error'
  const filename = text(row.filename).trim()
  const relPath = text(row.rel_path)
  const statusDraft = text(row.status_draft).trim() || 'Not Started'
  return {
    id,
    filename: filename || relPath || 'PDF',
    relPath,
    status,
    statusLabel: status.charAt(0).toUpperCase() + status.slice(1),
    exists,
    lastError: text(row.last_error),
    canImport,
    canPreview: exists && canImport,
    sourceHint: exists
      ? 'Source file exists in for_processing.'
      : 'Source file missing from for_processing.',
    draft: {
      title: text(row.title),
      status_draft: statusDraft,
      abstract: text(row.abstract),
      source_url: text(row.source_url).trim(),
      published_date: text(row.published_date),
      year: text(row.year).trim(),
      publisher: text(row.publisher).trim(),
      location: text(row.location).trim(),
      edition: text(row.edition).trim(),
      journal: text(row.journal).trim(),
      volume: text(row.volume).trim(),
      issue: text(row.issue).trim(),
      pages: text(row.pages).trim(),
      isbn: text(row.isbn).trim(),
      doi: text(row.doi).trim(),
      doc_type: text(row.doc_type).trim() || 'article',
      private_notes: text(row.private_notes),
      thumb_page: text(row.thumb_page).trim(),
      target_folder_id: text(row.target_folder_id).trim(),
      roles: normalizeRoles(row.roles),
      tags: normalizeTags(row.tags),
    },
  }
}

/** Keep the painted people and append only the person just created. */
export function processingPeopleAfterCreate(
  painted: ProcessingPerson[],
  created: { id: string; name: string },
): ProcessingPerson[] {
  const id = created.id.trim()
  if (!id) return painted.slice()
  if (painted.some((person) => person.id === id)) return painted.slice()
  return painted.concat([{
    id,
    name: created.name.trim() || id,
    raw: { id, name: created.name },
  }])
}

/**
 * Keep every painted folder. A failed read is null or empty and must not
 * become the whole catalogue. The created folder is added when it is missing.
 */
export function processingFoldersAfterCreate(
  painted: ProcessingFolder[],
  fetched: ProcessingFolder[] | null,
  created: { id: string; title: string },
): ProcessingFolder[] {
  const out: ProcessingFolder[] = []
  const seen = new Set<string>()
  function add(folder: { id?: string; title?: string } | null | undefined): void {
    const id = String(folder?.id || '').trim()
    if (!id || seen.has(id)) return
    seen.add(id)
    out.push({ id, title: String(folder?.title || '').trim() || id })
  }
  painted.forEach(add)
  if (Array.isArray(fetched)) fetched.forEach(add)
  add(created)
  return out
}

export function normalizeProcessingPeople(value: unknown): ProcessingPerson[] {
  if (!Array.isArray(value)) return []
  const people: ProcessingPerson[] = []
  value.forEach((entry) => {
    const row = record(entry)
    if (!row) return
    const id = text(row.id).trim()
    if (!id) return
    people.push({ id, name: processingPersonName(row) || id, raw: row })
  })
  return people
}

export function normalizeProcessingFolders(value: unknown): ProcessingFolder[] {
  if (!Array.isArray(value)) return []
  const folders: ProcessingFolder[] = []
  value.forEach((entry) => {
    const row = record(entry)
    if (!row) return
    const id = text(row.id).trim()
    if (!id) return
    folders.push({ id, title: text(row.title).trim() || id })
  })
  return folders
}

export function normalizeProcessingTags(value: unknown): ProcessingTagOption[] {
  if (!Array.isArray(value)) return []
  const tags: ProcessingTagOption[] = []
  value.forEach((entry) => {
    const row = record(entry)
    if (!row) return
    const id = text(row.id).trim()
    const name = text(row.name).trim()
    if (!id && !name) return
    const aliases = Array.isArray(row.aliases) ? row.aliases.map((alias) => text(alias)) : []
    tags.push({ id, name, aliases })
  })
  return tags
}

export function normalizeRoleTypes(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((entry) => text(entry).trim()).filter(Boolean)
}

export function processingVisibleCount(requested: unknown, total: number): number {
  const raw = typeof requested === 'number' ? requested : Number(requested)
  if (!Number.isFinite(raw) || raw <= 0) return Math.min(total, PROCESSING_PAGE_SIZE)
  return Math.min(total, Math.floor(raw))
}

export function processingWidgetPrefix(domPrefix: string, fileId: string, kind: string): string {
  const safePrefix = String(domPrefix || 'prks-pf').replace(/[^a-zA-Z0-9_-]/g, '_')
  const safeId = String(fileId || 'x').replace(/[^a-zA-Z0-9_-]/g, '_')
  const safeKind = String(kind || 'field').replace(/[^a-zA-Z0-9_-]/g, '_')
  return `${safePrefix}-${safeKind}-${safeId}`
}

export function filterProcessingPeople(
  people: ProcessingPerson[],
  query: string,
  match?: (person: Record<string, unknown>, query: string) => boolean,
): ProcessingPerson[] {
  const needle = query.trim().toLowerCase()
  return people
    .filter((person) => person.id)
    .filter((person) => {
      if (!needle) return true
      if (match) return match(person.raw, needle)
      return person.name.toLowerCase().includes(needle)
    })
    .slice(0, 25)
}

export function filterProcessingFolders(folders: ProcessingFolder[], query: string): ProcessingFolder[] {
  const needle = query.trim().toLowerCase()
  return folders
    .filter((folder) => folder.id)
    .filter((folder) => !needle || folder.title.toLowerCase().includes(needle))
    .slice(0, 25)
}

export interface ProcessingTagFilter {
  tags: ProcessingTagOption[]
  canCreate: boolean
}

export function filterProcessingTags(
  tags: ProcessingTagOption[],
  query: string,
  attachedIds: ReadonlySet<string>,
  match?: (tag: ProcessingTagOption, query: string) => boolean,
  exact?: (tag: ProcessingTagOption, query: string) => boolean,
): ProcessingTagFilter {
  const trimmed = query.trim()
  const needle = trimmed.toLowerCase()
  const available = tags.filter((tag) => !attachedIds.has(tag.id))
  const filtered = !trimmed
    ? available.slice(0, 40)
    : available
        .filter((tag) => (match ? match(tag, needle) : tag.name.toLowerCase().includes(needle)))
        .slice(0, 40)
  const exactMatch = !!trimmed && available.some((tag) =>
    exact ? exact(tag, needle) : tag.name.trim().toLowerCase() === needle,
  )
  return { tags: filtered, canCreate: !!trimmed && !exactMatch }
}

export function folderTitle(folders: ProcessingFolder[], folderId: string): string {
  const id = folderId.trim()
  if (!id) return ''
  return folders.find((folder) => folder.id === id)?.title || ''
}

export function buildProcessingProjection(input: ProcessingProjectionInput): ProcessingProjection {
  const files = Array.isArray(input.files)
    ? input.files.map(normalizeProcessingFile).filter((row): row is ProcessingFileRow => !!row)
    : []
  const requested = input.resume && typeof input.resume.visibleCount === 'number'
    ? input.resume.visibleCount
    : undefined
  return {
    generation: typeof input.generation === 'number' ? input.generation : 0,
    domPrefix: text(input.domPrefix).trim() || 'prks-pf',
    roleTypes: normalizeRoleTypes(input.roleTypes),
    files,
    people: normalizeProcessingPeople(input.people),
    folders: normalizeProcessingFolders(input.folders),
    visibleCount: processingVisibleCount(requested, files.length),
  }
}
