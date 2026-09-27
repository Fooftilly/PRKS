import { MutationObserver } from '@tanstack/query-core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError, prksApiRequest } from '../api/http'
import { createPrksQueryClient } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createPrksQueryClient', () => {
  it('sets explicit disposable-server-state defaults', () => {
    const client = createPrksQueryClient()
    const queries = client.getDefaultOptions().queries
    const mutations = client.getDefaultOptions().mutations
    expect(queries?.staleTime).toBe(30_000)
    expect(queries?.gcTime).toBe(5 * 60 * 1000)
    expect(queries?.refetchOnWindowFocus).toBe(false)
    expect(queries?.refetchOnReconnect).toBe(false)
    expect(queries?.networkMode).toBe('always')
    expect(mutations?.retry).toBe(0)
    expect(mutations?.networkMode).toBe('always')
  })

  it('shares one in-flight read and does not refetch fresh data', async () => {
    const client = createPrksQueryClient()
    let calls = 0
    let release: (() => void) | undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const queryKey = ['performance-diagnostics'] as const
    const queryFn = async () => {
      calls += 1
      await gate
      return { ok: true }
    }
    const first = client.fetchQuery({ queryKey, queryFn, staleTime: Number.POSITIVE_INFINITY })
    const second = client.fetchQuery({ queryKey, queryFn, staleTime: Number.POSITIVE_INFINITY })
    release?.()
    await Promise.all([first, second])
    expect(calls).toBe(1)
    await client.fetchQuery({ queryKey, queryFn, staleTime: Number.POSITIVE_INFINITY })
    expect(calls).toBe(1)
  })

  it('retries a 503 twice and does not retry a 400', async () => {
    const client = createPrksQueryClient()
    let unavailable = 0
    await expect(
      client.fetchQuery({
        queryKey: ['retry-503'],
        retryDelay: 0,
        queryFn: () => {
          unavailable += 1
          throw new PrksApiError('unavailable', 503, null)
        },
      }),
    ).rejects.toBeInstanceOf(PrksApiError)
    expect(unavailable).toBe(3)

    let rejected = 0
    await expect(
      client.fetchQuery({
        queryKey: ['retry-400'],
        retryDelay: 0,
        queryFn: () => {
          rejected += 1
          throw new PrksApiError('no', 400, 'invalid_request')
        },
      }),
    ).rejects.toBeInstanceOf(PrksApiError)
    expect(rejected).toBe(1)
  })

  it('aborts the query function when the query is cancelled', async () => {
    const client = createPrksQueryClient()
    let seen: AbortSignal | undefined
    const pending = client.fetchQuery({
      queryKey: ['cancel-me'],
      retry: false,
      queryFn: ({ signal }) =>
        new Promise((_resolve, reject) => {
          seen = signal
          signal.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'))
          })
        }),
    })
    await vi.waitFor(() => {
      if (!seen) throw new Error('signal not captured')
    })
    await client.cancelQueries({ queryKey: ['cancel-me'] })
    await expect(pending).rejects.toThrow()
    expect(seen?.aborted).toBe(true)
  })

  it('reports transport failure only after query retries are exhausted', async () => {
    const success = vi.fn()
    const failure = vi.fn()
    vi.stubGlobal('prksOfflineNoteRequestSuccess', success)
    vi.stubGlobal('prksOfflineNoteRequestFailure', failure)
    let attempts = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        attempts += 1
        if (attempts < 3) throw new TypeError('Failed to fetch')
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      }),
    )
    const client = createPrksQueryClient()
    await expect(
      client.fetchQuery({
        queryKey: ['reachability-recovers'],
        retryDelay: 0,
        queryFn: ({ signal }) => prksApiRequest('/api/diagnostics/performance', { signal }),
      }),
    ).resolves.toEqual({ ok: true })
    expect(attempts).toBe(3)
    expect(success).toHaveBeenCalledTimes(1)
    expect(failure).not.toHaveBeenCalled()

    attempts = 0
    success.mockClear()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        attempts += 1
        throw new TypeError('Failed to fetch')
      }),
    )
    await expect(
      client.fetchQuery({
        queryKey: ['reachability-down'],
        retryDelay: 0,
        queryFn: ({ signal }) => prksApiRequest('/api/diagnostics/performance', { signal }),
      }),
    ).rejects.toBeInstanceOf(TypeError)
    expect(attempts).toBe(3)
    expect(success).not.toHaveBeenCalled()
    expect(failure).toHaveBeenCalledTimes(1)
  })

  it('reports a mutation transport failure once and treats HTTP errors as reachable', async () => {
    const success = vi.fn()
    const failure = vi.fn()
    vi.stubGlobal('prksOfflineNoteRequestSuccess', success)
    vi.stubGlobal('prksOfflineNoteRequestFailure', failure)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: 'no', code: 'invalid_request' }), { status: 400 })),
    )
    const client = createPrksQueryClient()
    await expect(
      client.fetchQuery({
        queryKey: ['reachability-400'],
        retryDelay: 0,
        queryFn: ({ signal }) => prksApiRequest('/api/diagnostics/performance', { signal }),
      }),
    ).rejects.toBeInstanceOf(PrksApiError)
    expect(success).toHaveBeenCalledTimes(1)
    expect(failure).not.toHaveBeenCalled()

    success.mockClear()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const observer = new MutationObserver(client, {
      mutationFn: () =>
        prksApiRequest('/api/diagnostics/performance/reset', { method: 'POST', body: '{}' }),
    })
    await expect(observer.mutate()).rejects.toBeInstanceOf(TypeError)
    expect(failure).toHaveBeenCalledTimes(1)
    expect(success).not.toHaveBeenCalled()
  })

  it('does not retry mutations', async () => {
    const client = createPrksQueryClient()
    let calls = 0
    const observer = new MutationObserver(client, {
      mutationFn: async () => {
        calls += 1
        throw new PrksApiError('no', 503, null)
      },
    })
    await expect(observer.mutate()).rejects.toBeInstanceOf(PrksApiError)
    expect(calls).toBe(1)
  })
})
