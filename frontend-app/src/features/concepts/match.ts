import type { ConceptIndexItem } from './types'

/** Normalize a Concept index search query (lowercase, trimmed). */
export function normalizeConceptSearchQuery(query: string | null | undefined): string {
  return String(query == null ? '' : query)
    .trim()
    .toLowerCase()
}

/** Client-side match over an already-loaded effective index row. No network. */
export function matchConceptIndexItem(item: ConceptIndexItem, query: string): boolean {
  const q = normalizeConceptSearchQuery(query)
  if (!q) return true
  if (String(item.name || '').toLowerCase().includes(q)) return true
  for (const alias of item.aliases) {
    if (String(alias || '').toLowerCase().includes(q)) return true
  }
  for (const parent of item.parents) {
    if (String(parent.name || '').toLowerCase().includes(q)) return true
  }
  return false
}

export function filterConceptIndexItems(
  items: readonly ConceptIndexItem[],
  query: string | null | undefined,
): ConceptIndexItem[] {
  const q = normalizeConceptSearchQuery(query)
  if (!q) return items.slice()
  return items.filter((item) => matchConceptIndexItem(item, q))
}
