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
