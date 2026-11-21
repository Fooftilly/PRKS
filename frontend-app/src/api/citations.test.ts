import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from './http'
import { fetchBibtex, fetchCslItems, parseCitationsBibtex, parseCitationsCslJson } from './citations'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('citations client', () => {
  it('posts the selection and parses CSL-JSON items', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ format: 'csl-json', items: [{ id: 'W-1', type: 'book', title: 'T' }], missing: ['W-9'] })),
    )
    vi.stubGlobal('fetch', fetchMock)
    await expect(fetchCslItems(['W-1', 'W-9'])).resolves.toEqual({
      format: 'csl-json',
      items: [{ id: 'W-1', type: 'book', title: 'T' }],
      missing: ['W-9'],
    })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/citations')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ work_ids: ['W-1', 'W-9'], format: 'csl-json' })
  })

  it('parses a BibTeX answer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ format: 'bibtex', text: '@book{x}', missing: [] }))))
    await expect(fetchBibtex(['W-1'])).resolves.toEqual({ format: 'bibtex', text: '@book{x}', missing: [] })
  })

  it('refuses a malformed answer', () => {
    for (const bad of [null, [], { format: 'bibtex', items: [], missing: [] }, { format: 'csl-json', items: [{ id: 1 }], missing: [] },
      { format: 'csl-json', items: [], missing: [3] }]) {
      expect(() => parseCitationsCslJson(bad)).toThrow(PrksApiError)
    }
    expect(() => parseCitationsBibtex({ format: 'bibtex', text: 3, missing: [] })).toThrow(PrksApiError)
  })
})
