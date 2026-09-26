import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError, isAbortError, prksApiRequest } from './http'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('prksApiRequest', () => {
  it('returns parsed JSON and sends same-origin JSON headers', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(prksApiRequest('/api/diagnostics/performance')).resolves.toEqual({ ok: true })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/diagnostics/performance')
    expect(init.credentials).toBe('same-origin')
    expect((init.headers as Record<string, string>).Accept).toBe('application/json')
  })

  it('normalizes an error envelope without keeping the raw body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: 'unavailable', code: 'busy', secret: 'nope' }), { status: 503 })),
    )
    try {
      await prksApiRequest('/api/diagnostics/performance')
      expect.unreachable()
    } catch (error) {
      expect(error).toBeInstanceOf(PrksApiError)
      const apiError = error as PrksApiError
      expect(apiError.status).toBe(503)
      expect(apiError.code).toBe('busy')
      expect(apiError.message).toBe('unavailable')
      expect(apiError.message).not.toContain('nope')
    }
  })

  it('passes AbortSignal through and does not wrap abort', async () => {
    const success = vi.fn()
    const failure = vi.fn()
    vi.stubGlobal('prksOfflineNoteRequestSuccess', success)
    vi.stubGlobal('prksOfflineNoteRequestFailure', failure)
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        expect(init?.signal).toBe(controller.signal)
        throw new DOMException('Aborted', 'AbortError')
      }),
    )
    await expect(prksApiRequest('/api/diagnostics/performance', { signal: controller.signal })).rejects.toSatisfy(isAbortError)
    expect(success).not.toHaveBeenCalled()
    expect(failure).not.toHaveBeenCalled()
  })

  it('reports any resolved HTTP response as reachable and defers transport failure', async () => {
    const success = vi.fn()
    const failure = vi.fn()
    vi.stubGlobal('prksOfflineNoteRequestSuccess', success)
    vi.stubGlobal('prksOfflineNoteRequestFailure', failure)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: 'unavailable' }), { status: 503 })),
    )
    await expect(prksApiRequest('/api/diagnostics/performance')).rejects.toBeInstanceOf(PrksApiError)
    expect(success).toHaveBeenCalledTimes(1)
    expect(failure).not.toHaveBeenCalled()

    success.mockClear()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    await expect(prksApiRequest('/api/diagnostics/performance')).rejects.toBeInstanceOf(TypeError)
    expect(success).not.toHaveBeenCalled()
    expect(failure).not.toHaveBeenCalled()
  })

  it('does not treat a managed PDF response as reachability', async () => {
    const success = vi.fn()
    const failure = vi.fn()
    vi.stubGlobal('prksOfflineNoteRequestSuccess', success)
    vi.stubGlobal('prksOfflineNoteRequestFailure', failure)
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
    await expect(prksApiRequest('/api/pdfs/item')).resolves.toEqual({})
    expect(success).not.toHaveBeenCalled()
    expect(failure).not.toHaveBeenCalled()
  })

  it('ignores missing reachability hooks', async () => {
    vi.stubGlobal('prksOfflineNoteRequestSuccess', undefined)
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
    await expect(prksApiRequest('/api/diagnostics/performance')).resolves.toEqual({})
  })

  it('refuses a query string on the path', async () => {
    await expect(prksApiRequest('/api/diagnostics/performance?q=secret')).rejects.toBeInstanceOf(TypeError)
  })
})
