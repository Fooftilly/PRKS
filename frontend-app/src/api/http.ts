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

function readErrorEnvelope(payload: unknown): { error: string; code: string | null } | null {
  if (payload == null || typeof payload !== 'object' || Array.isArray(payload)) return null
  const record = payload as Record<string, unknown>
  if (typeof record.error !== 'string' || !record.error) return null
  const code = typeof record.code === 'string' ? record.code : null
  return { error: record.error, code }
}

export async function prksApiRequest(
  path: string,
  init: {
    method?: string
    body?: string
    signal?: AbortSignal
  } = {},
): Promise<unknown> {
  if (!path.startsWith('/api/') || path.startsWith('//') || path.includes('?')) {
    throw new TypeError('PRKS API paths must be same-origin /api/ paths without a query.')
  }
  let response: Response
  try {
    response = await fetch(path, {
      method: init.method ?? 'GET',
      body: init.body,
      signal: init.signal,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      },
    })
  } catch (error) {
    if (isAbortError(error) || error instanceof TypeError) throw error
    throw new TypeError('PRKS API request failed.')
  }

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
