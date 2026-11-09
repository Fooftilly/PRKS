/**
 * Same-origin PRKS HTTP transport for the Vue client.
 * Components do not call this. Feature services do.
 * Error text stays the server envelope message or a fixed fallback — never a
 * URL, body, or library field.
 */

export class PrksApiError extends Error {
  readonly status: number
  readonly code: string | null
  /**
   * What the caller's `readError` took from the failed body (never the body
   * itself), or null.
   */
  readonly detail: unknown

  constructor(message: string, status: number, code: string | null = null, detail: unknown = null) {
    super(message)
    this.name = 'PrksApiError'
    this.status = status
    this.code = code
    this.detail = detail
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
  prksOfflineRuntimeGeneration?: unknown
}

/** The classic request coordinator's Library epoch (frontend/js/request-coordinator.js). */
type PrksEpochRoot = typeof globalThis & {
  prksLibraryEpochHeaders?: () => Promise<Record<string, string>>
  prksNoteLibraryEpoch?: (response: Response) => void
}

/** The `PRKS-Library-Epoch` header a Library mutation carries (§10.4); none for a read. */
async function libraryEpochHeaders(method: string): Promise<Record<string, string>> {
  if (method === 'GET' || method === 'HEAD') return {}
  const headers = (globalThis as PrksEpochRoot).prksLibraryEpochHeaders
  return typeof headers === 'function' ? await headers() : {}
}

function noteLibraryEpoch(response: Response): void {
  const note = (globalThis as PrksEpochRoot).prksNoteLibraryEpoch
  if (typeof note === 'function') note(response)
}

/** Transport failures the query/mutation caches may report once retries stop. */
const reportableTransportFailures = new WeakSet<object>()

function isManagedPdfPath(path: string): boolean {
  return /^\/api\/pdfs\/[^/]+$/.test(path)
}

function callReachabilityHook(
  name: 'prksOfflineNoteRequestSuccess' | 'prksOfflineNoteRequestFailure',
  issuedAtGeneration?: number,
): void {
  const hook = (globalThis as PrksReachabilityRoot)[name]
  if (typeof hook === 'function') hook(issuedAtGeneration)
}

function reachabilityGeneration(): number | undefined {
  const read = (globalThis as PrksReachabilityRoot).prksOfflineRuntimeGeneration
  return typeof read === 'function' ? (read as () => number)() : undefined
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

/** Message when a failed response carries no PRKS error envelope. */
export const PRKS_API_FALLBACK_ERROR = 'PRKS could not complete the request.'

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

function abortError(): DOMException {
  return new DOMException('The request was aborted.', 'AbortError')
}

function responseHeaders(raw: string): Headers {
  const headers = new Headers()
  for (const line of raw.trim().split(/[\r\n]+/)) {
    const at = line.indexOf(':')
    if (at > 0) headers.append(line.slice(0, at).trim(), line.slice(at + 1).trim())
  }
  return headers
}

/** One request through XMLHttpRequest, for its upload progress; answers a Response like fetch. */
function sendWithProgress(
  url: string,
  method: string,
  body: Blob,
  headers: Record<string, string>,
  signal: AbortSignal | undefined,
  onProgress: (sent: number, total: number) => void,
): Promise<Response> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    const xhr = new XMLHttpRequest()
    xhr.open(method, url)
    xhr.withCredentials = true
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value)
    xhr.upload.onprogress = (event) => onProgress(event.loaded, event.lengthComputable ? event.total : body.size)
    const onAbort = () => xhr.abort()
    signal?.addEventListener('abort', onAbort, { once: true })
    const done = () => signal?.removeEventListener('abort', onAbort)
    xhr.onload = () => {
      done()
      if (xhr.status < 200 || xhr.status > 599) {
        reject(new TypeError('PRKS API request failed.'))
        return
      }
      resolve(new Response(xhr.status === 204 ? null : xhr.responseText, {
        status: xhr.status,
        headers: responseHeaders(xhr.getAllResponseHeaders()),
      }))
    }
    xhr.onerror = () => {
      done()
      reject(new TypeError('PRKS API request failed.'))
    }
    xhr.onabort = () => {
      done()
      reject(abortError())
    }
    xhr.send(body)
  })
}

function contentType(body: string | Blob | undefined): Record<string, string> {
  if (typeof body === 'string') return body ? { 'Content-Type': 'application/json' } : {}
  if (body instanceof Blob) return { 'Content-Type': body.type || 'application/octet-stream' }
  return {}
}

export async function prksApiRequest(
  path: string,
  init: {
    method?: string
    /** A JSON string, or a Blob sent as is under its own type (an upload). */
    body?: string | Blob
    /** Extra request headers. Accept, Content-Type and the epoch header are set here. */
    headers?: Record<string, string>
    signal?: AbortSignal
    /** Query parameters. The path itself never carries a query. */
    query?: Record<string, string>
    /**
     * Reads the fields a feature service needs from a failed answer's body.
     * The result is the error's `detail`; the body itself is not kept.
     */
    readError?: (payload: unknown) => unknown
    /**
     * Upload progress for a Blob body, in bytes sent. Fetch cannot report
     * it, so such a request goes through XMLHttpRequest with the same
     * headers, epoch and answer handling.
     */
    onUploadProgress?: (sent: number, total: number) => void
  } = {},
): Promise<unknown> {
  const apiPath = resolvePrksApiPath(path)
  const search = init.query ? new URLSearchParams(init.query).toString() : ''
  const method = (init.method ?? 'GET').toUpperCase()
  let response: Response
  let issuedAtGeneration: number | undefined
  try {
    const epoch = await libraryEpochHeaders(method)
    // Read the generation after asynchronous Library epoch preflight.
    issuedAtGeneration = reachabilityGeneration()
    const url = search ? `${apiPath}?${search}` : apiPath
    const headers = {
      ...(init.headers ?? {}),
      Accept: 'application/json',
      ...contentType(init.body),
      ...epoch,
    }
    response = init.onUploadProgress && init.body instanceof Blob
      ? await sendWithProgress(url, method, init.body, headers, init.signal, init.onUploadProgress)
      : await fetch(url, {
        method,
        body: init.body,
        signal: init.signal,
        credentials: 'same-origin',
        headers,
      })
  } catch (error) {
    if (isAbortError(error)) throw error
    const transport = error instanceof TypeError ? error : new TypeError('PRKS API request failed.')
    if (!isManagedPdfPath(apiPath)) reportableTransportFailures.add(transport)
    throw transport
  }
  if (!isManagedPdfPath(apiPath)) callReachabilityHook('prksOfflineNoteRequestSuccess', issuedAtGeneration)
  noteLibraryEpoch(response)

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
      envelope?.error ?? PRKS_API_FALLBACK_ERROR,
      response.status,
      envelope?.code ?? null,
      init.readError ? init.readError(payload) : null,
    )
  }
  return payload
}
