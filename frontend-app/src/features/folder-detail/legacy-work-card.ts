/**
 * Temporary compatibility boundary.
 *
 * `prksWorkCardHtml()` is the shared Work-card contract. Folder detail does
 * not own a Vue card. Remove this mount in #303 B4 when a shared Vue Work
 * card replaces that helper.
 */
export function folderDetailWorkCardHtml(work: unknown, offlineCached: boolean): string {
  const fn = window.prksWorkCardHtml
  if (typeof fn !== 'function') return ''
  const row = work as Parameters<typeof fn>[0]
  if (offlineCached) return fn(row, { suppressThumbnail: true })
  return fn(row, {})
}

export function folderDetailWorksHtml(works: readonly unknown[], offlineCached: boolean): string {
  return works.map((work) => folderDetailWorkCardHtml(work, offlineCached)).join('')
}
