/**
 * Same-origin PRKS HTTP transport for the Vue client.
 * Components do not call this. Feature services do.
 * Error text stays the server envelope message or a fixed fallback — never a
 * URL, body, or library field.
 */

export class PrksApiError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(message: string, status: number, code: string | null = null) {
    super(message)
    this.name = 'PrksApiError'
    this.status = status
    this.code = code
  }
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  )
}

type PrksReachabilityRoot = typeof globalThis & {
  prksOfflineNoteRequestSuccess?: unknown
  prksOfflineNoteRequestFailure?: unknown
}

/** Transport failures the query/mutation caches may report once retries stop. */
const reportableTransportFailures = new WeakSet<object>()

function isManagedPdfPath(path: string): boolean {
  return /^\/api\/pdfs\/[^/]+$/.test(path)
}

function callReachabilityHook(
  name: 'prksOfflineNoteRequestSuccess' | 'prksOfflineNoteRequestFailure',
): void {
  const hook = (globalThis as PrksReachabilityRoot)[name]
  if (typeof hook === 'function') hook()
}

/** Caches call this after retries stop. Only a marked transport error is a reachability failure. */
export function noteFinalPrksTransportFailure(error: unknown): void {
  if (isAbortError(error) || !(error instanceof TypeError)) return
  if (!reportableTransportFailures.delete(error)) return
  callReachabilityHook('prksOfflineNoteRequestFailure')
}

function readErrorEnvelope(payload: unknown): { error: string; code: string | null } | null {
  if (payload == null || typeof payload !== 'object' || Array.isArray(payload)) return null
  const record = payload as Record<string, unknown>
  if (typeof record.error !== 'string' || !record.error) return null
  const code = typeof record.code === 'string' ? record.code : null
  return { error: record.error, code }
}

const API_PATH_ERROR = 'PRKS API paths must be same-origin /api/ paths without a query.'

function resolvePrksApiPath(path: string): string {
  let url: URL
  try {
    url = new URL(path, location.origin)
  } catch {
    throw new TypeError(API_PATH_ERROR)
  }
  if (url.origin !== location.origin || url.search || url.hash || !url.pathname.startsWith('/api/')) {
    throw new TypeError(API_PATH_ERROR)
  }
  return url.pathname
}

export async function prksApiRequest(
  path: string,
  init: {
    method?: string
    body?: string
    signal?: AbortSignal
  } = {},
): Promise<unknown> {
  const apiPath = resolvePrksApiPath(path)
  const method = init.method ?? 'GET'
  let response: Response
  try {
    response = await fetch(apiPath, {
      method,
      body: init.body,
      signal: init.signal,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      },
    })
  } catch (error) {
    if (isAbortError(error)) throw error
    const transport = error instanceof TypeError ? error : new TypeError('PRKS API request failed.')
    if (!isManagedPdfPath(apiPath)) reportableTransportFailures.add(transport)
    throw transport
  }
  if (!isManagedPdfPath(apiPath)) callReachabilityHook('prksOfflineNoteRequestSuccess')

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text) as unknown
    } catch {
      payload = null
    }
  }
  if (!response.ok) {
    const envelope = readErrorEnvelope(payload)
    throw new PrksApiError(
      envelope?.error ?? 'PRKS could not complete the request.',
      response.status,
      envelope?.code ?? null,
    )
  }
  return payload
}
