/**
 * Typed PRKS route model: the hash parser, the route registry, and
 * canonical hashes. This is the only hash parser. It is not a router:
 * the coordinator in `app.js` still loads and dispatches each route, and
 * `navigation.js` keeps session state, Back, titles, and sidebar matching.
 *
 * The maintainer build emits this module as the classic script
 * `frontend/js/route-model.js` (global `prksRouteModel`), loaded before
 * `navigation.js`, which publishes the same names on `window`. That script also
 * bundles the domain vocabularies the parser validates against (`src/domain/`),
 * which own those lists. Vue imports each module directly.
 */

import { isPeopleRole } from '../domain/people-roles'
import { WORK_STATUSES, isWorkStatus, type WorkStatus } from '../domain/work-status'

export const HOME_HASH = '#/folders'

export interface RouteMeta {
  readonly title: string
  readonly loadingTitle: string
  readonly backLabel: string
  readonly navHref: string | null
  readonly fallbackBack: string
  readonly sectionHash: string | null
  readonly detail?: boolean
  readonly tabIcon: string
}

export const ROUTE_META = {
  folders: {
    title: 'Folders',
    loadingTitle: 'Folders',
    backLabel: 'Folders',
    navHref: '#/folders',
    fallbackBack: '#/folders',
    sectionHash: '#/folders',
    tabIcon: 'folder',
  },
  'folder-detail': {
    title: 'Folder',
    loadingTitle: 'Folder',
    backLabel: 'Folder',
    navHref: '#/folders',
    fallbackBack: '#/folders',
    sectionHash: '#/folders',
    detail: true,
    tabIcon: 'folder',
  },
  recent: {
    title: 'Recent',
    loadingTitle: 'Recent',
    backLabel: 'Recently opened',
    navHref: '#/recent',
    fallbackBack: '#/folders',
    sectionHash: '#/recent',
    tabIcon: 'clock',
  },
  'saved-views': {
    title: 'Saved Views',
    loadingTitle: 'Saved Views',
    backLabel: 'Saved Views',
    navHref: '#/views',
    fallbackBack: '#/views',
    sectionHash: '#/views',
    tabIcon: 'bookmark',
  },
  'saved-view-detail': {
    title: 'Saved View',
    loadingTitle: 'Saved View',
    backLabel: 'Saved View',
    navHref: '#/views',
    fallbackBack: '#/views',
    sectionHash: '#/views',
    detail: true,
    tabIcon: 'bookmark',
  },
  types: {
    title: 'File Types',
    loadingTitle: 'File Types',
    backLabel: 'File types',
    navHref: '#/types',
    fallbackBack: '#/types',
    sectionHash: '#/types',
    tabIcon: 'library',
  },
  'type-detail': {
    title: 'File Type',
    loadingTitle: 'File Type',
    backLabel: 'File type',
    navHref: '#/types',
    fallbackBack: '#/types',
    sectionHash: '#/types',
    detail: true,
    tabIcon: 'library',
  },
  playlists: {
    title: 'Playlists',
    loadingTitle: 'Playlists',
    backLabel: 'Playlists',
    navHref: '#/playlists',
    fallbackBack: '#/playlists',
    sectionHash: '#/playlists',
    tabIcon: 'clapperboard',
  },
  'playlist-detail': {
    title: 'Playlist',
    loadingTitle: 'Playlist',
    backLabel: 'Playlist',
    navHref: '#/playlists',
    fallbackBack: '#/playlists',
    sectionHash: '#/playlists',
    detail: true,
    tabIcon: 'clapperboard',
  },
  tags: {
    title: 'Tags',
    loadingTitle: 'Tags',
    backLabel: 'Tags',
    navHref: '#/tags',
    fallbackBack: '#/folders',
    sectionHash: '#/tags',
    tabIcon: 'tags',
  },
  publishers: {
    title: 'Publishers',
    loadingTitle: 'Publishers',
    backLabel: 'Publishers',
    navHref: '#/publishers',
    fallbackBack: '#/folders',
    sectionHash: '#/publishers',
    tabIcon: 'building-2',
  },
  people: {
    title: 'People',
    loadingTitle: 'People',
    backLabel: 'People',
    navHref: '#/people',
    fallbackBack: '#/people',
    sectionHash: '#/people',
    tabIcon: 'users',
  },
  person: {
    title: 'Person',
    loadingTitle: 'Person',
    backLabel: 'Person',
    navHref: '#/people',
    fallbackBack: '#/people',
    sectionHash: '#/people',
    detail: true,
    tabIcon: 'user',
  },
  'people-role': {
    title: 'People',
    loadingTitle: 'People',
    backLabel: 'People',
    navHref: null,
    fallbackBack: '#/people',
    sectionHash: '#/people',
    tabIcon: 'users',
  },
  'people-groups': {
    title: 'Groups',
    loadingTitle: 'People Groups',
    backLabel: 'Groups',
    navHref: '#/people/groups',
    fallbackBack: '#/people/groups',
    sectionHash: '#/people/groups',
    tabIcon: 'folders',
  },
  'person-group-detail': {
    title: 'Group',
    loadingTitle: 'Group',
    backLabel: 'Group',
    navHref: '#/people/groups',
    fallbackBack: '#/people/groups',
    sectionHash: '#/people/groups',
    detail: true,
    tabIcon: 'folders',
  },
  concepts: {
    title: 'Concepts',
    loadingTitle: 'Concepts',
    backLabel: 'Concepts',
    navHref: '#/concepts',
    fallbackBack: '#/concepts',
    sectionHash: '#/concepts',
    tabIcon: 'network',
  },
  'concept-detail': {
    title: 'Concept',
    loadingTitle: 'Concept',
    backLabel: 'Concept',
    navHref: '#/concepts',
    fallbackBack: '#/concepts',
    sectionHash: '#/concepts',
    detail: true,
    tabIcon: 'network',
  },
  positions: {
    title: 'Positions',
    loadingTitle: 'Positions',
    backLabel: 'Positions',
    navHref: '#/positions',
    fallbackBack: '#/positions',
    sectionHash: '#/positions',
    tabIcon: 'flag',
  },
  'position-detail': {
    title: 'Position',
    loadingTitle: 'Position',
    backLabel: 'Position',
    navHref: '#/positions',
    fallbackBack: '#/positions',
    sectionHash: '#/positions',
    detail: true,
    tabIcon: 'flag',
  },
  arguments: {
    title: 'Arguments & Stances',
    loadingTitle: 'Arguments & Stances',
    backLabel: 'Arguments & Stances',
    navHref: '#/arguments',
    fallbackBack: '#/arguments',
    sectionHash: '#/arguments',
    tabIcon: 'messages-square',
  },
  'argument-detail': {
    title: 'Argument',
    loadingTitle: 'Argument',
    backLabel: 'Argument',
    navHref: '#/arguments',
    fallbackBack: '#/arguments',
    sectionHash: '#/arguments',
    detail: true,
    tabIcon: 'messages-square',
  },
  'research-graph': {
    title: 'Research Graph',
    loadingTitle: 'Research Graph',
    backLabel: 'Research Graph',
    navHref: '#/graph',
    fallbackBack: '#/graph',
    sectionHash: '#/graph',
    tabIcon: 'share-2',
  },
  progress: {
    title: 'Progress',
    loadingTitle: 'Progress',
    backLabel: 'Progress',
    navHref: null,
    fallbackBack: '#/folders',
    sectionHash: '#/progress',
    tabIcon: 'list',
  },
  'processing-files': {
    title: 'Files for Processing',
    loadingTitle: 'Files for Processing',
    backLabel: 'Files for Processing',
    navHref: '#/processing-files',
    fallbackBack: '#/folders',
    sectionHash: '#/processing-files',
    tabIcon: 'inbox',
  },
  search: {
    title: 'Search',
    loadingTitle: 'Search',
    backLabel: 'Search results',
    navHref: null,
    fallbackBack: '#/folders',
    sectionHash: '#/search',
    tabIcon: 'search',
  },
  work: {
    title: 'File',
    loadingTitle: 'Work',
    backLabel: 'File',
    navHref: '#/folders',
    fallbackBack: '#/folders',
    sectionHash: '#/folders',
    detail: true,
    tabIcon: 'file-text',
  },
  unknown: {
    title: 'Section unavailable',
    loadingTitle: 'Loading',
    backLabel: 'Folders',
    navHref: null,
    fallbackBack: '#/folders',
    sectionHash: null,
    tabIcon: 'file',
  },
} as const satisfies Record<string, RouteMeta>

export type PrksRouteName = keyof typeof ROUTE_META

export interface SearchRouteParams {
  readonly q: string
  readonly tag: string
  readonly author: string
  readonly publisher: string
  readonly any: string
}

/** Params each parsed route carries. Every route name has exactly one shape. */
export interface PrksRouteParamsByName {
  folders: Record<string, never>
  'folder-detail': { readonly folderId: string }
  recent: Record<string, never>
  'saved-views': Record<string, never>
  'saved-view-detail': { readonly viewId: string }
  types: Record<string, never>
  'type-detail': { readonly docType: string }
  playlists: Record<string, never>
  'playlist-detail': { readonly playlistId: string }
  tags: Record<string, never>
  publishers: Record<string, never>
  people: Record<string, never>
  person: { readonly personId: string }
  'people-role': { readonly role: string; readonly knownRole: boolean }
  'people-groups': Record<string, never>
  'person-group-detail': { readonly groupId: string }
  concepts: Record<string, never>
  'concept-detail': { readonly conceptId: string }
  positions: Record<string, never>
  'position-detail': { readonly positionId: string }
  arguments: { readonly kind: string }
  'argument-detail': { readonly argumentId: string }
  'research-graph': { readonly focus: string }
  progress: { readonly status: WorkStatus }
  'processing-files': Record<string, never>
  search: SearchRouteParams
  work: { readonly workId: string }
  unknown: Record<string, never>
}

type ParsedRouteMember<N extends PrksRouteName> = {
  readonly name: N
  /** The normalized hash that was parsed. */
  readonly hash: string
  readonly canonicalHash: string
  readonly params: PrksRouteParamsByName[N]
  readonly parentSection: string | null
  readonly detail: boolean
  /** True when the address bar should be replaced with `canonicalHash`. */
  readonly canonicalize: boolean
}

/** Discriminated result of `parseRoute`. `name` selects the params shape. */
export type PrksParsedRoute = { [N in PrksRouteName]: ParsedRouteMember<N> }[PrksRouteName]

/** Every route name the parser can return, registry order. */
export const ROUTE_NAMES = Object.keys(ROUTE_META) as PrksRouteName[]

function safeDecode(raw: unknown): string | null {
  const s = String(raw == null ? '' : raw)
  if (!s) return ''
  try {
    return decodeURIComponent(s)
  } catch {
    return null
  }
}

function encodePathSegment(raw: unknown): string {
  return encodeURIComponent(String(raw == null ? '' : raw))
}

function unknownRoute(hash: unknown): ParsedRouteMember<'unknown'> {
  const h = String(hash || '')
  return {
    name: 'unknown',
    hash: h,
    canonicalHash: h && h.charAt(0) === '#' ? h : h ? '#' + h : '#/unknown',
    params: {},
    parentSection: null,
    detail: false,
    canonicalize: false,
  }
}

function routeRecord<N extends PrksRouteName>(
  name: N,
  hash: string,
  canonicalHash: string,
  params: PrksRouteParamsByName[N],
  extra?: { readonly canonicalize?: boolean },
): ParsedRouteMember<N> {
  const meta: RouteMeta = ROUTE_META[name]
  return {
    name,
    hash,
    canonicalHash,
    params,
    parentSection: meta.sectionHash || null,
    detail: !!meta.detail,
    canonicalize: !!(extra && extra.canonicalize),
  }
}

/** Normalize raw address input. Null when it is not a safe in-app hash. */
export function normalizeHashInput(raw: unknown): string | null {
  let s = String(raw == null ? '' : raw).trim()
  if (!s || s === '#') return HOME_HASH
  if (s.charAt(0) === '/') s = '#' + s
  if (s.charAt(0) !== '#') return null
  if (/[\0\r\n]/.test(s)) return null
  const rest = s.slice(1)
  if (/^(javascript:|data:|vbscript:)/i.test(rest)) return null
  return s
}

interface SplitHash {
  readonly hash: string
  readonly query: string
  readonly segments: readonly string[]
}

function splitHash(raw: unknown): SplitHash | null {
  const normalized = normalizeHashInput(raw)
  if (normalized == null) return null
  const q = normalized.indexOf('?')
  const pathPart = q < 0 ? normalized : normalized.slice(0, q)
  const query = q < 0 ? '' : normalized.slice(q + 1)
  const path = pathPart.replace(/^#\/?/, '')
  const segments = path ? path.split('/') : []
  return { hash: normalized, query, segments }
}

function queryParams(query: string): URLSearchParams | null {
  try {
    return new URLSearchParams(query)
  } catch {
    return null
  }
}

function parseSearchParams(query: string): SearchRouteParams {
  const empty: SearchRouteParams = { q: '', tag: '', author: '', publisher: '', any: '' }
  if (!query) return empty
  const usp = queryParams(query)
  if (!usp) return empty
  return {
    q: usp.get('q') || '',
    tag: usp.get('tag') || '',
    author: usp.get('author') || '',
    publisher: usp.get('publisher') || '',
    any: usp.get('any') || '',
  }
}

function parseProgressStatus(query: string): WorkStatus | null {
  if (!query) return null
  const usp = queryParams(query)
  if (!usp) return null
  const raw = usp.get('status')
  if (raw == null || String(raw).trim() === '') return null
  const decoded = String(raw).trim()
  return isWorkStatus(decoded) ? decoded : null
}

/** Canonical `#/progress` hash for a status. */
export function progressCanonicalHash(status: WorkStatus): string {
  return '#/progress?status=' + encodePathSegment(status)
}

const GRAPH_FOCUS_RE = /^(concept|position|argument|work|person):[A-Za-z0-9][A-Za-z0-9._-]*$/

/** Graph focus from a `#/graph` query, or '' when absent or malformed. */
export function parseGraphFocus(query: string): string {
  if (!query) return ''
  const usp = queryParams(query)
  if (!usp) return ''
  const raw = usp.get('focus')
  if (raw == null) return ''
  const decoded = String(raw).trim()
  if (!GRAPH_FOCUS_RE.test(decoded)) return ''
  return decoded
}

function graphCanonical(focus: string): string {
  if (focus) return '#/graph?focus=' + encodePathSegment(focus)
  return '#/graph'
}

/** Canonical Research Graph hash focused on one node, or `#/graph`. */
export function graphFocusHash(nodeType: unknown, recordId: unknown): string {
  const t = String(nodeType || '').trim()
  const id = String(recordId || '').trim()
  if (!t || !id) return '#/graph'
  const focus = t + ':' + id
  if (!GRAPH_FOCUS_RE.test(focus)) return '#/graph'
  return graphCanonical(focus)
}

/** Decode one id segment. Null for a malformed or empty segment. */
function idSegment(raw: string | undefined): string | null {
  const id = safeDecode(raw)
  return id ? id : null
}

/** Parse one hash into a typed route. Unrecognized input is `unknown`. */
export function parseRoute(hash: unknown): PrksParsedRoute {
  const split = splitHash(hash)
  if (!split) return unknownRoute(String(hash || ''))
  const segs = split.segments
  const rawHash = split.hash
  if (!segs.length) {
    return routeRecord('folders', rawHash, HOME_HASH, {}, { canonicalize: rawHash !== HOME_HASH })
  }

  const head = segs[0]

  if (head === 'graph' && segs.length === 1) {
    const focus = parseGraphFocus(split.query)
    return routeRecord('research-graph', rawHash, graphCanonical(focus), { focus })
  }

  if (head === 'folders' && segs.length === 1) return routeRecord('folders', rawHash, '#/folders', {})
  if (head === 'folders' && segs.length === 2) {
    const folderId = idSegment(segs[1])
    if (folderId == null) return unknownRoute(rawHash)
    return routeRecord('folder-detail', rawHash, '#/folders/' + encodePathSegment(folderId), { folderId })
  }

  if (head === 'recent' && segs.length === 1) return routeRecord('recent', rawHash, '#/recent', {})

  if (head === 'views' && segs.length === 1) return routeRecord('saved-views', rawHash, '#/views', {})
  if (head === 'views' && segs.length === 2) {
    const viewId = idSegment(segs[1])
    if (viewId == null) return unknownRoute(rawHash)
    return routeRecord('saved-view-detail', rawHash, '#/views/' + encodePathSegment(viewId), { viewId })
  }

  if (head === 'types' && segs.length === 1) return routeRecord('types', rawHash, '#/types', {})
  if (head === 'types' && segs.length >= 2) {
    const docType = idSegment(segs.slice(1).join('/'))
    if (docType == null) return unknownRoute(rawHash)
    return routeRecord('type-detail', rawHash, '#/types/' + encodePathSegment(docType), { docType })
  }

  if (head === 'playlists' && segs.length === 1) return routeRecord('playlists', rawHash, '#/playlists', {})
  if (head === 'playlists' && segs.length === 2) {
    const playlistId = idSegment(segs[1])
    if (playlistId == null) return unknownRoute(rawHash)
    return routeRecord('playlist-detail', rawHash, '#/playlists/' + encodePathSegment(playlistId), {
      playlistId,
    })
  }

  if (head === 'tags' && segs.length === 1) return routeRecord('tags', rawHash, '#/tags', {})
  if (head === 'publishers' && segs.length === 1) return routeRecord('publishers', rawHash, '#/publishers', {})

  if (head === 'concepts' && segs.length === 1) return routeRecord('concepts', rawHash, '#/concepts', {})
  if (head === 'concepts' && segs.length === 2) {
    const conceptId = idSegment(segs[1])
    if (conceptId == null) return unknownRoute(rawHash)
    return routeRecord('concept-detail', rawHash, '#/concepts/' + encodePathSegment(conceptId), { conceptId })
  }

  if (head === 'positions' && segs.length === 1) return routeRecord('positions', rawHash, '#/positions', {})
  if (head === 'positions' && segs.length === 2) {
    const positionId = idSegment(segs[1])
    if (positionId == null) return unknownRoute(rawHash)
    return routeRecord('position-detail', rawHash, '#/positions/' + encodePathSegment(positionId), {
      positionId,
    })
  }

  if (head === 'arguments' && segs.length === 1) {
    const usp = queryParams(split.query)
    const kind = usp ? String(usp.get('kind') || '').trim() : ''
    const canonical = kind ? '#/arguments?kind=' + encodePathSegment(kind) : '#/arguments'
    return routeRecord('arguments', rawHash, canonical, { kind })
  }
  if (head === 'arguments' && segs.length === 2) {
    const argumentId = idSegment(segs[1])
    if (argumentId == null) return unknownRoute(rawHash)
    return routeRecord('argument-detail', rawHash, '#/arguments/' + encodePathSegment(argumentId), {
      argumentId,
    })
  }

  if (head === 'processing-files' && segs.length === 1) {
    return routeRecord('processing-files', rawHash, '#/processing-files', {})
  }

  if (head === 'search' && segs.length === 1) {
    const canonical = split.query ? '#/search?' + split.query : '#/search'
    return routeRecord('search', rawHash, canonical, parseSearchParams(split.query))
  }

  if (head === 'progress' && segs.length === 1) {
    const parsed = parseProgressStatus(split.query)
    const status = parsed || WORK_STATUSES[0]
    return routeRecord('progress', rawHash, progressCanonicalHash(status), { status }, { canonicalize: !parsed })
  }

  if (head === 'works' && segs.length === 2) {
    const workId = idSegment(segs[1])
    if (workId == null) return unknownRoute(rawHash)
    return routeRecord('work', rawHash, '#/works/' + encodePathSegment(workId), { workId })
  }

  if (head === 'people') {
    if (segs.length === 1) return routeRecord('people', rawHash, '#/people', {})
    if (segs[1] === 'role' && segs.length >= 3) {
      const role = safeDecode(segs.slice(2).join('/'))
      if (role == null) return unknownRoute(rawHash)
      const knownRole = isPeopleRole(role)
      return routeRecord('people-role', rawHash, '#/people/role/' + encodePathSegment(role), { role, knownRole })
    }
    if (segs[1] === 'groups' && segs.length === 2) {
      return routeRecord('people-groups', rawHash, '#/people/groups', {})
    }
    if (segs[1] === 'groups' && segs.length === 3) {
      const groupId = idSegment(segs[2])
      if (groupId == null) return unknownRoute(rawHash)
      return routeRecord('person-group-detail', rawHash, '#/people/groups/' + encodePathSegment(groupId), {
        groupId,
      })
    }
    if (segs.length === 2 && segs[1] !== 'role' && segs[1] !== 'groups') {
      const personId = idSegment(segs[1])
      if (personId == null) return unknownRoute(rawHash)
      return routeRecord('person', rawHash, '#/people/' + encodePathSegment(personId), { personId })
    }
  }

  return unknownRoute(rawHash)
}

/** True for every parsed route except `unknown`. */
export function isRecognizedRoute(route: { readonly name?: unknown } | null | undefined): boolean {
  return !!(route && route.name && route.name !== 'unknown')
}
