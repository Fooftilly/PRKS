/**
 * Typed client for the online-only Files for Processing HTTP family.
 * Wire keys match backend/api_contract/processing_files.py (#45). Version
 * matches the Files for Processing OpenAPI document. This module does not own
 * query caching.
 */
import { PrksApiError, prksApiRequest } from './http'

export const PROCESSING_FILES_CONTRACT_VERSION = '0.1.0'

export const PROCESSING_FILE_KEYS = [
  'id',
  'rel_path',
  'filename',
  'folder',
  'status',
  'last_error',
  'imported_work_id',
  'imported_at',
  'discovered_at',
  'updated_at',
  'exists',
  'title',
  'status_draft',
  'published_date',
  'abstract',
  'source_url',
  'author_text',
  'year',
  'publisher',
  'location',
  'edition',
  'journal',
  'volume',
  'issue',
  'pages',
  'isbn',
  'doi',
  'doc_type',
  'private_notes',
  'thumb_page',
  'target_folder_id',
  'roles',
  'tags',
] as const
export const PROCESSING_FILE_ROLE_KEYS = ['person_id', 'person_name', 'role_type', 'order_index'] as const
export const PROCESSING_FILE_TAG_KEYS = ['id', 'name', 'color', 'created_at'] as const
export const PROCESSING_FILE_IMPORTED_KEYS = ['processing_file_id', 'work_id'] as const

export const PROCESSING_FILE_STATUSES = ['pending', 'missing', 'imported', 'error'] as const
export type ProcessingFileStatus = (typeof PROCESSING_FILE_STATUSES)[number]

export const PROCESSING_DRAFT_STATUSES = ['Planned', 'In Progress', 'Completed', 'Paused', 'Not Started'] as const
export type ProcessingDraftStatus = (typeof PROCESSING_DRAFT_STATUSES)[number]

export interface ProcessingFileRole {
  person_id: string
  person_name: string
  role_type: string
  order_index: number
}

export interface ProcessingFileTag {
  id: string
  name: string
  color: string | null
  created_at: string | null
}

const TEXT_KEYS = [
  'title',
  'published_date',
  'abstract',
  'source_url',
  'author_text',
  'year',
  'publisher',
  'location',
  'edition',
  'journal',
  'volume',
  'issue',
  'pages',
  'isbn',
  'doi',
  'doc_type',
  'private_notes',
  'target_folder_id',
] as const

type ProcessingFileText = Record<(typeof TEXT_KEYS)[number], string>

export interface ProcessingFile extends ProcessingFileText {
  id: string
  rel_path: string
  filename: string
  folder: string
  status: ProcessingFileStatus
  last_error: string | null
  imported_work_id: string | null
  imported_at: string | null
  discovered_at: string | null
  updated_at: string | null
  exists: boolean
  status_draft: ProcessingDraftStatus
  thumb_page: number | null
  roles: ProcessingFileRole[]
  tags: ProcessingFileTag[]
}

export interface ProcessingFileImported {
  processing_file_id: string
  work_id: string
}

/**
 * A PATCH body as the inbox card sends it. Omitted fields stay unchanged;
 * `roles` and `tags` replace the staged lists.
 */
export interface ProcessingFileUpdate {
  title?: string
  status_draft?: string
  published_date?: string
  abstract?: string
  source_url?: string
  year?: string
  publisher?: string
  location?: string
  edition?: string
  journal?: string
  volume?: string
  issue?: string
  pages?: string
  isbn?: string
  doi?: string
  doc_type?: string
  private_notes?: string
  thumb_page?: string
  target_folder_id?: string
  roles?: Array<{ person_id: string; role_type: string }>
  tags?: Array<{ id: string }>
}

const LIST_ERROR = 'Could not load files for processing.'
const UPDATE_ERROR = 'Could not update processing file metadata.'
const IMPORT_ERROR = 'Could not import file.'

function invalid(message: string): PrksApiError {
  return new PrksApiError(message, 200, 'invalid_response')
}

function asRecord(value: unknown, message: string): Record<string, unknown> {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) throw invalid(message)
  return value as Record<string, unknown>
}

function requireKeys(record: Record<string, unknown>, keys: readonly string[], message: string): void {
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) throw invalid(message)
  }
}

function requireString(value: unknown, message: string): string {
  if (typeof value !== 'string') throw invalid(message)
  return value
}

function optionalString(value: unknown, message: string): string | null {
  if (value === null) return null
  return requireString(value, message)
}

function requireInteger(value: unknown, message: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) throw invalid(message)
  return value
}

function requireMember<T extends string>(value: unknown, members: readonly T[], message: string): T {
  if (typeof value !== 'string' || !(members as readonly string[]).includes(value)) throw invalid(message)
  return value as T
}

function parseRole(value: unknown, message: string): ProcessingFileRole {
  const record = asRecord(value, message)
  requireKeys(record, PROCESSING_FILE_ROLE_KEYS, message)
  return {
    person_id: requireString(record.person_id, message),
    person_name: requireString(record.person_name, message),
    role_type: requireString(record.role_type, message),
    order_index: requireInteger(record.order_index, message),
  }
}

function parseTag(value: unknown, message: string): ProcessingFileTag {
  const record = asRecord(value, message)
  requireKeys(record, PROCESSING_FILE_TAG_KEYS, message)
  return {
    id: requireString(record.id, message),
    name: requireString(record.name, message),
    color: optionalString(record.color, message),
    created_at: optionalString(record.created_at, message),
  }
}

function parseArray<T>(value: unknown, message: string, parse: (row: unknown, message: string) => T): T[] {
  if (!Array.isArray(value)) throw invalid(message)
  return value.map((row) => parse(row, message))
}

export function parseProcessingFile(payload: unknown, message: string = LIST_ERROR): ProcessingFile {
  const record = asRecord(payload, message)
  requireKeys(record, PROCESSING_FILE_KEYS, message)
  if (typeof record.exists !== 'boolean') throw invalid(message)
  const text = {} as ProcessingFileText
  for (const key of TEXT_KEYS) text[key] = requireString(record[key], message)
  return {
    ...text,
    id: requireString(record.id, message),
    rel_path: requireString(record.rel_path, message),
    filename: requireString(record.filename, message),
    folder: requireString(record.folder, message),
    status: requireMember(record.status, PROCESSING_FILE_STATUSES, message),
    last_error: optionalString(record.last_error, message),
    imported_work_id: optionalString(record.imported_work_id, message),
    imported_at: optionalString(record.imported_at, message),
    discovered_at: optionalString(record.discovered_at, message),
    updated_at: optionalString(record.updated_at, message),
    exists: record.exists,
    status_draft: requireMember(record.status_draft, PROCESSING_DRAFT_STATUSES, message),
    thumb_page: record.thumb_page === null ? null : requireInteger(record.thumb_page, message),
    roles: parseArray(record.roles, message, parseRole),
    tags: parseArray(record.tags, message, parseTag),
  }
}

export function parseProcessingFiles(payload: unknown): ProcessingFile[] {
  return parseArray(payload, LIST_ERROR, parseProcessingFile)
}

export function parseProcessingFileImported(payload: unknown): ProcessingFileImported {
  const record = asRecord(payload, IMPORT_ERROR)
  requireKeys(record, PROCESSING_FILE_IMPORTED_KEYS, IMPORT_ERROR)
  return {
    processing_file_id: requireString(record.processing_file_id, IMPORT_ERROR),
    work_id: requireString(record.work_id, IMPORT_ERROR),
  }
}

function filePath(fileId: string): string {
  return `/api/processing-files/${encodeURIComponent(fileId)}`
}

/** Inbox files that are not imported yet. `rescan` reconciles the inbox folder on disk first. */
export async function listProcessingFiles(
  options: { rescan?: boolean; signal?: AbortSignal } = {},
): Promise<ProcessingFile[]> {
  const payload = await prksApiRequest('/api/processing-files', {
    signal: options.signal,
    query: options.rescan ? { rescan: '1' } : undefined,
  })
  return parseProcessingFiles(payload)
}

export async function updateProcessingFile(fileId: string, update: ProcessingFileUpdate): Promise<ProcessingFile> {
  const payload = await prksApiRequest(filePath(fileId), {
    method: 'PATCH',
    body: JSON.stringify(update),
  })
  return parseProcessingFile(payload, UPDATE_ERROR)
}

export async function importProcessingFile(fileId: string): Promise<ProcessingFileImported> {
  const payload = await prksApiRequest(`${filePath(fileId)}/import`, {
    method: 'POST',
    body: '{}',
  })
  return parseProcessingFileImported(payload)
}
