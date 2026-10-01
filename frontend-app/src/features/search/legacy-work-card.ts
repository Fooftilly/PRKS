import type { SearchResultRow } from './types'

/**
 * The server's own excerpt rule, in code points, so a pending Abstract is cut
 * where the acknowledged one will be.
 */
export function searchResultSubtitle(work: SearchResultRow): string {
  if (!work.abstract) return ''
  const excerpt = window.prksAbstractExcerpt
  const cut = typeof excerpt === 'function' ? excerpt(work.abstract) : String(work.abstract).substring(0, 100)
  return `${cut}…`
}

/**
 * Temporary compatibility boundary.
 *
 * `prksWorkCardHtml()` is the shared Work-card contract. Search results do not
 * own a Vue card. Remove this mount in #303 B4 when a shared Vue Work card
 * replaces that helper.
 */
export function searchResultCardHtml(work: SearchResultRow): string {
  const fn = window.prksWorkCardHtml
  if (typeof fn !== 'function') return ''
  return fn(work, { subtitle: searchResultSubtitle(work) })
}
