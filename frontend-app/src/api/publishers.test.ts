import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from './http'
import type { PublisherAliasAdded, PublisherCreated, PublisherDeleted, PublisherInUse } from './generated/publishers'
import {
  PUBLISHER_ALIAS_ADDED_KEYS,
  PUBLISHER_CREATED_KEYS,
  PUBLISHER_DELETED_KEYS,
  PUBLISHER_IN_USE_KEYS,
  deletePublisher,
  listPublishersInUse,
  parsePublisherCreated,
  parsePublishersInUse,
} from './publishers'

type SameKeys<T, Keys extends readonly (keyof T)[]> = [Exclude<keyof T, Keys[number]>, Exclude<Keys[number], keyof T>] extends [
  never,
  never,
]
  ? true
  : false

type RequiredKeys<T> = { [K in keyof T]-?: undefined extends T[K] ? never : K }[keyof T]

type SameRequired<T, Keys extends readonly (keyof T)[]> = [
  Exclude<RequiredKeys<T>, Keys[number]>,
  Exclude<Keys[number], RequiredKeys<T>>,
] extends [never, never]
  ? true
  : false

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Publishers client', () => {
  it('keeps runtime response keys aligned with the generated transport types', () => {
    const inUse: SameKeys<PublisherInUse, typeof PUBLISHER_IN_USE_KEYS> = true
    const created: SameKeys<PublisherCreated, typeof PUBLISHER_CREATED_KEYS> = true
    const aliasAdded: SameKeys<PublisherAliasAdded, typeof PUBLISHER_ALIAS_ADDED_KEYS> = true
    const deleted: SameKeys<PublisherDeleted, typeof PUBLISHER_DELETED_KEYS> = true
    const inUseRequired: SameRequired<PublisherInUse, typeof PUBLISHER_IN_USE_KEYS> = true
    const createdRequired: SameRequired<PublisherCreated, typeof PUBLISHER_CREATED_KEYS> = true
    const aliasAddedRequired: SameRequired<PublisherAliasAdded, typeof PUBLISHER_ALIAS_ADDED_KEYS> = true
    const deletedRequired: SameRequired<PublisherDeleted, typeof PUBLISHER_DELETED_KEYS> = true
    expect(inUse && created && aliasAdded && deleted && inUseRequired && createdRequired && aliasAddedRequired && deletedRequired).toBe(true)
  })

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
