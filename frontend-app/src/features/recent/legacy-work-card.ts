import type { RecentRow } from './projection'

/**
 * Temporary compatibility boundary.
 *
 * `prksWorkCardHtml()` is the shared Work-card contract. Recent does not own
 * a Vue card. Remove this mount in #303 B4 when a shared Vue Work card
 * replaces that helper.
 */
export function recentWorkCardHtml(work: RecentRow, offlineCached: boolean, subtitle: string): string {
  const fn = window.prksWorkCardHtml
  if (typeof fn !== 'function') return ''
  if (offlineCached) return fn(work, { subtitle, suppressThumbnail: true })
  return fn(work, { subtitle })
}
