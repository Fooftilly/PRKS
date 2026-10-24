/** Generated Search/Saved View codec contracts against the real hash parser.
 *
 * Values include URL-reserved characters and multilingual scalar text. Keep
 * the suite bounded and deterministic tests alongside it (#391).
 */
import fc from 'fast-check'
import { describe, expect, it } from 'vitest'
import { parseRoute } from '../../routing/route-model'
import {
  SEARCH_UNSAVABLE_MESSAGE,
  definitionFromRoute,
  hashFromDefinition,
  optionsFromDefinition,
} from './codec'

const RUNS = { numRuns: 36 }
// Explicit Unicode scalar alphabet: an isolated UTF-16 surrogate is not a
// round-trippable URL value because URLSearchParams converts it to U+FFFD.
const field = fc.array(
  fc.constantFrom('a', 'Z', '9', ' ', '+', '&', '%', '?', '#', '/', 'é', 'λ', '中', '🔥'),
  { maxLength: 20 },
).map((chars) => chars.join(''))
const nonempty = fc.tuple(fc.constantFrom('x', 'λ', '中', '🔥'), field)
  .map(([head, rest]) => head + rest)

const validSearch = fc.oneof(
  nonempty.map((q) => ({ mode: 'all' as const, params: { any: '1', q } })),
  fc.record({ tag: nonempty, author: field, publisher: field })
    .map(({ tag, author, publisher }) => ({
      mode: 'tag' as const,
      params: { tag, author, publisher },
    })),
  fc.record({ q: nonempty, author: field, publisher: field })
    .map(({ q, author, publisher }) => ({
      mode: 'advanced' as const,
      params: { q, author, publisher },
    })),
)

const invalidSearch = fc.oneof(
  fc.record({ q: nonempty, tag: nonempty }),
  fc.record({ any: fc.constant('1'), q: nonempty, tag: nonempty }),
  fc.record({ any: fc.constant('yes'), q: nonempty, author: nonempty }),
  fc.record({ any: fc.constant('true'), q: nonempty, publisher: nonempty }),
)

describe('fast-check Saved View query codec', () => {
  it('round-trips valid definitions through canonical hash and real route parser', () => {
    fc.assert(fc.property(validSearch, ({ mode, params }) => {
      const initial = definitionFromRoute({ name: 'search', params })
      expect(initial.ok).toBe(true)
      if (!initial.ok) throw new Error('Generated valid search became unsavable')
      expect(initial.definition.mode).toBe(mode)
      const hash = hashFromDefinition(initial.definition)
      const route = parseRoute(hash)
      expect(route.name).toBe('search')
      const decoded = definitionFromRoute(route)
      expect(decoded).toEqual(initial)
      if (!decoded.ok) throw new Error('Round-trip unexpectedly rejected saved search')
      expect(hashFromDefinition(decoded.definition)).toBe(hash)

      // Query order is not semantic; permuting keys must preserve a Saved View.
      const fields = [...new URLSearchParams(hash.slice('#/search?'.length)).entries()]
      const reordered = '#/search?' + new URLSearchParams(fields.reverse()).toString()
      expect(definitionFromRoute(parseRoute(reordered))).toEqual(initial)

      const d = initial.definition
      const fetched = optionsFromDefinition(d)
      if (mode === 'all') {
        expect(fetched).toEqual({ q: d.q, tag: null, options: { any: '1' } })
      } else if (mode === 'tag') {
        expect(fetched).toEqual({
          q: '', tag: d.tag, options: { author: d.author, publisher: d.publisher },
        })
      } else {
        expect(fetched).toEqual({
          q: d.q, tag: null, options: { author: d.author, publisher: d.publisher },
        })
      }
    }), RUNS)
  })

  it('never serializes an incompatible search as a savable definition', () => {
    fc.assert(fc.property(invalidSearch, (params) => {
      const before = JSON.stringify(params)
      const result = definitionFromRoute({ name: 'search', params })
      expect(result.ok).toBe(false)
      if (!result.ok) {
        expect(result.unsavable).toBe(true)
        expect(result.empty).toBeUndefined()
        expect(result.message).toBe(SEARCH_UNSAVABLE_MESSAGE)
      }
      expect(JSON.stringify(params)).toBe(before)
    }), RUNS)
  })
})
