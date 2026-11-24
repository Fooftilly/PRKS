/**
 * First-run setup transport (#612; backend/api/setup.py).
 *
 * `GET /api/setup` reads the setup state and `POST /api/setup` sets the
 * instance up with the operator's bootstrap token. `POST /api/setup/storage`
 * chooses the Library folder for the next start (#720), with the same token,
 * when the server says the folder is `choosable`. Neither is Library
 * traffic: no epoch, no session, no offline queue. Error codes are mapped to
 * fixed text by the component; nothing here keeps or logs a token or a
 * password.
 */

export type SetupState = 'unconfigured' | 'bootstrapping' | 'ready'

/** Whether the page may choose the Library folder (#720). */
export type SetupStorage = 'choosable' | 'fixed' | 'restart_required'

export interface SetupStatus {
  state: SetupState
  storage: SetupStorage
}

export type ChooseFolderResult =
  | { ok: true; folder: string; created: boolean }
  | { ok: false; error: string; field?: string; reason?: string; message?: string; retryAfter?: number }

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
export const SETUP_STORAGE_PATH = '/api/setup/storage'

/** A bootstrap token as `prks admin bootstrap issue-token` makes it (backend bootstrap_tokens). */
export const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43,128}$/

export const DEFAULT_LIBRARY_NAME = 'Library'

type Fetch = typeof fetch

function isState(value: unknown): value is SetupState {
  return value === 'unconfigured' || value === 'bootstrapping' || value === 'ready'
}

function isStorage(value: unknown): value is SetupStorage {
  return value === 'choosable' || value === 'fixed' || value === 'restart_required'
}

/** The setup state and folder choice, or `null` when the server did not answer usefully. */
export async function readSetupState(fetchImpl: Fetch = fetch): Promise<SetupStatus | null> {
  try {
    const response = await fetchImpl(SETUP_PATH, {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return null
    const body = (await response.json()) as { state?: unknown; storage?: unknown } | null
    if (!isState(body?.state)) return null
    return { state: body.state, storage: isStorage(body?.storage) ? body.storage : 'fixed' }
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

async function postJson(path: string, payload: unknown, fetchImpl: Fetch): Promise<Response | null> {
  try {
    return await fetchImpl(path, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch {
    return null
  }
}

async function bodyOf(response: Response): Promise<Record<string, unknown>> {
  try {
    const parsed: unknown = await response.json()
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

/** Records `folder` as the Library folder for the next start; PRKS must restart before setup. */
export async function chooseFolder(
  input: { token: string; folder: string },
  fetchImpl: Fetch = fetch,
): Promise<ChooseFolderResult> {
  const response = await postJson(SETUP_STORAGE_PATH, { token: input.token.trim(), folder: input.folder.trim() }, fetchImpl)
  if (response === null) return { ok: false, error: 'unreachable' }
  const body = await bodyOf(response)
  if (response.ok && typeof body.folder === 'string') {
    return { ok: true, folder: body.folder, created: body.created === true }
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
    timeoutMs = 5000,
  }: {
    fetchImpl?: Fetch
    intervalMs?: number
    sleep?: (ms: number) => Promise<void>
    maxAttempts?: number
    timeoutMs?: number
  } = {},
): Promise<boolean> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    // A request the switch-over leaves hanging is given up and tried again.
    const controller = new AbortController()
    const deadline = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const response = await fetchImpl('/api/session', {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
      if (response.status === 200 || response.status === 401) return true
    } catch {
      // The server is switching over; try again.
    } finally {
      clearTimeout(deadline)
    }
    await sleep(intervalMs)
  }
  return false
}
