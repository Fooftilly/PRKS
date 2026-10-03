import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryObserver } from '@tanstack/vue-query'
import { PrksApiError } from '../../api/http'
import { listSavedViews, type SavedView } from '../../api/saved-views'
import { createPrksQueryClient } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'
import { savedViewActionMessage, savedViewRecords, SavedViewOfflineRefusal } from './records'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.prksOfflineGuardMutation
  delete window.prksReportClientError
})

const SEARCH = { mode: 'all', q: 'x', tag: '', author: '', publisher: '' } as const
function view(id: string, name: string): SavedView {
  return { id, name, search: { ...SEARCH }, created_at: null, updated_at: null }
}

/** A server that commits each write before answering it with `reply`. */
function committingServer(reply: () => Response) {
  const views = [view('SV-0', 'Old')]
  const requests: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      requests.push(`${method} ${url}`)
      if (method === 'GET') return new Response(JSON.stringify(views), { status: 200 })
      views.push(view('SV-1', 'New'))
      return reply()
    }),
  )
  return requests
}

describe('Saved View records', () => {
  it('reads through the shared cache: concurrent reads share a request, and each new read asks the server', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify([view('SV-0', 'Old')]), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const records = savedViewRecords(createPrksQueryClient())
    await Promise.all([records.list(), records.list()])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await records.list()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('answers null for a missing view without reporting a failure', async () => {
    const report = vi.fn()
    window.prksReportClientError = report
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'Saved View not found.' }), { status: 404 })))
    await expect(savedViewRecords(createPrksQueryClient()).get('SV-9')).resolves.toBeNull()
    expect(report).not.toHaveBeenCalled()
  })

  it('reports a failed list or record read under the Saved Views source, with no id', async () => {
    const report = vi.fn()
    window.prksReportClientError = report
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ id: 'SV-1' }), { status: 200 })))
    const records = savedViewRecords(createPrksQueryClient())
    await expect(records.get('SV-1')).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(records.list()).rejects.toMatchObject({ code: 'invalid_response' })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'boom' }), { status: 500 })))
    await expect(records.get('SV-2')).rejects.toMatchObject({ status: 500 })
    await expect(records.list()).rejects.toMatchObject({ status: 500 })
    expect(report.mock.calls).toEqual(
      Array.from({ length: 4 }, () => [{ kind: 'api_client_error', source: 'saved-views.fetch' }]),
    )
  })

  it.each([
    ['a malformed reply', () => new Response(JSON.stringify({ id: 'SV-1' }), { status: 201 })],
    ['a server error', () => new Response(JSON.stringify(null), { status: 500 })],
    [
      'a lost reply',
      () => {
        throw new TypeError('Failed to fetch')
      },
    ],
  ])('refetches the list after a write that may have committed but got %s', async (_label, reply) => {
    const requests = committingServer(reply)
    const client = createPrksQueryClient()
    const observer = new QueryObserver(client, {
      queryKey: prksQueryKeys.savedViews.list(),
      queryFn: ({ signal }) => listSavedViews(signal),
      retry: false,
    })
    const unsubscribe = observer.subscribe(() => {})
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toHaveLength(1))
    await expect(savedViewRecords(client).create({ name: 'New', search: { ...SEARCH } })).rejects.toBeDefined()
    expect(requests).toEqual(['GET /api/saved-views', 'POST /api/saved-views', 'GET /api/saved-views'])
    expect(observer.getCurrentResult().data?.map((row) => row.id)).toEqual(['SV-0', 'SV-1'])
    unsubscribe()
  })

  it('invalidates every Saved Views read after a successful write and does not retry a write', async () => {
    const client = createPrksQueryClient()
    client.setQueryData(prksQueryKeys.savedViews.list(), [])
    client.setQueryData(prksQueryKeys.savedViews.detail('SV-1'), view('SV-1', 'Old'))
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: 'deleted' }), { status: 200 })))
    await savedViewRecords(client).remove('SV-1')
    expect(client.getQueryState(prksQueryKeys.savedViews.list())?.isInvalidated).toBe(true)
    expect(client.getQueryState(prksQueryKeys.savedViews.detail('SV-1'))?.isInvalidated).toBe(true)

    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', failing)
    await expect(savedViewRecords(client).remove('SV-1')).rejects.toBeInstanceOf(TypeError)
    expect(failing).toHaveBeenCalledTimes(1)
  })

  it('sends nothing when the offline guard refuses a write', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const guard = vi.fn(() => true)
    window.prksOfflineGuardMutation = guard
    await expect(
      savedViewRecords(createPrksQueryClient()).update('SV-1', { name: 'x', search: { ...SEARCH } }),
    ).rejects.toBeInstanceOf(SavedViewOfflineRefusal)
    expect(guard).toHaveBeenCalledWith('Saved Views require a connection to PRKS.')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('words a failure from the server text, the offline refusal, or the action fallback', () => {
    expect(savedViewActionMessage(new PrksApiError('Name is required.', 400), 'Could not save view.')).toBe(
      'Name is required.',
    )
    expect(
      savedViewActionMessage(new PrksApiError('PRKS could not complete the request.', 500), 'Could not save view.'),
    ).toBe('Could not save view.')
    expect(savedViewActionMessage(new TypeError('Failed to fetch'), 'Could not save view.')).toBe('Could not save view.')
    expect(savedViewActionMessage(new SavedViewOfflineRefusal(), 'Could not save view.')).toBe(
      'Requires a connection to PRKS.',
    )
  })

  it('tells write listeners after any sent write invalidated, from any instance, until they unsubscribe', async () => {
    const client = createPrksQueryClient()
    client.setQueryData(prksQueryKeys.savedViews.list(), [])
    const seen: boolean[] = []
    const stop = savedViewRecords(client).onWrite(() => {
      seen.push(client.getQueryState(prksQueryKeys.savedViews.list())?.isInvalidated === true)
    })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(null), { status: 500 })))
    await expect(savedViewRecords(client).remove('SV-1')).rejects.toBeDefined()
    expect(seen).toEqual([true])
    stop()
    await expect(savedViewRecords(client).remove('SV-1')).rejects.toBeDefined()
    expect(seen).toEqual([true])
  })

  function heldRead() {
    const signals: AbortSignal[] = []
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit = {}) => {
        if (init.signal) signals.push(init.signal)
        await gate
        if (init.signal?.aborted) throw new DOMException('aborted', 'AbortError')
        return new Response(JSON.stringify(view('SV-1', 'Held')), { status: 200 })
      }),
    )
    return { signals, release: () => release() }
  }

  it('cancels a record read when its only reader leaves, and reports nothing', async () => {
    const report = vi.fn()
    window.prksReportClientError = report
    const server = heldRead()
    const client = createPrksQueryClient()
    const route = new AbortController()
    const read = savedViewRecords(client).get('SV-1', route.signal)
    await vi.waitFor(() => expect(server.signals).toHaveLength(1))
    route.abort()
    await expect(read).rejects.toMatchObject({ name: 'AbortError' })
    expect(server.signals[0].aborted).toBe(true)
    server.release()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(client.getQueryState(prksQueryKeys.savedViews.detail('SV-1'))?.fetchStatus).toBe('idle')
    expect(report).not.toHaveBeenCalled()
    await expect(savedViewRecords(client).get('SV-1', AbortSignal.abort())).rejects.toMatchObject({
      name: 'AbortError',
    })
  })

  it('keeps a shared record read for the reader that is still waiting', async () => {
    const server = heldRead()
    const client = createPrksQueryClient()
    const records = savedViewRecords(client)
    const leaving = new AbortController()
    const left = records.get('SV-1', leaving.signal)
    const staying = records.get('SV-1')
    await vi.waitFor(() => expect(server.signals).toHaveLength(1))
    leaving.abort()
    await expect(left).rejects.toMatchObject({ name: 'AbortError' })
    expect(server.signals[0].aborted).toBe(false)
    server.release()
    await expect(staying).resolves.toMatchObject({ id: 'SV-1', name: 'Held' })
  })
})
