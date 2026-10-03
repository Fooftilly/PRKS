import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from './http'
import {
  deletePublisher,
  listPublishersInUse,
  parsePublisherCreated,
  parsePublishersInUse,
} from './publishers'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Publishers client', () => {
  it('parses the in-use list exactly', () => {
    expect(
      parsePublishersInUse([{ id: 'R-1', name: 'OUP', aliases: ['Oxford UP'], work_count: 3 }]),
    ).toEqual([{ id: 'R-1', name: 'OUP', aliases: ['Oxford UP'], work_count: 3 }])
    expect(parsePublishersInUse([])).toEqual([])
  })

  it.each([
    ['not a list', { id: 'R-1' }],
    ['missing key', [{ id: 'R-1', name: 'OUP', aliases: [] }]],
    ['non-string alias', [{ id: 'R-1', name: 'OUP', aliases: [4], work_count: 0 }]],
    ['negative count', [{ id: 'R-1', name: 'OUP', aliases: [], work_count: -1 }]],
    ['fractional count', [{ id: 'R-1', name: 'OUP', aliases: [], work_count: 1.5 }]],
    ['numeric id', [{ id: 1, name: 'OUP', aliases: [], work_count: 0 }]],
  ])('refuses a malformed list (%s)', (_label, payload) => {
    expect(() => parsePublishersInUse(payload)).toThrowError(
      expect.objectContaining({ code: 'invalid_response', message: 'Could not load publishers.' }),
    )
  })

  it('parses a create result and refuses one without `existed`', () => {
    expect(parsePublisherCreated({ id: 'R-1', name: 'OUP', existed: true })).toEqual({
      id: 'R-1',
      name: 'OUP',
      existed: true,
    })
    expect(() => parsePublisherCreated({ id: 'R-1', name: 'OUP' })).toThrow(PrksApiError)
  })

  it('reads the list with used=1 and passes the abort signal', async () => {
    const fetchMock = vi.fn(async () => new Response('[]', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    await expect(listPublishersInUse(controller.signal)).resolves.toEqual([])
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/publishers?used=1')
    expect(init.signal).toBe(controller.signal)
  })

  it('refuses a delete answered with an unexpected status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: 'added' }), { status: 200 })))
    await expect(deletePublisher('R-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
