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
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        expect(init?.signal).toBe(controller.signal)
        throw new DOMException('Aborted', 'AbortError')
      }),
    )
    await expect(prksApiRequest('/api/diagnostics/performance', { signal: controller.signal })).rejects.toSatisfy(isAbortError)
  })

  it('refuses a query string on the path', async () => {
    await expect(prksApiRequest('/api/diagnostics/performance?q=secret')).rejects.toBeInstanceOf(TypeError)
  })
})
