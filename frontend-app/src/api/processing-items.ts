/**
 * Typed client for the PostgreSQL Processing queue (`/api/processing-items`,
 * docs/postgresql-architecture.md §11.5).
 * Transport types come from docs/api/openapi-processing-items.json. Runtime
 * parsers still check response shape. This module does not own caching or
 * write sequencing: the queue page does.
 */
import { PrksApiError, prksApiRequest } from './http'
import type { components } from './generated/processing-items'

type Schemas = components['schemas']
export type ProcessingItem = Schemas['ProcessingItem']
export type ProcessingItemDraft = Schemas['ProcessingItemDraft']
export type ProcessingImportResult = Schemas['ProcessingImportResult']
export type ProcessingDuplicates = Schemas['ProcessingDuplicates']

export const PROCESSING_ITEMS_PATH = '/api/processing-items'
export const PROCESSING_ITEMS_PAGE_SIZE = 50

/** The draft fields the queue page edits. Roles and tags stay as drafted. */
export const PROCESSING_ITEM_EDIT_KEYS = ['title', 'author_text', 'year', 'doc_type', 'target_folder_id'] as const
export type ProcessingItemEditKey = (typeof PROCESSING_ITEM_EDIT_KEYS)[number]
export type ProcessingItemEdit = Partial<Record<ProcessingItemEditKey, string>>

const DRAFT_TEXT_KEYS = [
  'abstract', 'author_text', 'doc_type', 'doi', 'edition', 'isbn', 'issue', 'journal', 'location',
  'pages', 'private_notes', 'published_date', 'publisher', 'source_url', 'status_draft',
  'target_folder_id', 'title', 'volume', 'year',
] as const

const STATUSES = ['ready', 'imported', 'deleted'] as const

/** A refusal as the queue answers it: its message, and the item as it now is when there is one. */
export interface ProcessingRefusal {
  message: string | null
  item: ProcessingItem | null
}

export class ProcessingItemsResponseError extends Error {
  constructor(what: string) {
    super(`PRKS answered an unexpected ${what}.`)
    this.name = 'ProcessingItemsResponseError'
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function text(value: unknown): value is string {
  return typeof value === 'string'
}

function nullableText(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function parseDraft(value: unknown): ProcessingItemDraft | null {
  const raw = record(value)
  if (!raw) return null
  for (const key of DRAFT_TEXT_KEYS) if (!text(raw[key])) return null
  if (!(raw.thumb_page === null || typeof raw.thumb_page === 'number')) return null
  if (!Array.isArray(raw.roles) || !Array.isArray(raw.tags)) return null
  return raw as unknown as ProcessingItemDraft
}

/** One queue item, or null when the value is not one. */
export function parseProcessingItem(value: unknown): ProcessingItem | null {
  const raw = record(value)
  if (!raw) return null
  if (!text(raw.id) || !raw.id || !text(raw.original_filename) || !text(raw.sha256)) return null
  if (!text(raw.created_at) || !text(raw.updated_at)) return null
  if (typeof raw.revision !== 'number' || typeof raw.byte_size !== 'number') return null
  if (!STATUSES.includes(raw.status as (typeof STATUSES)[number])) return null
  if (!nullableText(raw.imported_at) || !nullableText(raw.imported_work_id) || !nullableText(raw.deleted_at)) return null
  const draft = raw.draft === null ? null : parseDraft(raw.draft)
  if (raw.draft !== null && draft === null) return null
  if (raw.status === 'ready' && draft === null) return null
  return { ...(raw as unknown as ProcessingItem), draft }
}

function requireItem(value: unknown): ProcessingItem {
  const item = parseProcessingItem(value)
  if (!item) throw new ProcessingItemsResponseError('processing item')
  return item
}

function parseDuplicates(value: unknown): ProcessingDuplicates {
  const raw = record(value)
  const items = Array.isArray(raw?.items) ? raw.items : []
  const works = Array.isArray(raw?.works) ? raw.works : []
  return {
    items: items.filter((one) => text(record(one)?.id) && text(record(one)?.original_filename)) as ProcessingDuplicates['items'],
    works: works.filter((one) => text(record(one)?.id) && text(record(one)?.title)) as ProcessingDuplicates['works'],
    more_items: raw?.more_items === true,
    more_works: raw?.more_works === true,
  }
}

/** The refusal's message and item, kept on the error's `detail`. */
function readRefusal(payload: unknown): ProcessingRefusal {
  const raw = record(payload)
  return {
    message: raw && text(raw.message) && raw.message ? raw.message : null,
    item: raw ? parseProcessingItem(raw.item) : null,
  }
}

/** What a failed queue call says, for the page to show. */
export function processingRefusal(error: unknown): ProcessingRefusal | null {
  if (!(error instanceof PrksApiError)) return null
  const detail = error.detail as ProcessingRefusal | null
  return detail && typeof detail === 'object' ? detail : { message: null, item: null }
}

/** The text a failed queue call shows: the queue's message, else the envelope, else `fallback`. */
export function processingErrorMessage(error: unknown, fallback: string): string {
  const refusal = processingRefusal(error)
  if (refusal?.message) return refusal.message
  if (error instanceof PrksApiError && error.message && !/^[A-Z_]+$/.test(error.message)) return error.message
  return fallback
}

function itemPath(id: string, tail = ''): string {
  return `${PROCESSING_ITEMS_PATH}/${encodeURIComponent(id)}${tail}`
}

export interface ProcessingItemPage {
  items: ProcessingItem[]
  nextCursor: string | null
}

/** One page of `ready` items, oldest upload first. */
export async function listProcessingItems(
  options: { cursor?: string | null; limit?: number; signal?: AbortSignal } = {},
): Promise<ProcessingItemPage> {
  const query: Record<string, string> = { limit: String(options.limit ?? PROCESSING_ITEMS_PAGE_SIZE) }
  if (options.cursor) query.cursor = options.cursor
  const payload = record(await prksApiRequest(PROCESSING_ITEMS_PATH, { query, signal: options.signal, readError: readRefusal }))
  if (!payload || !Array.isArray(payload.items) || !nullableText(payload.next_cursor)) {
    throw new ProcessingItemsResponseError('processing queue page')
  }
  return { items: payload.items.map(requireItem), nextCursor: payload.next_cursor }
}

export interface ProcessingItemUploaded {
  item: ProcessingItem
  created: boolean
  duplicates: ProcessingDuplicates
}

/**
 * Upload one PDF. `key` is the upload's idempotency key: send the same key
 * again to retry, and the queue answers with the item it already holds.
 */
export async function uploadProcessingItem(
  file: Blob & { name?: string },
  key: string,
  options: { signal?: AbortSignal; onProgress?: (sent: number, total: number) => void } = {},
): Promise<ProcessingItemUploaded> {
  const body = file.type === 'application/pdf' ? file : new Blob([file], { type: 'application/pdf' })
  const payload = record(await prksApiRequest(PROCESSING_ITEMS_PATH, {
    method: 'POST',
    body,
    headers: {
      'Idempotency-Key': key,
      'PRKS-Filename': encodeURIComponent(file.name || 'upload.pdf'),
    },
    signal: options.signal,
    readError: readRefusal,
    onUploadProgress: options.onProgress,
  }))
  if (!payload || typeof payload.created !== 'boolean') throw new ProcessingItemsResponseError('upload answer')
  return { item: requireItem(payload.item), created: payload.created, duplicates: parseDuplicates(payload.duplicates) }
}

/** Edit a ready item's draft at `revision`. A changed item is refused with the item as it now is. */
export async function updateProcessingItem(
  id: string,
  revision: number,
  edit: ProcessingItemEdit,
): Promise<ProcessingItem> {
  const fields: Record<string, string | number> = { revision }
  for (const key of PROCESSING_ITEM_EDIT_KEYS) {
    const value = edit[key]
    if (value !== undefined) fields[key] = value
  }
  return requireItem(await prksApiRequest(itemPath(id), {
    method: 'PATCH',
    body: JSON.stringify(fields),
    readError: readRefusal,
  }))
}

/** Remove a ready item. The answer is its `deleted` marker. */
export async function deleteProcessingItem(id: string): Promise<ProcessingItem> {
  return requireItem(await prksApiRequest(itemPath(id), { method: 'DELETE', readError: readRefusal }))
}

export interface ProcessingItemImported {
  itemId: string
  workId: string
  created: boolean
}

/** Import one item into a Work. A retry answers with the same Work. */
export async function importProcessingItem(id: string): Promise<ProcessingItemImported> {
  const payload = record(await prksApiRequest(itemPath(id, '/import'), { method: 'POST', body: '{}', readError: readRefusal }))
  if (!payload || !text(payload.item_id) || !text(payload.work_id) || typeof payload.created !== 'boolean') {
    throw new ProcessingItemsResponseError('import answer')
  }
  return { itemId: payload.item_id, workId: payload.work_id, created: payload.created }
}

/** Import several items, each in its own transaction: one result per item, in order. */
export async function importProcessingItems(ids: readonly string[]): Promise<ProcessingImportResult[]> {
  const payload = record(await prksApiRequest(`${PROCESSING_ITEMS_PATH}/import`, {
    method: 'POST',
    body: JSON.stringify({ item_ids: ids }),
    readError: readRefusal,
  }))
  if (!payload || !Array.isArray(payload.results)) throw new ProcessingItemsResponseError('batch import answer')
  return payload.results.map((one) => {
    const raw = record(one)
    if (!raw || !text(raw.item_id) || typeof raw.ok !== 'boolean') throw new ProcessingItemsResponseError('import result')
    return {
      item_id: raw.item_id,
      ok: raw.ok,
      work_id: text(raw.work_id) ? raw.work_id : null,
      created: raw.created === true,
      code: text(raw.code) ? raw.code : null,
      message: text(raw.message) ? raw.message : null,
      item: parseProcessingItem(raw.item),
    }
  })
}

/** The same-origin URL of a ready item's uploaded PDF. */
export function processingItemPdfUrl(id: string): string {
  return itemPath(id, '/pdf')
}

/**
 * A fresh idempotency key for one upload. ``randomUUID`` needs a secure
 * context, so a plain-HTTP LAN origin falls back to 16 random bytes from
 * ``getRandomValues``, which every supported browser has.
 */
export function newUploadKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
