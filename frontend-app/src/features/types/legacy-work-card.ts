import type { TypesBrowseRow } from './projection'

/**
 * Temporary compatibility boundary.
 *
 * `prksWorkCardHtml()` is the shared Work-card contract. Type detail does not
 * own a Vue card. The document-type badge is hidden because the page header
 * already names the type. Remove this mount in #303 B4 when a shared Vue Work
 * card replaces that helper.
 */
export function typeDetailWorkCardHtml(work: TypesBrowseRow, offlineCached: boolean): string {
  const fn = window.prksWorkCardHtml
  if (typeof fn !== 'function') return ''
  if (offlineCached) return fn(work, { hideDocTypeBadge: true, suppressThumbnail: true })
  return fn(work, { hideDocTypeBadge: true })
}
