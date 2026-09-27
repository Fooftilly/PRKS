/**
 * Narrow boundary to the shared research-markdown preview helper.
 * Vue must not invent a second markdown engine or unsanitized HTML path.
 * `emptyHtml` is the feature's own empty-state sentence.
 */
export function researchMarkdownHtml(
  text: string | null | undefined,
  emptyHtml: string,
): string {
  const raw = String(text || '')
  const fn = window.prksResearchMarkdownHtml
  if (typeof fn === 'function') return fn(raw)
  if (!raw.trim()) return emptyHtml
  const esc = window.prksEscapeHtml
  if (typeof esc === 'function') return `<p>${esc(raw)}</p>`
  return `<p>${raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')}</p>`
}
