import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryObserver } from '@tanstack/vue-query'
import { listPublishersInUse } from '../../api/publishers'
import { createPrksQueryClient } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'
import { browserPublishersIntents, type PublishersIntentOwner } from './intents'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.prksNavigate
  delete window.prksConfirmDestructive
  delete window.prksOfflineGuardMutation
})

function owner(state: { generation: number }): PublishersIntentOwner {
  return {
    tabId: 'tab-publishers',
    isCurrent: (generation) => generation === state.generation,
    lastResolvedRoute: { name: 'publishers' },
  }
}

type Reply = { status?: number; body: unknown }

/** Answer each request in order and record what was sent. */
function stubFetch(...replies: Reply[]) {
  const calls: { url: string; method: string; body: unknown }[] = []
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({
      url,
      method: init.method ?? 'GET',
      body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
    })
    const reply = replies.shift()
    if (!reply) throw new Error(`unexpected request ${url}`)
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

/** A query client whose Publishers read was already painted. */
function primedClient() {
  const client = createPrksQueryClient()
  client.setQueryData(prksQueryKeys.publishers.inUse(), [])
  return client
}

describe('Publishers intents', () => {
  it('opens search on the owning tab only while that page is current', () => {
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const state = { generation: 2 }
    browserPublishersIntents(owner(state), 2, primedClient()).openPublisher('Oxford University Press')
    expect(navigate).toHaveBeenCalledWith('#/search?publisher=Oxford%20University%20Press', {
      tabId: 'tab-publishers',
    })
    state.generation = 3
    browserPublishersIntents(owner(state), 2, primedClient()).openPublisher('Oxford University Press')
    expect(navigate).toHaveBeenCalledTimes(1)
  })

  it('creates through the typed client and invalidates the Publishers read', async () => {
    const calls = stubFetch({ body: { id: 'R-1', name: 'OUP', existed: false } })
    const client = primedClient()
    const outcome = await browserPublishersIntents(owner({ generation: 2 }), 2, client).create('  OUP ')
    expect(outcome.status).toBe('success')
    expect(calls).toEqual([{ url: '/api/publishers', method: 'POST', body: { name: 'OUP' } }])
    expect(client.getQueryState(prksQueryKeys.publishers.inUse())?.isInvalidated).toBe(true)
  })

  it('adds and removes aliases on the encoded publisher path', async () => {
    const calls = stubFetch({ body: { status: 'added' } }, { body: { status: 'deleted' } })
    const intents = browserPublishersIntents(owner({ generation: 1 }), 1, primedClient())
    expect((await intents.addAlias('R/1', ' Oxford UP ')).status).toBe('success')
    expect((await intents.removeAlias('R/1', 'A & B')).status).toBe('success')
    expect(calls).toEqual([
      { url: '/api/publishers/R%2F1/aliases', method: 'POST', body: { alias: 'Oxford UP' } },
      { url: '/api/publishers/R%2F1/aliases?alias=A+%26+B', method: 'DELETE', body: undefined },
    ])
  })

  it('shows the server refusal, or the action fallback when there is none', async () => {
    stubFetch(
      { status: 400, body: { error: 'alias already used' } },
      { status: 500, body: null },
    )
    const intents = browserPublishersIntents(owner({ generation: 1 }), 1, primedClient())
    expect(await intents.addAlias('R-1', 'OUP')).toEqual({ status: 'error', message: 'alias already used' })
    expect(await intents.create('OUP')).toEqual({ status: 'error', message: 'Could not add publisher.' })
  })

  it('does not retry a write and reports a transport failure as the action failure', async () => {
    const failure = vi.fn()
    vi.stubGlobal('prksOfflineNoteRequestFailure', failure)
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', fetchMock)
    const outcome = await browserPublishersIntents(owner({ generation: 1 }), 1, primedClient()).create('OUP')
    expect(outcome).toEqual({ status: 'error', message: 'Could not add publisher.' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(failure).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['a malformed reply', () => new Response(JSON.stringify({ id: 'R-1' }), { status: 200 })],
    ['a server error', () => new Response(JSON.stringify(null), { status: 500 })],
    [
      'a lost reply',
      () => {
        throw new TypeError('Failed to fetch')
      },
    ],
  ])('refetches the list after a write that may have committed but got %s', async (_label, reply) => {
    const server = [{ id: 'R-0', name: 'Old', aliases: [], work_count: 0 }]
    const requests: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit = {}) => {
        const method = init.method ?? 'GET'
        requests.push(`${method} ${url}`)
        if (method === 'GET') return new Response(JSON.stringify(server), { status: 200 })
        server.push({ id: 'R-1', name: 'OUP', aliases: [], work_count: 0 })
        return reply()
      }),
    )
    const client = createPrksQueryClient()
    const observer = new QueryObserver(client, {
      queryKey: prksQueryKeys.publishers.inUse(),
      queryFn: ({ signal }) => listPublishersInUse(signal),
      retry: false,
    })
    const unsubscribe = observer.subscribe(() => {})
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toHaveLength(1))
    const outcome = await browserPublishersIntents(owner({ generation: 1 }), 1, client).create('OUP')
    expect(outcome).toEqual({ status: 'error', message: 'Could not add publisher.' })
    expect(requests).toEqual(['GET /api/publishers?used=1', 'POST /api/publishers', 'GET /api/publishers?used=1'])
    expect(observer.getCurrentResult().data?.map((row) => row.id)).toEqual(['R-0', 'R-1'])
    unsubscribe()
  })

  it('stays quiet and sends nothing when the offline guard refuses the write', async () => {
    const calls = stubFetch()
    const guard = vi.fn(() => true)
    window.prksOfflineGuardMutation = guard
    const outcome = await browserPublishersIntents(owner({ generation: 1 }), 1, primedClient()).create('OUP')
    expect(outcome.status).toBe('quiet')
    expect(guard).toHaveBeenCalledWith('Publishers require a connection to PRKS.')
    expect(calls).toEqual([])
  })

  it('reports nothing to an owner that left during the write, but still invalidates', async () => {
    const state = { generation: 2 }
    let release: (() => void) | undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        await gate
        return new Response(JSON.stringify({ status: 'added' }), { status: 200 })
      }),
    )
    const client = primedClient()
    const pending = browserPublishersIntents(owner(state), 2, client).addAlias('R-1', 'OUP')
    state.generation = 3
    release?.()
    expect((await pending).status).toBe('quiet')
    expect(client.getQueryState(prksQueryKeys.publishers.inUse())?.isInvalidated).toBe(true)
  })

  it('does not delete after confirm once the owner has left', async () => {
    const calls = stubFetch()
    const state = { generation: 4 }
    window.prksConfirmDestructive = async () => {
      state.generation = 5
      return true
    }
    const outcome = await browserPublishersIntents(owner(state), 4, primedClient()).remove('p1', 'OUP')
    expect(outcome.status).toBe('quiet')
    expect(calls).toEqual([])
  })

  it('confirms, deletes, and invalidates', async () => {
    const calls = stubFetch({ body: { status: 'deleted' } })
    window.prksConfirmDestructive = async (opts) => {
      expect(opts.title).toBe('Delete publisher “OUP”?')
      expect(opts.confirmLabel).toBe('Delete publisher')
      return true
    }
    const client = primedClient()
    const outcome = await browserPublishersIntents(owner({ generation: 4 }), 4, client).remove('p1', 'OUP')
    expect(outcome.status).toBe('success')
    expect(calls).toEqual([{ url: '/api/publishers/p1', method: 'DELETE', body: undefined }])
    expect(client.getQueryState(prksQueryKeys.publishers.inUse())?.isInvalidated).toBe(true)
  })
})
