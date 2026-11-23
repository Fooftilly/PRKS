/**
 * First-run setup transport (#612; backend/api/setup.py).
 *
 * `GET /api/setup` reads the setup state and `POST /api/setup` sets the
 * instance up with the operator's bootstrap token. Neither is Library
 * traffic: no epoch, no session, no offline queue. Error codes are mapped to
 * fixed text by the component; nothing here keeps or logs a token or a
 * password.
 */

export type SetupState = 'unconfigured' | 'bootstrapping' | 'ready'

export interface SetupInput {
  token: string
  login: string
  displayName: string
  password: string
  libraryName: string
}

export type SetupResult =
  | { ok: true; created: boolean; signedIn: boolean }
  | {
      ok: false
      /** The server's stable code, or `unreachable` when no answer came. */
      error: string
      field?: string
      reason?: string
      message?: string
      retryAfter?: number
    }

export const SETUP_PATH = '/api/setup'

/** A bootstrap token as `prks admin bootstrap issue-token` makes it (backend bootstrap_tokens). */
export const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43,128}$/

export const DEFAULT_LIBRARY_NAME = 'Library'

type Fetch = typeof fetch

function isState(value: unknown): value is SetupState {
  return value === 'unconfigured' || value === 'bootstrapping' || value === 'ready'
}

/** The setup state, or `null` when the server did not answer usefully. */
export async function readSetupState(fetchImpl: Fetch = fetch): Promise<SetupState | null> {
  try {
    const response = await fetchImpl(SETUP_PATH, {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return null
    const body: unknown = await response.json()
    const state = (body as { state?: unknown } | null)?.state
    return isState(state) ? state : null
  } catch {
    return null
  }
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

export async function submitSetup(input: SetupInput, fetchImpl: Fetch = fetch): Promise<SetupResult> {
  let response: Response
  try {
    response = await fetchImpl(SETUP_PATH, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        token: input.token.trim(),
        login: input.login.trim(),
        display_name: input.displayName.trim(),
        password: input.password,
        library_name: input.libraryName.trim(),
      }),
    })
  } catch {
    return { ok: false, error: 'unreachable' }
  }
  let body: Record<string, unknown> = {}
  try {
    const parsed: unknown = await response.json()
    if (parsed && typeof parsed === 'object') body = parsed as Record<string, unknown>
  } catch {
    body = {}
  }
  if (response.ok) {
    return { ok: true, created: body.created === true, signedIn: body.signed_in === true }
  }
  const retry = Number.parseInt(response.headers.get('Retry-After') ?? '', 10)
  return {
    ok: false,
    error: text(body.error) ?? (response.status >= 500 ? 'unavailable' : 'refused'),
    field: text(body.field),
    reason: text(body.reason),
    message: text(body.message),
    retryAfter: Number.isFinite(retry) && retry > 0 ? retry : undefined,
  }
}

/**
 * Resolves once the normal application answers `GET /api/session` (200 or
 * 401): the setup server answers 503 until the process has switched over, and
 * no answer at all in between.
 */
export async function waitForApplication(
  {
    fetchImpl = fetch,
    intervalMs = 500,
    sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
    maxAttempts = 240,
  }: {
    fetchImpl?: Fetch
    intervalMs?: number
    sleep?: (ms: number) => Promise<void>
    maxAttempts?: number
  } = {},
): Promise<boolean> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      const response = await fetchImpl('/api/session', {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      })
      if (response.status === 200 || response.status === 401) return true
    } catch {
      // The server is switching over; try again.
    }
    await sleep(intervalMs)
  }
  return false
}
