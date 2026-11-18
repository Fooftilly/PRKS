import type { DurableWorkFields, RoleLink, VideoMeta } from './runtime'
import { FOLDER_GONE, FOLDER_NOT_PICKED } from './folder'
import { PrksApiError } from '../../../api/http'
import { isInvalidCreateAnswer, type PdfWorkCreateRequest, type WorkCreated } from '../../../api/works-create'

export type SourceKind = 'pdf' | 'video'

/** Fields with an inline error under them. */
export type NewFileField = 'file' | 'videoUrl' | 'folder' | 'people' | 'date' | 'videoDate'

export const CHOOSE_PDF = 'Choose a PDF file.'
export const BAD_PDF_READ = 'Could not read this PDF. Choose the file again.'
export const BAD_YOUTUBE_URL = 'Enter a valid YouTube URL.'
export const BAD_DATE = 'Use dd/mm/yyyy.'
export const NOTHING_SAVED = 'Nothing was saved.'
export const PERSON_NOT_PICKED = 'Pick this person from the list or quick-create them, or clear the field.'
export const PERSON_NOT_ADDED =
  'That person could not be added, so the file was not created. Try adding them again, then create.'
export const ENRICHMENT_NOT_ADDED =
  'A tag or playlist could not be created, so the file was not created. Nothing was saved.'
export const PEOPLE_STILL_SAVING =
  'A person on this file is still being saved. Nothing was saved; try again in a moment.'
export const PERSON_NOT_ON_SERVER = 'A person on this file has not reached PRKS yet. Try again in a moment.'
export const UNCONFIRMED_SAVE =
  'Could not confirm whether the file was saved. Check your library before trying again.'
export const SERVER_FAILED = 'PRKS could not finish creating the file. Check your library before trying again.'
export const PDF_NEEDS_CONNECTION = 'Adding a PDF file requires a connection to PRKS.'

export interface NewFileDraft {
  kind: SourceKind
  pdf: File | null
  title: string
  status: string
  docType: string
  abstract: string
  year: string
  folderId: string
  folderCommitted: boolean
  /** People search text that was never picked or quick-created. */
  personSearch: string
  videoUrl: string
  videoChannel: string
  videoPublished: string
  playlistId: string
  pdfSourceUrl: string
  pdfPublished: string
  thumbPage: string
  privateNotes: string
  publisher: string
  location: string
  edition: string
  journal: string
  volume: string
  issue: string
  pages: string
  isbn: string
  doi: string
  roles: RoleLink[]
  tags: Array<{ id: string; name: string }>
}

export interface NewFileSaveDeps {
  /** The dialog opening this press belongs to. */
  opening: string
  /** The dialog is still shown, for the same opening. */
  stillOpening: () => boolean
  /** The opening whose Create is running, if any. */
  inFlight: () => string | null
  setInFlight: (opening: string | null) => void
  setBusy: (busy: boolean) => void
  /** This opening's Person quick-create, resolving to "row added". */
  pendingPerson: () => Promise<boolean> | null
  /** This opening's tag and playlist quick-creates, each resolving to success. */
  pendingQuickCreates: () => Array<Promise<boolean>>
  readDraft: () => NewFileDraft
  clearErrors: () => void
  setFieldError: (field: NewFileField, message: string) => void
  focusField: (field: NewFileField) => void
  setPeopleError: (message: string) => void
  setStatus: (message: string) => void
  isValidYoutubeUrl: (url: string) => boolean
  /** The ISO date for a dd/mm/yyyy entry, '' when it does not parse. */
  parseDate: (raw: string) => string
  readPdfBase64: (file: File) => Promise<string>
  /** The oEmbed details for a URL, fetching them when this opening has none yet. */
  videoMeta: (url: string) => Promise<VideoMeta | null>
  today: () => string
  createVideoDurably: (
    fields: DurableWorkFields,
    tags: Array<{ id: string; name: string }>,
  ) => Promise<{ create?: { entity_id?: string } } | null>
  offlineRefused: (message: string) => boolean
  waitForPeople: (personIds: string[]) => Promise<boolean>
  /** `POST /api/works` for a PDF (src/api/works-create.ts). */
  createPdfWork: (request: PdfWorkCreateRequest) => Promise<WorkCreated>
  /** `POST /api/works/:id/tags` for one of the new Work's first Tags. */
  attachTag: (workId: string, tagId: string) => Promise<void>
  /** The Work exists: stale the cached projections it joins (never Recent). */
  markCreated: (changes: { people: boolean }) => void
  failureText: (errText: string) => string
  closeAndOpen: (workId: string) => void
  alert: (message: string, title: string) => void
}

/** Validation for the fields Create checks, in reading order. */
export function validateDraft(
  draft: NewFileDraft,
  deps: Pick<NewFileSaveDeps, 'isValidYoutubeUrl' | 'parseDate'>,
): Array<[NewFileField, string]> {
  const errors: Array<[NewFileField, string]> = []
  if (draft.kind === 'pdf' && !draft.pdf) errors.push(['file', CHOOSE_PDF])
  if (draft.kind === 'video' && !deps.isValidYoutubeUrl(draft.videoUrl.trim())) {
    errors.push(['videoUrl', BAD_YOUTUBE_URL])
  }
  if (!draft.folderCommitted) errors.push(['folder', FOLDER_NOT_PICKED])
  // Like the Folder field: a typed person is a choice not yet made.
  if (draft.personSearch.trim()) errors.push(['people', PERSON_NOT_PICKED])
  const videoPublished = draft.videoPublished.trim()
  if (draft.kind === 'video' && videoPublished && !deps.parseDate(videoPublished)) {
    errors.push(['videoDate', BAD_DATE])
  }
  const pdfPublished = draft.pdfPublished.trim()
  if (draft.kind === 'pdf' && pdfPublished && !deps.parseDate(pdfPublished)) {
    errors.push(['date', BAD_DATE])
  }
  return errors
}

function thumbPageOf(raw: string): number | null {
  const text = raw.trim()
  if (!text) return null
  // Whole page numbers only: "1.5" or "1e2" is not a page.
  if (!/^\d+$/.test(text)) return null
  const n = Number(text)
  return Number.isSafeInteger(n) && n >= 1 ? n : null
}

function roleLinks(roles: readonly RoleLink[]): RoleLink[] {
  return roles
    .map((role) => ({
      person_id: role.person_id,
      role_type: role.role_type || 'Author',
      credit_name: role.credit_name || '',
    }))
    .filter((role) => role.person_id)
}

/**
 * Create File (#1036 NF-5).
 *
 * Every wait (a Person or tag still being created, the PDF read, the video
 * details, the request) can outlast the dialog. Each one re-checks the
 * opening it was pressed in, so a result never lands in a reopened form.
 */
export async function saveNewFile(deps: NewFileSaveDeps): Promise<void> {
  const { opening, stillOpening } = deps
  // The in-flight mark names the opening that owns it: a submit left over
  // from a closed form neither blocks this one nor releases its busy state.
  if (deps.inFlight() === opening) return
  const beginBusy = () => {
    deps.setInFlight(opening)
    deps.setBusy(true)
  }
  const endBusy = () => {
    if (deps.inFlight() === opening) deps.setInFlight(null)
    if (stillOpening()) deps.setBusy(false)
  }

  // A Person quick-created a moment ago is still being written and added:
  // the Work is created with them, or not at all.
  const pendingPerson = deps.pendingPerson()
  if (pendingPerson) {
    beginBusy()
    let added = false
    try {
      added = (await pendingPerson) === true
    } catch {
      added = false
    } finally {
      endBusy()
    }
    if (!stillOpening()) return
    if (!added) {
      deps.setPeopleError(PERSON_NOT_ADDED)
      deps.setStatus(NOTHING_SAVED)
      return
    }
  }
  // The same for a tag or playlist created from this form a moment ago.
  const pendingEnrichment = deps.pendingQuickCreates()
  if (pendingEnrichment.length) {
    beginBusy()
    let added = false
    try {
      added = (await Promise.all(pendingEnrichment)).every(Boolean)
    } finally {
      endBusy()
    }
    if (!stillOpening()) return
    if (!added) {
      deps.setStatus(ENRICHMENT_NOT_ADDED)
      return
    }
  }

  const draft = deps.readDraft()
  deps.clearErrors()
  const errors = validateDraft(draft, deps)
  if (errors.length) {
    errors.forEach(([field, message]) => deps.setFieldError(field, message))
    deps.focusField(errors[0][0])
    return
  }

  beginBusy()
  try {
    await createWork(deps, draft)
  } finally {
    endBusy()
  }
}

async function createWork(deps: NewFileSaveDeps, draft: NewFileDraft): Promise<void> {
  const { stillOpening } = deps
  const isVideo = draft.kind === 'video'
  let fileBase64: string | null = null
  let fileName: string | null = null
  if (!isVideo && draft.pdf) {
    fileName = draft.pdf.name
    try {
      fileBase64 = await deps.readPdfBase64(draft.pdf)
    } catch {
      if (!stillOpening()) return
      deps.setFieldError('file', BAD_PDF_READ)
      deps.focusField('file')
      return
    }
  }

  const sourceUrl = (isVideo ? draft.videoUrl : draft.pdfSourceUrl).trim()
  let meta: VideoMeta | null = null
  if (isVideo && sourceUrl) {
    meta = await deps.videoMeta(sourceUrl)
    // Discarded or reopened during the details fetch: void.
    if (!stillOpening()) return
  }
  const channel = isVideo ? draft.videoChannel.trim() : ''
  const publishedIso = isVideo
    ? deps.parseDate(draft.videoPublished.trim())
    : deps.parseDate(draft.pdfPublished.trim())
  const roles = roleLinks(draft.roles)
  const folderId = draft.folderId.trim()

  deps.setStatus('')

  if (isVideo) {
    const fields: DurableWorkFields = {
      // Submitted before the preview filled Title: the fetched title, as
      // the input would now show it. A typed title always wins.
      title: draft.title.trim() ? draft.title : (meta && meta.title ? String(meta.title).trim() : ''),
      status: draft.status,
      doc_type: 'online',
      abstract: draft.abstract || '',
      author_text: channel || (meta && meta.author_name ? String(meta.author_name) : ''),
      year: draft.year || '',
      published_date: publishedIso || '',
      // A YouTube Work is accessed now. The durable CREATE_WORK path stores
      // what it is given, so supply the date the HTTP path would set.
      urldate: deps.today(),
      private_notes: draft.privateNotes || '',
      thumb_url: meta && meta.thumbnail_url ? String(meta.thumbnail_url) : '',
      source: { kind: 'video', url: sourceUrl },
      folder_id: folderId,
      playlist_id: draft.playlistId.trim(),
      roles,
    }
    const tags = draft.tags.filter((tag) => tag.id).map((tag) => ({ id: tag.id, name: tag.name || '' }))
    try {
      if (!stillOpening()) return
      const batch = await deps.createVideoDurably(fields, tags)
      const newId = batch && batch.create && batch.create.entity_id
      if (!stillOpening()) return
      deps.closeAndOpen(newId || '')
    } catch (error) {
      // A failure for a form that is gone has no one to tell.
      if (!stillOpening()) return
      const errText = (error instanceof Error && error.message) || 'Could not create the file.'
      deps.setStatus(deps.failureText(errText))
      if (/url|youtube/i.test(errText)) {
        deps.setFieldError('videoUrl', BAD_YOUTUBE_URL)
        deps.focusField('videoUrl')
      }
    }
    return
  }

  // PDF binary ingestion stays on POST /api/works until a Blob design exists.
  if (deps.offlineRefused(PDF_NEEDS_CONNECTION)) return
  const peopleReady = await deps.waitForPeople(roles.map((role) => role.person_id))
  if (!stillOpening()) return
  if (!peopleReady) {
    deps.setStatus(PEOPLE_STILL_SAVING)
    return
  }

  const payload: PdfWorkCreateRequest = {
    title: draft.title,
    status: draft.status,
    doc_type: draft.docType || 'article',
    abstract: draft.abstract,
    author_text: '',
    year: draft.year,
    folder_id: folderId || null,
    file_b64: fileBase64,
    file_name: fileName,
    roles,
    source_kind: 'pdf',
    source_url: sourceUrl,
    thumb_url: '',
    provider: '',
    published_date: publishedIso || null,
    urldate: '',
    playlist_id: '',
    private_notes: draft.privateNotes,
    thumb_page: thumbPageOf(draft.thumbPage),
    publisher: draft.publisher.trim(),
    location: draft.location,
    edition: draft.edition.trim(),
    journal: draft.journal.trim(),
    volume: draft.volume.trim(),
    issue: draft.issue.trim(),
    pages: draft.pages.trim(),
    isbn: draft.isbn.trim(),
    doi: draft.doi.trim(),
  }
  // What this submit asked for, read while the form is frozen.
  const tagsToAttach = draft.tags.slice()

  let created: WorkCreated
  try {
    created = await deps.createPdfWork(payload)
  } catch (error) {
    if (!(error instanceof PrksApiError) || isInvalidCreateAnswer(error)) {
      // A dropped response, or a 2xx we cannot read, does not prove the
      // create failed: the Work may be committed. Never invite a blind retry.
      if (isInvalidCreateAnswer(error)) deps.markCreated({ people: roles.length > 0 })
      if (stillOpening()) deps.setStatus(UNCONFIRMED_SAVE)
      return
    }
    // A refusal for a form that is gone has no one to tell.
    if (!stillOpening()) return
    // Only a refusal (4xx) proves nothing was created; a server error may
    // come after the row was committed.
    deps.setStatus(error.status >= 500 ? SERVER_FAILED : deps.failureText(error.message))
    if (error.code === 'PERSON_NOT_FOUND') {
      deps.setPeopleError(PERSON_NOT_ON_SERVER)
      return
    }
    if (error.code === 'FOLDER_NOT_FOUND') {
      // A stale destination (deleted elsewhere) is a Folder-field problem.
      deps.setFieldError('folder', FOLDER_GONE)
      deps.focusField('folder')
    }
    return
  }
  deps.markCreated({ people: roles.length > 0 })

  const newId = created.id
  let tagsFailed = 0
  for (const tag of tagsToAttach) {
    try {
      await deps.attachTag(newId, tag.id)
    } catch {
      tagsFailed += 1
    }
  }
  // Sent before a discard: the Work exists with what was asked for, but the
  // dialog on screen now (if any) is someone else's.
  if (!stillOpening()) return
  // The Work exists now: close and open it either way, so a retry can never
  // create a second one. A partial enrichment is said out loud.
  deps.closeAndOpen(newId)
  if (tagsFailed) {
    const one = tagsFailed === 1
    deps.alert(
      `The file was created, but ${one ? 'one tag' : `${tagsFailed} tags`} could not be attached. Add ${one ? 'it' : 'them'} from the file's details.`,
      'Tags not attached',
    )
  }
}
