/**
 * Typed client for the online-only Publishers HTTP family.
 * Transport types come from docs/api/openapi-publishers.json. Runtime parsers
 * still check response shape. This module does not own query caching.
 */
import { PrksApiError, prksApiRequest } from './http'
import type { PublisherCreated, PublisherInUse } from './generated/publishers'

export type { PublisherCreated, PublisherInUse }

export const PUBLISHERS_CONTRACT_VERSION = '0.1.0'

export const PUBLISHER_IN_USE_KEYS = ['id', 'name', 'aliases', 'work_count'] as const
export const PUBLISHER_CREATED_KEYS = ['id', 'name', 'existed'] as const
export const PUBLISHER_ALIAS_ADDED_KEYS = ['status'] as const
export const PUBLISHER_DELETED_KEYS = ['status'] as const

const LIST_ERROR = 'Could not load publishers.'
const CREATE_ERROR = 'Could not add publisher.'
const ALIAS_ADD_ERROR = 'Could not add alias.'
const ALIAS_REMOVE_ERROR = 'Could not remove alias.'
const DELETE_ERROR = 'Could not delete publisher.'

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

function requireCount(value: unknown, message: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) throw invalid(message)
  return value
}

function requireStatus(payload: unknown, status: string, message: string): void {
  const record = asRecord(payload, message)
  requireKeys(record, ['status'], message)
  if (record.status !== status) throw invalid(message)
}

function parsePublisherInUse(value: unknown): PublisherInUse {
  const record = asRecord(value, LIST_ERROR)
  requireKeys(record, PUBLISHER_IN_USE_KEYS, LIST_ERROR)
  if (!Array.isArray(record.aliases)) throw invalid(LIST_ERROR)
  return {
    id: requireString(record.id, LIST_ERROR),
    name: requireString(record.name, LIST_ERROR),
    aliases: record.aliases.map((alias) => requireString(alias, LIST_ERROR)),
    work_count: requireCount(record.work_count, LIST_ERROR),
  }
}

export function parsePublishersInUse(payload: unknown): PublisherInUse[] {
  if (!Array.isArray(payload)) throw invalid(LIST_ERROR)
  return payload.map((row) => parsePublisherInUse(row))
}

export function parsePublisherCreated(payload: unknown): PublisherCreated {
  const record = asRecord(payload, CREATE_ERROR)
  requireKeys(record, PUBLISHER_CREATED_KEYS, CREATE_ERROR)
  if (typeof record.existed !== 'boolean') throw invalid(CREATE_ERROR)
  return {
    id: requireString(record.id, CREATE_ERROR),
    name: requireString(record.name, CREATE_ERROR),
    existed: record.existed,
  }
}

function publisherPath(publisherId: string): string {
  return `/api/publishers/${encodeURIComponent(publisherId)}`
}

export async function listPublishersInUse(signal?: AbortSignal): Promise<PublisherInUse[]> {
  const payload = await prksApiRequest('/api/publishers', { query: { used: '1' }, signal })
  return parsePublishersInUse(payload)
}

export async function createPublisher(name: string): Promise<PublisherCreated> {
  const payload = await prksApiRequest('/api/publishers', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
  return parsePublisherCreated(payload)
}

export async function addPublisherAlias(publisherId: string, alias: string): Promise<void> {
  const payload = await prksApiRequest(`${publisherPath(publisherId)}/aliases`, {
    method: 'POST',
    body: JSON.stringify({ alias }),
  })
  requireStatus(payload, 'added', ALIAS_ADD_ERROR)
}

export async function removePublisherAlias(publisherId: string, alias: string): Promise<void> {
  const payload = await prksApiRequest(`${publisherPath(publisherId)}/aliases`, {
    method: 'DELETE',
    query: { alias },
  })
  requireStatus(payload, 'deleted', ALIAS_REMOVE_ERROR)
}

export async function deletePublisher(publisherId: string): Promise<void> {
  const payload = await prksApiRequest(publisherPath(publisherId), { method: 'DELETE' })
  requireStatus(payload, 'deleted', DELETE_ERROR)
}
