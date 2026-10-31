/**
 * Pure Work video source → embed decision.
 *
 * The Vue player pane is the only caller. Identity still comes from the
 * effective Work (`provider`, `provider_id`, `source_url`); this module only
 * decides what may be placed in an iframe or an external link.
 *
 * Hosts and the provider-id alphabet match `backend/work_source_sync.py` and
 * `frontend/js/work-source-state.js`. A present `provider_id` short-circuits
 * the URL: an identifier that is not well-formed does not fall through to a
 * different video, and it is never copied into an iframe src.
 */

export const YOUTUBE_HOSTS: readonly string[] = [
  'youtube.com',
  'www.youtube.com',
  'm.youtube.com',
  'youtu.be',
]

/** Matches `work_source_sync.MAX_PROVIDER_ID_CHARS` and its alphabet. */
export const MAX_PROVIDER_ID_CHARS = 512

const PROVIDER_ID_RE = /^[A-Za-z0-9_-]{1,512}$/
const YOUTUBE_EMBED_ORIGIN = 'https://www.youtube.com'
const HOSTS = new Set(YOUTUBE_HOSTS)

export const VIDEO_IFRAME_ALLOW =
  'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share'

export const VIDEO_IFRAME_TITLE = 'Video player'

export interface WorkVideoSource {
  readonly provider: string
  readonly providerId: string
  readonly sourceUrl: string
}

export type WorkVideoPresentation =
  | { readonly state: 'embed'; readonly src: string }
  | { readonly state: 'link'; readonly href: string }
  | { readonly state: 'unavailable' }

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** Source fields the shell may hand the player. Non-strings are blank. */
export function workVideoSourceFromWork(
  work: Record<string, unknown> | null | undefined,
): WorkVideoSource {
  return {
    provider: asText(work?.provider),
    providerId: asText(work?.provider_id),
    sourceUrl: asText(work?.source_url),
  }
}

function isProviderId(value: string): boolean {
  return PROVIDER_ID_RE.test(value)
}

function youtubeIdFromUrl(sourceUrl: string): string {
  let parsed: URL
  try {
    parsed = new URL(sourceUrl)
  } catch {
    return ''
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return ''
  const host = parsed.hostname.toLowerCase()
  if (!HOSTS.has(host)) return ''
  const bounded = (id: string) => (isProviderId(id) ? id : '')
  if (host === 'youtu.be') {
    return bounded((parsed.pathname.replace(/^\//, '').split('/')[0] || '').trim())
  }
  const fromQuery = (parsed.searchParams.get('v') || '').trim()
  if (fromQuery) return bounded(fromQuery)
  const parts = parsed.pathname.replace(/^\//, '').split('/')
  if (parts[0] === 'embed' && parts[1]) return bounded(parts[1].trim())
  return ''
}

/** An embed src we built, or '' when the id cannot stay on that exact URL. */
function embedSrc(id: string): string {
  if (!isProviderId(id)) return ''
  const encoded = encodeURIComponent(id)
  if (encoded !== id) return ''
  const src = `${YOUTUBE_EMBED_ORIGIN}/embed/${encoded}`
  let parsed: URL
  try {
    parsed = new URL(src)
  } catch {
    return ''
  }
  if (parsed.protocol !== 'https:') return ''
  if (parsed.origin !== YOUTUBE_EMBED_ORIGIN) return ''
  if (parsed.hostname !== 'www.youtube.com') return ''
  if (parsed.pathname !== `/embed/${id}`) return ''
  if (parsed.search || parsed.hash || parsed.username || parsed.password) return ''
  return src
}

/** http(s) only. `javascript:`, `data:`, and other schemes are not links. */
function safeLink(raw: string): string {
  const text = raw.trim()
  if (!text || /[\u0000-\u001F\u007F]/.test(text)) return ''
  let parsed: URL
  try {
    parsed = new URL(text)
  } catch {
    return ''
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return ''
  return parsed.href
}

/**
 * Supported YouTube identity becomes one `https://www.youtube.com/embed/`
 * URL. Anything else is an external http(s) link, or unavailable.
 */
export function resolveWorkVideoEmbed(source: WorkVideoSource): WorkVideoPresentation {
  const provider = asText(source.provider) || 'youtube'
  const providerId = asText(source.providerId)
  const sourceUrl = asText(source.sourceUrl)
  let id = ''
  if (provider === 'youtube') {
    if (providerId) {
      id = isProviderId(providerId) ? providerId : ''
    } else {
      id = youtubeIdFromUrl(sourceUrl)
    }
  }
  if (id) {
    const src = embedSrc(id)
    if (src) return { state: 'embed', src }
  }
  const href = safeLink(sourceUrl)
  if (href) return { state: 'link', href }
  return { state: 'unavailable' }
}
