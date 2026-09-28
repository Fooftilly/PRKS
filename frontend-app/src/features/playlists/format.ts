/** Display helpers copied from the legacy playlist renderer. No durable reads. */

export function formatPlaylistPublishedDate(raw: unknown): string {
  const s = String(raw || '').trim()
  if (!s) return ''
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (!iso) return s
  return `${iso[3]}/${iso[2]}/${iso[1]}`
}

export function playlistWorkSubtitle(work: {
  author_text?: unknown
  published_date?: unknown
}): string {
  const channel = String(work.author_text || '').trim()
  const published = formatPlaylistPublishedDate(work.published_date)
  if (channel && published) return `${channel} · ${published}`
  return channel || published || ''
}

export function playlistItemCountLabel(count: number): string {
  const n = Number.isFinite(count) ? count : 0
  return `${n} item${n === 1 ? '' : 's'}`
}
