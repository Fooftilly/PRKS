import { describe, expect, it } from 'vitest'
import {
  acceptSearchRequest,
  buildSearchRouteProjection,
  isAnySearch,
  SEARCH_NO_RESULTS,
  SEARCH_NO_TAGGED_FILES,
  searchCanOfferSave,
  searchTitle,
} from './projection'

describe('search projection', () => {
  it('accepts route params with the same any truthiness fetchSearch sends', () => {
    expect(isAnySearch('1')).toBe(true)
    expect(isAnySearch(' Yes ')).toBe(true)
    expect(isAnySearch('TRUE')).toBe(true)
    expect(isAnySearch('0')).toBe(false)
    expect(isAnySearch(undefined)).toBe(false)
    expect(acceptSearchRequest({ q: ' a ', tag: null, author: 'b', any: '1' })).toEqual({
      q: 'a',
      tag: '',
      author: 'b',
      publisher: '',
      any: true,
    })
    expect(acceptSearchRequest(null)).toEqual({ q: '', tag: '', author: '', publisher: '', any: false })
  })

  it('titles every request shape like the legacy painter', () => {
    const req = (r: Partial<ReturnType<typeof acceptSearchRequest>>) => acceptSearchRequest(r)
    expect(searchTitle(req({ tag: 'T', q: 'ignored' }))).toBe('Files tagged “T”')
    expect(searchTitle(req({ q: 'q', author: 'a', publisher: 'p' }))).toBe(
      'Search: “q” · author “a” · publisher “p”',
    )
    expect(searchTitle(req({ author: 'a', publisher: 'p' }))).toBe('Author “a” · publisher “p”')
    expect(searchTitle(req({ q: 'q', publisher: 'p' }))).toBe('Search: “q” · publisher “p”')
    expect(searchTitle(req({ publisher: 'p' }))).toBe('Files with publisher matching “p”')
    expect(searchTitle(req({ q: 'q', author: 'a' }))).toBe('Search: “q” · author “a”')
    expect(searchTitle(req({ author: 'a' }))).toBe('Files with author matching “a”')
    expect(searchTitle(req({ q: '<b>' }))).toBe('Search results for “<b>”')
  })

  it('offers Save View for any non-empty request and picks the empty copy', () => {
    expect(searchCanOfferSave(acceptSearchRequest({}))).toBe(false)
    expect(searchCanOfferSave(acceptSearchRequest({ publisher: 'p' }))).toBe(true)
    const tagged = buildSearchRouteProjection({
      request: { tag: 'T' },
      canonicalHash: '#/search?tag=T',
      rows: [{ id: 'w1' }, null, [], 'x'],
      generation: 3,
    })
    expect(tagged.results.rows).toEqual([{ id: 'w1' }])
    expect(tagged.results.emptyMessage).toBe(SEARCH_NO_TAGGED_FILES)
    expect(tagged.results.generation).toBe(3)
    const plain = buildSearchRouteProjection({ request: { q: 'q' }, canonicalHash: '#/search?q=q', generation: 1 })
    expect(plain.results.rows).toEqual([])
    expect(plain.results.emptyMessage).toBe(SEARCH_NO_RESULTS)
  })
})
