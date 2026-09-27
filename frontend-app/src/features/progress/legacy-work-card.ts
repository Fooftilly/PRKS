/**
 * Temporary compatibility boundary.
 *
 * `prksWorkCardHtml()` is the shared Work-card contract. Progress does not
 * own a Vue card, and it does not copy card markup, keyboard, or preview
 * behavior. Remove this mount when a shared Vue card replaces that helper.
 */
export interface ProgressBrowseRow {
  id?: unknown
  title?: unknown
  status?: unknown
  abstract_excerpt?: unknown
  abstract?: unknown
}

export interface ProgressWorkCardOptions {
  subtitle: string
  suppressThumbnail?: true
}

/** Cached Progress pages must not request thumbnails. Online pages keep them. */
export function progressWorkCardOptions(offlineCached: boolean, subtitle: string): ProgressWorkCardOptions {
  if (offlineCached) return { subtitle, suppressThumbnail: true }
  return { subtitle }
}

export function legacyWorkCardHtml(work: ProgressBrowseRow, offlineCached: boolean, subtitle: string): string {
  const fn = window.prksWorkCardHtml
  if (typeof fn !== 'function') return ''
  return fn(work, progressWorkCardOptions(offlineCached, subtitle))
}
