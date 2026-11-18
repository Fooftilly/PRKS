/**
 * Typed client for creating a PDF Work over HTTP (`POST /api/works`) and
 * attaching its first Tags (`POST /api/works/:id/tags`).
 *
 * A video Work is created through the durable CREATE_WORK operation, not
 * here: PDF bytes have no offline Blob design yet, so a PDF create stays
 * online-only. This module owns the request and response contracts. What a
 * refusal means for the form (which field, which message) stays with the
 * New File submit coordinator.
 */
import { PrksApiError, noteFinalPrksTransportFailure, prksApiRequest } from './http'

/** One Person a new Work is born with. */
export interface WorkCreateRole {
  person_id: string
  role_type: string
  credit_name: string
}

/** The body of `POST /api/works` for a PDF upload. */
export interface PdfWorkCreateRequest {
  title: string
  status: string
  doc_type: string
  abstract: string
  author_text: string
  year: string
  folder_id: string | null
  /** Base64 of the PDF bytes, without a data-URL prefix. */
  file_b64: string | null
  file_name: string | null
  roles: WorkCreateRole[]
  source_kind: 'pdf'
  source_url: string
  thumb_url: string
  provider: string
  published_date: string | null
  urldate: string
  playlist_id: string
  private_notes: string
  thumb_page: number | null
  publisher: string
  location: string
  edition: string
  journal: string
  volume: string
  issue: string
  pages: string
  isbn: string
  doi: string
}

export interface WorkCreated {
  id: string
}

/**
 * Refusal codes `POST /api/works` names. Any other refusal carries its
 * server message only.
 */
export type WorkCreateRefusalCode = 'FOLDER_NOT_FOUND' | 'PERSON_NOT_FOUND'

const CREATE_ERROR = 'PRKS answered the create in an unexpected shape.'
const ATTACH_ERROR = 'PRKS answered the tag attach in an unexpected shape.'

function invalid(message: string): PrksApiError {
  return new PrksApiError(message, 200, 'invalid_response')
}

function asRecord(value: unknown, message: string): Record<string, unknown> {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) throw invalid(message)
  return value as Record<string, unknown>
}

export function parseWorkCreated(payload: unknown): WorkCreated {
  const record = asRecord(payload, CREATE_ERROR)
  if (typeof record.id !== 'string' || !record.id.trim()) throw invalid(CREATE_ERROR)
  return { id: record.id }
}

export function parseTagAttached(payload: unknown): void {
  const record = asRecord(payload, ATTACH_ERROR)
  if (record.status !== 'added') throw invalid(ATTACH_ERROR)
}

/** A 2xx answer whose body is not the contract: the Work may well exist. */
export function isInvalidCreateAnswer(error: unknown): boolean {
  return error instanceof PrksApiError && error.code === 'invalid_response'
}

/**
 * One mutation outside the Query cache, so nothing retries it: a transport
 * failure is final here and is reported to the offline runtime at once
 * (the classic mutation coordinator did the same).
 */
async function postOnce(path: string, body: unknown): Promise<unknown> {
  try {
    return await prksApiRequest(path, { method: 'POST', body: JSON.stringify(body) })
  } catch (error) {
    noteFinalPrksTransportFailure(error)
    throw error
  }
}

export async function createPdfWork(request: PdfWorkCreateRequest): Promise<WorkCreated> {
  return parseWorkCreated(await postOnce('/api/works', request))
}

export async function attachTagToWork(workId: string, tagId: string): Promise<void> {
  parseTagAttached(await postOnce(`/api/works/${encodeURIComponent(workId)}/tags`, { tag_id: tagId }))
}
