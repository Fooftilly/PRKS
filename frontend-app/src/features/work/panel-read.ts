/**
 * Read-only Work right-panel display.
 *
 * The coordinator supplies records it already resolved. This module does not
 * read durable operations, and it does not treat metadata or role overlays as
 * the editor base. #291 measures saves against `editor`. #294 reads
 * `display` people, tags, folder, and playlist.
 */

export interface WorkPanelTag {
  readonly id: string
  readonly name: string
  readonly color: string
}

export interface WorkPanelPerson {
  readonly personId: string
  readonly roleType: string
  readonly displayName: string
  readonly canonicalName: string
  readonly orderIndex: string
}

export interface WorkPanelLink {
  readonly id: string
  readonly title: string
}

export interface WorkPanelDocType {
  readonly value: string
  readonly label: string
  readonly color: string
  readonly border: string
}

/** Acknowledged Work plus source and placement. Not the pending overlay. */
export interface WorkPanelEditorBase {
  readonly id: string
  readonly fields: Readonly<Record<string, string>>
  readonly folderId: string
  readonly folderTitle: string
  readonly playlistId: string
  readonly playlistTitle: string
  readonly sourceKind: string
  readonly filePath: string
}

export interface WorkPanelDisplay {
  readonly title: string
  readonly status: string
  readonly docType: WorkPanelDocType
  readonly year: string
  readonly publishedDisplay: string
  readonly showPublishedDate: boolean
  readonly publisher: string
  readonly location: string
  readonly edition: string
  readonly journal: string
  readonly volume: string
  readonly issue: string
  readonly pages: string
  readonly isbn: string
  readonly doi: string
  readonly abstract: string
  readonly sourceUrl: string
  readonly showOriginalUrl: boolean
  readonly hasBibliographicText: boolean
  readonly people: readonly WorkPanelPerson[]
  readonly tags: readonly WorkPanelTag[]
  readonly folder: WorkPanelLink | null
  readonly playlist: WorkPanelLink | null
  readonly peopleCount: number
  readonly tagsCount: number
  readonly statusIcon: string
}

export interface WorkPanelReadModel {
  readonly ownerTabId: string
  readonly ownerGeneration: number
  readonly workId: string
  readonly editor: WorkPanelEditorBase
  readonly display: WorkPanelDisplay
}

export interface WorkPanelReadInput {
  readonly ownerTabId: string
  readonly ownerGeneration: number
  readonly workId: string
  readonly work: Record<string, unknown> | null
  readonly effectiveWork?: Record<string, unknown> | null
  readonly tags?: readonly unknown[] | null
  readonly sourceKind?: string
  readonly publishedDisplay?: string
  readonly docType?: Partial<WorkPanelDocType> | null
  readonly statusIcon?: string
}

export const WORK_PANEL_EDITOR_FIELDS = [
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

const BIB_FIELDS = [
  'publisher',
  'location',
  'edition',
  'journal',
  'volume',
  'issue',
  'pages',
  'isbn',
  'doi',
] as const

function text(value: unknown): string {
  if (value == null) return ''
  return String(value)
}

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function editorFields(work: Record<string, unknown> | null): Record<string, string> {
  const fields: Record<string, string> = {}
  for (const key of WORK_PANEL_EDITOR_FIELDS) {
    fields[key] = text(work?.[key])
  }
  return Object.freeze(fields)
}

function link(id: unknown, title: unknown): WorkPanelLink | null {
  const linkId = text(id).trim()
  const linkTitle = text(title).trim()
  if (!linkId && !linkTitle) return null
  return Object.freeze({ id: linkId, title: linkTitle || linkId })
}

/** A folder placement is a route. A title with no id is not a folder. */
function folderPlacement(id: unknown, title: unknown): WorkPanelLink | null {
  const linkId = text(id).trim()
  if (!linkId) return null
  const linkTitle = text(title).trim()
  return Object.freeze({ id: linkId, title: linkTitle || linkId })
}

function personName(role: Record<string, unknown>): string {
  const credit = text(role.credit_name).trim()
  if (credit) return credit
  const named = text(role.name).trim()
  if (named) return named
  const combined = `${text(role.first_name).trim()} ${text(role.last_name).trim()}`.trim()
  return combined || 'Person'
}

function canonicalName(role: Record<string, unknown>, displayName: string): string {
  const combined = `${text(role.first_name).trim()} ${text(role.last_name).trim()}`.trim()
  return combined || text(role.name).trim() || displayName
}

function peopleFrom(source: Record<string, unknown> | null): WorkPanelPerson[] {
  const roles = source && Array.isArray(source.roles) ? source.roles : []
  const people: WorkPanelPerson[] = []
  for (const row of roles) {
    const role = record(row)
    if (!role) continue
    const displayName = personName(role)
    const personId = text(role.person_id || role.id).trim()
    people.push(
      Object.freeze({
        personId,
        roleType: text(role.role_type).trim() || 'Linked',
        displayName,
        canonicalName: canonicalName(role, displayName),
        orderIndex: role.order_index == null || role.order_index === '' ? '0' : text(role.order_index),
      }),
    )
  }
  return people
}

function tagsFrom(rows: readonly unknown[] | null | undefined): WorkPanelTag[] {
  if (!Array.isArray(rows)) return []
  const tags: WorkPanelTag[] = []
  for (const row of rows) {
    const tag = record(row)
    if (!tag) continue
    const name = text(tag.name).trim()
    const id = text(tag.id).trim()
    if (!id && !name) continue
    tags.push(Object.freeze({ id, name, color: text(tag.color).trim() }))
  }
  return tags
}

function docTypeOf(input: WorkPanelReadInput, raw: string): WorkPanelDocType {
  const supplied = input.docType
  const value = text(supplied?.value || raw || 'misc').trim() || 'misc'
  return Object.freeze({
    value,
    label: text(supplied?.label || value),
    color: text(supplied?.color),
    border: text(supplied?.border),
  })
}

export function projectWorkPanelRead(input: WorkPanelReadInput): WorkPanelReadModel {
  const work = record(input.work)
  const effective = record(input.effectiveWork) ?? work
  const folder = folderPlacement(work?.folder_id ?? record(work?.folder)?.id, work?.folder_title ?? record(work?.folder)?.title)
  const playlist = link(work?.playlist_id, work?.playlist_title)
  const people = peopleFrom(effective)
  const tags = tagsFrom(input.tags)
  const year = text(effective?.year).trim()
  const sourceUrl = text(effective?.source_url).trim()
  const sourceKind = text(input.sourceKind).trim()
  const abstract = text(effective?.abstract)
  const publisher = text(effective?.publisher)
  const location = text(effective?.location)
  const edition = text(effective?.edition)
  const journal = text(effective?.journal)
  const volume = text(effective?.volume)
  const issue = text(effective?.issue)
  const pages = text(effective?.pages)
  const isbn = text(effective?.isbn)
  const doi = text(effective?.doi)
  const showOriginalUrl = sourceKind === 'pdf' && !!sourceUrl
  const showPublishedDate = !year
  const publishedDisplay = text(input.publishedDisplay).trim()
  const hasBibliographicText = !!(
    year ||
    (showPublishedDate && publishedDisplay) ||
    publisher ||
    location ||
    edition ||
    journal ||
    volume ||
    issue ||
    pages ||
    isbn ||
    doi ||
    abstract ||
    showOriginalUrl
  )
  const status = text(effective?.status).trim() || 'Not Started'
  const display: WorkPanelDisplay = Object.freeze({
    title: text(effective?.title),
    status,
    docType: docTypeOf(input, text(effective?.doc_type)),
    year,
    publishedDisplay,
    showPublishedDate,
    publisher,
    location,
    edition,
    journal,
    volume,
    issue,
    pages,
    isbn,
    doi,
    abstract,
    sourceUrl,
    showOriginalUrl,
    hasBibliographicText,
    people: Object.freeze(people),
    tags: Object.freeze(tags),
    folder,
    playlist,
    peopleCount: people.length,
    tagsCount: tags.length,
    statusIcon: text(input.statusIcon).trim(),
  })
  const editor: WorkPanelEditorBase = Object.freeze({
    id: text(work?.id || input.workId),
    fields: editorFields(work),
    folderId: folder?.id ?? '',
    folderTitle: folder?.title ?? '',
    playlistId: playlist?.id ?? '',
    playlistTitle: playlist?.title ?? '',
    sourceKind,
    filePath: text(work?.file_path).trim(),
  })
  return Object.freeze({
    ownerTabId: text(input.ownerTabId),
    ownerGeneration: input.ownerGeneration,
    workId: text(input.workId),
    editor,
    display,
  })
}

export function workPanelBibFields(display: WorkPanelDisplay): readonly { field: string; label: string; value: string }[] {
  const labels: Record<(typeof BIB_FIELDS)[number], string> = {
    publisher: 'Publisher',
    location: 'Location',
    edition: 'Edition',
    journal: 'Journal',
    volume: 'Volume',
    issue: 'Issue',
    pages: 'Pages',
    isbn: 'ISBN',
    doi: 'DOI',
  }
  return BIB_FIELDS.filter((field) => text(display[field]).trim()).map((field) =>
    Object.freeze({ field, label: labels[field], value: text(display[field]) }),
  )
}

function personToRole(person: WorkPanelPerson): Record<string, unknown> {
  return {
    id: person.personId,
    role_type: person.roleType,
    credit_name: person.displayName,
    name: person.canonicalName,
    order_index: person.orderIndex,
  }
}

function displayRecord(current: WorkPanelReadModel): Record<string, unknown> {
  const display = current.display
  return {
    title: display.title,
    status: display.status,
    doc_type: display.docType.value,
    year: display.year,
    publisher: display.publisher,
    location: display.location,
    edition: display.edition,
    journal: display.journal,
    volume: display.volume,
    issue: display.issue,
    pages: display.pages,
    isbn: display.isbn,
    doi: display.doi,
    abstract: display.abstract,
    source_url: display.sourceUrl,
    roles: current.display.people.map(personToRole),
  }
}

/** Merge a later overlay into a mounted read model without replacing the editor base. */
export function refreshWorkPanelDisplay(
  current: WorkPanelReadModel,
  patch: {
    readonly effectiveWork?: Record<string, unknown> | null
    readonly people?: readonly unknown[] | null
    readonly tags?: readonly unknown[] | null
    readonly publishedDisplay?: string
    readonly docType?: Partial<WorkPanelDocType> | null
    readonly statusIcon?: string
  },
): WorkPanelReadModel {
  const effective = record(patch.effectiveWork)
  const metadata = { ...(effective ?? {}) }
  delete metadata.roles
  const effectiveWork: Record<string, unknown> = {
    ...displayRecord(current),
    ...metadata,
  }
  // People stay on the mounted overlay unless this refresh names them.
  // A metadata payload still carries the acknowledged Work's roles.
  effectiveWork.roles = patch.people != null ? patch.people : displayRecord(current).roles
  const nextStatus = text(metadata.status).trim()
  const statusChanged = !!nextStatus && nextStatus !== current.display.status
  const nextDoc = text(metadata.doc_type).trim()
  const docChanged = !!nextDoc && nextDoc !== current.display.docType.value
  return projectWorkPanelRead({
    ownerTabId: current.ownerTabId,
    ownerGeneration: current.ownerGeneration,
    workId: current.workId,
    work: {
      id: current.editor.id,
      ...current.editor.fields,
      folder_id: current.editor.folderId,
      folder_title: current.editor.folderTitle,
      playlist_id: current.editor.playlistId,
      playlist_title: current.editor.playlistTitle,
      file_path: current.editor.filePath,
    },
    effectiveWork,
    tags: patch.tags != null ? patch.tags : current.display.tags,
    sourceKind: current.editor.sourceKind,
    publishedDisplay:
      patch.publishedDisplay != null
        ? patch.publishedDisplay
        : effective && text(effective.published_date).trim()
          ? text(effective.published_date)
          : current.display.publishedDisplay,
    docType:
      patch.docType !== undefined
        ? patch.docType
        : docChanged
          ? { value: nextDoc }
          : current.display.docType,
    statusIcon:
      patch.statusIcon != null ? patch.statusIcon : statusChanged ? '' : current.display.statusIcon,
  })
}
