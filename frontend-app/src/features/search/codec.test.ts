import { describe, expect, it } from 'vitest'
import {
  SEARCH_UNSAVABLE_MESSAGE,
  definitionFromRoute,
  hashFromDefinition,
  optionsFromDefinition,
  summaryText,
} from './codec'

const MESSAGE = 'This search combination cannot be saved as a view.'

function route(params: Record<string, unknown>) {
  return { name: 'search', params }
}

function queryOf(hash: string): URLSearchParams {
  return new URLSearchParams(hash.slice('#/search?'.length))
}

describe('search query codec', () => {
  it('rejects an empty search', () => {
    for (const input of [null, undefined, {}, route({}), route({ q: '  ', tag: '', any: '0' })]) {
      const parsed = definitionFromRoute(input)
      expect(parsed.ok).toBe(false)
      if (!parsed.ok) {
        expect(parsed.empty).toBe(true)
        expect(parsed.unsavable).toBe(true)
        expect(parsed.message).toBe(MESSAGE)
      }
    }
    expect(SEARCH_UNSAVABLE_MESSAGE).toBe(MESSAGE)
  })

  it('accepts a trimmed all-fields search', () => {
    const parsed = definitionFromRoute(route({ any: '1', q: '  critical theory  ' }))
    expect(parsed).toEqual({
      ok: true,
      definition: { mode: 'all', q: 'critical theory', tag: '', author: '', publisher: '' },
    })
    expect(definitionFromRoute(route({ any: ' TRUE ', q: 'x' })).ok).toBe(true)
    expect(definitionFromRoute(route({ any: 'yes', q: 'x' })).ok).toBe(true)
    const bare = definitionFromRoute({ q: 'from the route object itself', any: '1' })
    expect(bare.ok && bare.definition.mode).toBe('all')
    expect(bare.ok && bare.definition.q).toBe('from the route object itself')
  })

  it('accepts a tag search and an advanced search', () => {
    const tag = definitionFromRoute(route({ tag: '  Frankfurt School ', publisher: ' Verso ' }))
    expect(tag).toEqual({
      ok: true,
      definition: {
        mode: 'tag',
        q: '',
        tag: 'Frankfurt School',
        author: '',
        publisher: 'Verso',
      },
    })
    const advanced = definitionFromRoute(route({ q: ' culture industry ', author: ' Adorno ' }))
    expect(advanced).toEqual({
      ok: true,
      definition: {
        mode: 'advanced',
        q: 'culture industry',
        tag: '',
        author: 'Adorno',
        publisher: '',
      },
    })
    const authorOnly = definitionFromRoute(route({ author: 'Benjamin' }))
    expect(authorOnly.ok && authorOnly.definition.mode).toBe('advanced')
  })

  it('rejects any mixed with tag, author, or publisher, and tag mixed with q', () => {
    for (const params of [
      { any: '1', author: 'Adorno' },
      { any: '1', tag: 'Frankfurt' },
      { any: '1', publisher: 'Verso' },
      { any: 'yes', q: 'culture', author: 'Adorno' },
      { tag: 'Frankfurt', q: 'culture' },
      { tag: ' Frankfurt ', q: ' culture ' },
    ]) {
      const parsed = definitionFromRoute(route(params))
      expect(parsed.ok).toBe(false)
      if (!parsed.ok) {
        expect(parsed.unsavable).toBe(true)
        expect(parsed.empty).toBeUndefined()
        expect(parsed.message).toBe(MESSAGE)
      }
    }
  })

  it('serializes a canonical hash with stable parameter order', () => {
    const all = definitionFromRoute(route({ any: '1', q: 'critical theory' }))
    const allHash = hashFromDefinition(all.ok ? all.definition : null)
    expect(allHash).toBe('#/search?' + new URLSearchParams({ any: '1', q: 'critical theory' }).toString())
    expect(allHash.slice('#/search?'.length).split('&').map((part) => part.split('=')[0])).toEqual(['any', 'q'])

    const advanced = definitionFromRoute(route({ q: 'culture industry', author: 'Adorno' }))
    const advancedHash = hashFromDefinition(advanced.ok ? advanced.definition : null)
    const advancedParams = queryOf(advancedHash)
    expect([...advancedParams.keys()]).toEqual(['q', 'author'])
    expect(advancedParams.get('q')).toBe('culture industry')
    expect(advancedParams.get('author')).toBe('Adorno')
    expect(advancedParams.get('tag')).toBeNull()

    const tag = definitionFromRoute(route({ tag: 'Frankfurt School', publisher: 'Verso' }))
    const tagHash = hashFromDefinition(tag.ok ? tag.definition : null)
    const tagParams = queryOf(tagHash)
    expect([...tagParams.keys()]).toEqual(['tag', 'publisher'])
    expect(tagParams.get('tag')).toBe('Frankfurt School')
    expect(tagParams.get('publisher')).toBe('Verso')
    expect(tagParams.get('q')).toBeNull()

    expect(hashFromDefinition({ mode: 'all', q: '  spaced  ' })).toBe(
      '#/search?' + new URLSearchParams({ any: '1', q: 'spaced' }).toString(),
    )
    expect(hashFromDefinition(null)).toBe('#/search?')
    expect(hashFromDefinition({ mode: 'tag', tag: 'T', author: 'A', publisher: 'P' }).includes('tag=')).toBe(true)
  })

  it('maps a definition to fetchSearch arguments', () => {
    const all = definitionFromRoute(route({ any: '1', q: 'critical theory' }))
    expect(optionsFromDefinition(all.ok ? all.definition : null)).toEqual({
      q: 'critical theory',
      tag: null,
      options: { any: '1' },
    })
    expect(
      optionsFromDefinition({ mode: 'tag', q: 'ignored', tag: ' T ', author: ' A ', publisher: ' P ' }),
    ).toEqual({
      q: '',
      tag: 'T',
      options: { author: 'A', publisher: 'P' },
    })
    expect(
      optionsFromDefinition({ mode: 'advanced', q: ' q ', author: ' a ', publisher: ' p ' }),
    ).toEqual({
      q: 'q',
      tag: null,
      options: { author: 'a', publisher: 'p' },
    })
  })

  it('builds the Saved View summary without restating a second format', () => {
    const all = definitionFromRoute(route({ any: '1', q: 'critical theory' }))
    const advanced = definitionFromRoute(route({ q: 'culture industry', author: 'Adorno' }))
    const tag = definitionFromRoute(route({ tag: 'Frankfurt School', publisher: 'Verso' }))
    expect(summaryText(all.ok ? all.definition : null)).toBe('All: critical theory')
    expect(summaryText(advanced.ok ? advanced.definition : null)).toBe(
      'Keywords: culture industry · Author: Adorno',
    )
    expect(summaryText(tag.ok ? tag.definition : null)).toBe('Tag: Frankfurt School · Publisher: Verso')
    expect(summaryText({ mode: 'tag', tag: 'T', author: 'A', publisher: '' })).toBe('Tag: T · Author: A')
    expect(summaryText({ mode: 'all', q: '  x  ' })).toBe('All:   x  ')
    expect(summaryText({})).toBe('')
    expect(summaryText(null)).toBe('')
  })
})
