import type { PositionIndexItem } from './types'

/** Normalize a Position index search query (lowercase, trimmed). */
export function normalizePositionSearchQuery(query: string | null | undefined): string {
  return String(query == null ? '' : query)
    .trim()
    .toLowerCase()
}

/** Client-side match over an already-loaded effective index row. No network. */
export function matchPositionIndexItem(item: PositionIndexItem, query: string): boolean {
  const q = normalizePositionSearchQuery(query)
  if (!q) return true
  if (String(item.name || '').toLowerCase().includes(q)) return true
  return String(item.description || '').toLowerCase().includes(q)
}

export function filterPositionIndexItems(
  items: readonly PositionIndexItem[],
  query: string | null | undefined,
): PositionIndexItem[] {
  const q = normalizePositionSearchQuery(query)
  if (!q) return items.slice()
  return items.filter((item) => matchPositionIndexItem(item, q))
}

/** Visible description excerpt. Matches the legacy 159-char clip plus ellipsis. */
export function positionDescriptionExcerpt(description: string | null | undefined): string {
  const excerpt = String(description || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (excerpt.length > 160) return `${excerpt.slice(0, 159).trim()}…`
  return excerpt
}
