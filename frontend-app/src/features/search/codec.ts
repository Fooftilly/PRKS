/**
 * Canonical Search / Saved View query codec.
 *
 * One implementation. Vue imports these functions. The maintainer build also
 * emits them as the classic script `frontend/js/search-query-codec.js`, whose
 * global `prksSearchQueryCodec` is the only legacy bridge.
 *
 * This module does not parse hashes. `prksParseRoute` in `frontend/js/navigation.js`
 * remains the only hash parser. A search definition is already-parsed route params.
 */

export const SEARCH_UNSAVABLE_MESSAGE = 'This search combination cannot be saved as a view.'

export interface SearchDefinition {
  readonly mode: 'all' | 'tag' | 'advanced'
  readonly q: string
  readonly tag: string
  readonly author: string
  readonly publisher: string
}

export interface SearchDefinitionFailure {
  readonly ok: false
  readonly empty?: true
  readonly unsavable: true
  readonly message: string
}

export interface SearchDefinitionSuccess {
  readonly ok: true
  readonly definition: SearchDefinition
}

export type SearchDefinitionResult = SearchDefinitionSuccess | SearchDefinitionFailure

export interface SearchFetchOptions {
  readonly q: string
  readonly tag: string | null
  readonly options: { readonly any: '1' } | { readonly author: string; readonly publisher: string }
}

function recordOf(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object') return value as Record<string, unknown>
  return {}
}

function paramsFromRoute(route: unknown): Record<string, unknown> {
  const record = recordOf(route)
  const params = record.params
  if (params && typeof params === 'object') return params as Record<string, unknown>
  return record
}

function truthyAny(raw: unknown): boolean {
  const anyRaw = raw == null ? '' : raw
  return (
    anyRaw === '1' ||
    String(anyRaw).trim().toLowerCase() === 'true' ||
    String(anyRaw).trim().toLowerCase() === 'yes'
  )
}

function trimmed(value: unknown): string {
  return String(value || '').trim()
}

function unsavable(empty = false): SearchDefinitionFailure {
  return empty
    ? { ok: false, empty: true, unsavable: true, message: SEARCH_UNSAVABLE_MESSAGE }
    : { ok: false, unsavable: true, message: SEARCH_UNSAVABLE_MESSAGE }
}

/** Route params → a savable definition, or the existing unsavable result. */
export function definitionFromRoute(route: unknown): SearchDefinitionResult {
  const params = paramsFromRoute(route)
  const q = trimmed(params.q)
  const tag = trimmed(params.tag)
  const author = trimmed(params.author)
  const publisher = trimmed(params.publisher)
  const any = truthyAny(params.any)
  if (!q && !tag && !author && !publisher) return unsavable(true)
  if (any && (tag || author || publisher)) return unsavable()
  if (tag && q) return unsavable()
  if (any) {
    if (!q) return unsavable()
    return {
      ok: true,
      definition: { mode: 'all', q, tag: '', author: '', publisher: '' },
    }
  }
  if (tag) {
    return {
      ok: true,
      definition: { mode: 'tag', q: '', tag, author, publisher },
    }
  }
  return {
    ok: true,
    definition: { mode: 'advanced', q, tag: '', author, publisher },
  }
}

/** Definition → canonical `#/search?...`. Parameter order follows mode. */
export function hashFromDefinition(definition: unknown): string {
  const d = recordOf(definition)
  const p = new URLSearchParams()
  const mode = String(d.mode || '')
  const q = trimmed(d.q)
  const tag = trimmed(d.tag)
  const author = trimmed(d.author)
  const publisher = trimmed(d.publisher)
  if (mode === 'all') {
    p.set('any', '1')
    if (q) p.set('q', q)
  } else if (mode === 'tag') {
    if (tag) p.set('tag', tag)
    if (author) p.set('author', author)
    if (publisher) p.set('publisher', publisher)
  } else {
    if (q) p.set('q', q)
    if (author) p.set('author', author)
    if (publisher) p.set('publisher', publisher)
  }
  return '#/search?' + p.toString()
}

/** Saved definition → the `fetchSearch` arguments the coordinator already uses. */
export function optionsFromDefinition(definition: unknown): SearchFetchOptions {
  const d = recordOf(definition)
  const q = trimmed(d.q)
  const tag = trimmed(d.tag)
  const author = trimmed(d.author)
  const publisher = trimmed(d.publisher)
  if (d.mode === 'all') return { q, tag: null, options: { any: '1' } }
  if (d.mode === 'tag') return { q: '', tag, options: { author, publisher } }
  return { q, tag: null, options: { author, publisher } }
}

/** Saved View summary. Display text is not re-trimmed. */
export function summaryText(definition: unknown): string {
  const d = recordOf(definition)
  const parts: string[] = []
  if (d.mode === 'all') return 'All: ' + String(d.q || '')
  if (d.mode === 'tag') {
    parts.push('Tag: ' + String(d.tag || ''))
    if (d.author) parts.push('Author: ' + d.author)
    if (d.publisher) parts.push('Publisher: ' + d.publisher)
    return parts.join(' · ')
  }
  if (d.q) parts.push('Keywords: ' + d.q)
  if (d.author) parts.push('Author: ' + d.author)
  if (d.publisher) parts.push('Publisher: ' + d.publisher)
  return parts.join(' · ')
}
