import { describe, expect, expectTypeOf, it } from 'vitest'
import {
  HOME_HASH,
  PEOPLE_ROLES,
  PROGRESS_STATUS_VALUES,
  ROUTE_META,
  ROUTE_NAMES,
  graphFocusHash,
  isRecognizedRoute,
  parseGraphFocus,
  parseRoute,
  type PrksParsedRoute,
  type PrksRouteName,
} from './route-model'
import type { PrksRouteInstance } from '../route-surface/routes'

describe('route model', () => {
  it('parses the home and section hashes', () => {
    expect(parseRoute('')).toMatchObject({ name: 'folders', hash: HOME_HASH, canonicalize: false })
    expect(parseRoute('#/')).toMatchObject({ name: 'folders', canonicalHash: HOME_HASH, canonicalize: true })
    expect(parseRoute('#/folders')).toMatchObject({ name: 'folders', canonicalHash: '#/folders', canonicalize: false })
    expect(parseRoute('#/recent').name).toBe('recent')
    expect(parseRoute('#/views').name).toBe('saved-views')
    expect(parseRoute('#/people/groups').name).toBe('people-groups')
    expect(parseRoute('#/processing-files').name).toBe('processing-files')
  })

  it('decodes ids and re-encodes the canonical hash', () => {
    const route = parseRoute('#/works/a%20b')
    expect(route.name).toBe('work')
    if (route.name !== 'work') return
    expect(route.params.workId).toBe('a b')
    expect(route.canonicalHash).toBe('#/works/a%20b')
    expect(route.detail).toBe(true)
    expect(route.parentSection).toBe(ROUTE_META.work.sectionHash)
  })

  it('rejects malformed and unregistered hashes as unknown', () => {
    for (const hash of ['#/works/%E0%A4', '#/works', '#/nope', '#/folders/a/b', '#/people/a/b']) {
      const route = parseRoute(hash)
      expect(route.name, hash).toBe('unknown')
      expect(isRecognizedRoute(route), hash).toBe(false)
    }
    expect(isRecognizedRoute(parseRoute('#/tags'))).toBe(true)
    expect(isRecognizedRoute(null)).toBe(false)
  })

  it('canonicalizes progress status and keeps the shared list', () => {
    expect(PROGRESS_STATUS_VALUES).toEqual(['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'])
    for (const status of PROGRESS_STATUS_VALUES) {
      const route = parseRoute('#/progress?status=' + encodeURIComponent(status))
      expect(route).toMatchObject({ name: 'progress', params: { status }, canonicalize: false })
    }
    for (const hash of ['#/progress', '#/progress?status=', '#/progress?status=Finished']) {
      expect(parseRoute(hash), hash).toMatchObject({
        name: 'progress',
        params: { status: 'Not Started' },
        canonicalHash: '#/progress?status=Not%20Started',
        canonicalize: true,
      })
    }
  })

  it('marks known People roles', () => {
    expect(PEOPLE_ROLES).not.toContain('Mentioned')
    expect(parseRoute('#/people/role/Author').params).toEqual({ role: 'Author', knownRole: true })
    expect(parseRoute('#/people/role/Mentioned').params).toEqual({ role: 'Mentioned', knownRole: false })
  })

  it('validates graph focus', () => {
    expect(parseGraphFocus('focus=concept:a1')).toBe('concept:a1')
    expect(parseGraphFocus('focus=bad:1')).toBe('')
    expect(graphFocusHash('concept', 'a1')).toBe('#/graph?focus=concept%3Aa1')
    expect(graphFocusHash('bad', 'a1')).toBe('#/graph')
    expect(parseRoute('#/graph?focus=work:1.2-x').params).toEqual({ focus: 'work:1.2-x' })
  })

  it('carries search params', () => {
    expect(parseRoute('#/search?q=a&author=b').params).toEqual({ q: 'a', tag: '', author: 'b', publisher: '', any: '' })
  })

  it('lists every registry name and nothing else', () => {
    expect([...ROUTE_NAMES].sort()).toEqual(Object.keys(ROUTE_META).sort())
    expect(ROUTE_NAMES).toContain('unknown')
  })

  it('types parsed routes and mounted instances by registry name', () => {
    expectTypeOf<PrksParsedRoute['name']>().toEqualTypeOf<PrksRouteName>()
    expectTypeOf<PrksRouteInstance['name']>().toExtend<PrksRouteName>()
    const route = parseRoute('#/concepts/c1')
    if (route.name === 'concept-detail') expectTypeOf(route.params).toEqualTypeOf<{ readonly conceptId: string }>()
  })
})
