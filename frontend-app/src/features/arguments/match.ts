import type { ArgumentIndexItem, ArgumentKind, ArgumentKindFilter } from './types'

/** Normalize an Argument index search query (lowercase, trimmed). */
export function normalizeArgumentSearchQuery(query: string | null | undefined): string {
  return String(query == null ? '' : query)
    .trim()
    .toLowerCase()
}

/**
 * Client-side match over an already-loaded effective index row.
 * Name and main text are the primary fields. Kind, target names, and source
 * titles stay searchable so the previous index behavior is preserved.
 * No network.
 */
export function matchArgumentIndexItem(item: ArgumentIndexItem, query: string): boolean {
  const q = normalizeArgumentSearchQuery(query)
  if (!q) return true
  if (String(item.name || '').toLowerCase().includes(q)) return true
  if (String(item.main_text || '').toLowerCase().includes(q)) return true
  if (String(item.kind || '').toLowerCase().includes(q)) return true
  for (const target of item.targets) {
    if (String(target.name || '').toLowerCase().includes(q)) return true
  }
  for (const source of item.sources) {
    if (String(source.work_title || '').toLowerCase().includes(q)) return true
  }
  return false
}

export function filterArgumentIndexItems(
  items: readonly ArgumentIndexItem[],
  query: string | null | undefined,
): ArgumentIndexItem[] {
  const q = normalizeArgumentSearchQuery(query)
  if (!q) return items.slice()
  return items.filter((item) => matchArgumentIndexItem(item, q))
}

export function normalizeArgumentKindFilter(kind: string | null | undefined): ArgumentKindFilter {
  if (kind === 'argument' || kind === 'stance') return kind
  return 'all'
}

export function argumentKindUi(kind: string | null | undefined): {
  plural: string
  empty: string
  creationKinds: ArgumentKind[]
} {
  const filter = normalizeArgumentKindFilter(kind)
  if (filter === 'argument') {
    return { plural: 'Arguments', empty: 'No Arguments yet.', creationKinds: ['argument'] }
  }
  if (filter === 'stance') {
    return { plural: 'Stances', empty: 'No Stances yet.', creationKinds: ['stance'] }
  }
  return {
    plural: 'Arguments or Stances',
    empty: 'No Arguments or Stances yet.',
    creationKinds: ['argument', 'stance'],
  }
}

/** Canonical index hash. Kind filters are real routes, not local-only state. */
export function argumentIndexHash(kind: string | null | undefined): string {
  const filter = normalizeArgumentKindFilter(kind)
  if (filter === 'all') return '#/arguments'
  return `#/arguments?kind=${encodeURIComponent(filter)}`
}

/** New target rows default to supports for Arguments and holds for Stances. */
export function defaultArgumentVerdict(kind: string | null | undefined): string {
  return kind === 'stance' ? 'holds' : 'supports'
}
