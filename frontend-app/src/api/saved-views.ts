/**
 * Typed client for the online-only Saved Views HTTP family.
 * Wire keys match backend/api_contract/saved_views.py (#45). Version matches
 * the Saved Views OpenAPI document. This module does not own query caching.
 */
import { PrksApiError, prksApiRequest } from './http'

export const SAVED_VIEWS_CONTRACT_VERSION = '0.1.0'

export const SAVED_VIEW_KEYS = ['id', 'name', 'search', 'created_at', 'updated_at'] as const
export const SAVED_VIEW_SEARCH_KEYS = ['mode', 'q', 'tag', 'author', 'publisher'] as const
export const SAVED_VIEW_DELETED_KEYS = ['status'] as const

export const SAVED_VIEW_MODES = ['all', 'advanced', 'tag'] as const
export type SavedViewMode = (typeof SAVED_VIEW_MODES)[number]

export interface SavedViewSearch {
  mode: SavedViewMode
  q: string
  tag: string
  author: string
  publisher: string
}

export interface SavedView {
  id: string
  name: string
  search: SavedViewSearch
  created_at: string | null
  updated_at: string | null
}

export interface SavedViewInput {
  name: string
  search: SavedViewSearch
}

const LIST_ERROR = 'Could not load Saved Views.'
const READ_ERROR = 'Could not load Saved View.'
const SAVE_ERROR = 'Could not save view.'
const UPDATE_ERROR = 'Could not update Saved View.'
const DELETE_ERROR = 'Could not delete Saved View.'

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

function parseSearch(value: unknown, message: string): SavedViewSearch {
  const record = asRecord(value, message)
  requireKeys(record, SAVED_VIEW_SEARCH_KEYS, message)
  const mode = record.mode
  if (typeof mode !== 'string' || !(SAVED_VIEW_MODES as readonly string[]).includes(mode)) throw invalid(message)
  return {
    mode: mode as SavedViewMode,
    q: requireString(record.q, message),
    tag: requireString(record.tag, message),
    author: requireString(record.author, message),
    publisher: requireString(record.publisher, message),
  }
}

export function parseSavedView(payload: unknown, message: string = READ_ERROR): SavedView {
  const record = asRecord(payload, message)
  requireKeys(record, SAVED_VIEW_KEYS, message)
  return {
    id: requireString(record.id, message),
    name: requireString(record.name, message),
    search: parseSearch(record.search, message),
    created_at: optionalString(record.created_at, message),
    updated_at: optionalString(record.updated_at, message),
  }
}

export function parseSavedViews(payload: unknown): SavedView[] {
  if (!Array.isArray(payload)) throw invalid(LIST_ERROR)
  return payload.map((row) => parseSavedView(row, LIST_ERROR))
}

function viewPath(viewId: string): string {
  return `/api/saved-views/${encodeURIComponent(viewId)}`
}

export async function listSavedViews(signal?: AbortSignal): Promise<SavedView[]> {
  return parseSavedViews(await prksApiRequest('/api/saved-views', { signal }))
}

/** One view, or `null` when the server has no such view. */
export async function getSavedView(viewId: string, signal?: AbortSignal): Promise<SavedView | null> {
  try {
    return parseSavedView(await prksApiRequest(viewPath(viewId), { signal }))
  } catch (err) {
    if (err instanceof PrksApiError && err.status === 404) return null
    throw err
  }
}

export async function createSavedView(input: SavedViewInput): Promise<SavedView> {
  const payload = await prksApiRequest('/api/saved-views', {
    method: 'POST',
    body: JSON.stringify({ name: input.name, search: input.search }),
  })
  return parseSavedView(payload, SAVE_ERROR)
}

export async function updateSavedView(viewId: string, input: SavedViewInput): Promise<SavedView> {
  const payload = await prksApiRequest(viewPath(viewId), {
    method: 'PATCH',
    body: JSON.stringify({ name: input.name, search: input.search }),
  })
  return parseSavedView(payload, UPDATE_ERROR)
}

export async function deleteSavedView(viewId: string): Promise<void> {
  const record = asRecord(await prksApiRequest(viewPath(viewId), { method: 'DELETE' }), DELETE_ERROR)
  requireKeys(record, SAVED_VIEW_DELETED_KEYS, DELETE_ERROR)
  if (record.status !== 'deleted') throw invalid(DELETE_ERROR)
}
