/**
 * Relationship-strip parts. Plain text, or text with a same-app link.
 * A part never carries markup.
 */
export type RelSummaryPart =
  | string
  | { text: string; href?: string }
  | null
  | undefined
  | false

export interface RelSummaryItem {
  text: string
  href: string | null
}

/** Same-app hash routes and root-relative paths only. */
export function safeInternalHref(href: unknown): string | null {
  if (typeof href !== 'string') return null
  const h = href.trim()
  if (!h) return null
  const lower = h.toLowerCase()
  if (lower.includes('javascript:') || lower.includes('data:')) return null
  if (h.charAt(0) === '#') return h
  if (h.charAt(0) === '/' && h.charAt(1) !== '/') return h
  return null
}

export function relSummaryItems(parts: readonly RelSummaryPart[]): RelSummaryItem[] {
  const out: RelSummaryItem[] = []
  for (const part of parts) {
    if (part == null || part === false) continue
    if (typeof part === 'string') {
      const text = part.trim()
      if (text) out.push({ text, href: null })
      continue
    }
    const text = String(part.text || '').trim()
    if (text) out.push({ text, href: safeInternalHref(part.href) })
  }
  return out
}
