import fc from 'fast-check'
import { describe, expect, it } from 'vitest'
import {
  MAX_PROVIDER_ID_CHARS,
  resolveWorkVideoEmbed,
  workVideoSourceFromWork,
  YOUTUBE_HOSTS,
  type WorkVideoSource,
} from './video-embed'

const EMBED = /^https:\/\/www\.youtube\.com\/embed\/[A-Za-z0-9_-]{1,512}$/
const ID = 'abcdefghijk'

function source(over: Partial<WorkVideoSource> = {}): WorkVideoSource {
  return {
    provider: 'youtube',
    providerId: '',
    sourceUrl: '',
    ...over,
  }
}

function expectEmbed(result: ReturnType<typeof resolveWorkVideoEmbed>, id: string) {
  expect(result).toEqual({ state: 'embed', src: `https://www.youtube.com/embed/${id}` })
  if (result.state === 'embed') {
    const url = new URL(result.src)
    expect(url.protocol).toBe('https:')
    expect(url.hostname).toBe('www.youtube.com')
    expect(url.pathname).toBe(`/embed/${id}`)
    expect(url.search).toBe('')
    expect(url.hash).toBe('')
    expect(url.username).toBe('')
    expect(url.password).toBe('')
  }
}

describe('resolveWorkVideoEmbed', () => {
  it('embeds a provider id and ignores the URL', () => {
    expectEmbed(
      resolveWorkVideoEmbed(source({
        providerId: ID,
        sourceUrl: 'javascript:alert(1)',
      })),
      ID,
    )
  })

  it('embeds the recognized YouTube URL forms when no provider id is stored', () => {
    const urls = [
      `https://www.youtube.com/watch?v=${ID}`,
      `https://youtube.com/watch?v=${ID}`,
      `https://m.youtube.com/watch?v=${ID}`,
      `https://youtu.be/${ID}`,
      `http://www.youtube.com/embed/${ID}`,
      `https://www.youtube.com/embed/${ID}?autoplay=1`,
      `https://user:pass@www.youtube.com/watch?v=${ID}#t=12`,
    ]
    for (const sourceUrl of urls) {
      expectEmbed(resolveWorkVideoEmbed(source({ provider: '', sourceUrl })), ID)
    }
  })

  it('treats a blank provider as youtube and trims a stored id', () => {
    expectEmbed(
      resolveWorkVideoEmbed(source({ provider: '  ', providerId: `  ${ID}  ` })),
      ID,
    )
  })

  it('does not embed another provider even when the URL is YouTube', () => {
    const result = resolveWorkVideoEmbed(source({
      provider: 'vimeo',
      providerId: ID,
      sourceUrl: `https://www.youtube.com/watch?v=${ID}`,
    }))
    expect(result.state).toBe('link')
    if (result.state === 'link') {
      expect(result.href).toBe(`https://www.youtube.com/watch?v=${ID}`)
    }
  })

  it('keeps a bad provider id from selecting a different video in the URL', () => {
    const result = resolveWorkVideoEmbed(source({
      providerId: `${ID}/../../evil`,
      sourceUrl: 'https://www.youtube.com/watch?v=zzzzzzzzzzz',
    }))
    expect(result.state).toBe('link')
    if (result.state === 'link') {
      expect(new URL(result.href).hostname).toBe('www.youtube.com')
      expect(result.href).not.toContain('/embed/')
    }
  })

  it('refuses hosts that only look like YouTube', () => {
    for (const sourceUrl of [
      'https://notyoutube.com/watch?v=abcdefghijk',
      'https://youtube.com.example.org/watch?v=abcdefghijk',
      'https://evil.com/watch?v=abcdefghijk',
      'https://www.youtube.com.evil.com/watch?v=abcdefghijk',
    ]) {
      const result = resolveWorkVideoEmbed(source({ sourceUrl }))
      expect(result.state).toBe('link')
      if (result.state === 'link') {
        expect(result.href.startsWith('https://www.youtube.com/embed/')).toBe(false)
      }
    }
  })

  it('drops schemes that are not http or https', () => {
    for (const sourceUrl of [
      'javascript:alert(1)',
      'data:text/html,<script>alert(1)</script>',
      'blob:https://www.youtube.com/abcdefghijk',
      'ftp://www.youtube.com/watch?v=abcdefghijk',
      'file:///etc/passwd',
      'vbscript:msgbox(1)',
    ]) {
      expect(resolveWorkVideoEmbed(source({ sourceUrl }))).toEqual({ state: 'unavailable' })
    }
  })

  it('rejects ids outside the identifier alphabet and the length bound', () => {
    const hostile = [
      'abc def',
      'abc"onload="alert(1)',
      "abc'><script>",
      'abc/def',
      'abc?autoplay=1',
      'abc&enablejsapi=1',
      'abc%2Fdef',
      'abc\ndef',
      'abc\u0000def',
      '../watch',
      'A'.repeat(MAX_PROVIDER_ID_CHARS + 1),
    ]
    for (const providerId of hostile) {
      const result = resolveWorkVideoEmbed(source({
        providerId,
        sourceUrl: 'javascript:alert(document.domain)',
      }))
      expect(result).toEqual({ state: 'unavailable' })
    }
    expectEmbed(
      resolveWorkVideoEmbed(source({ providerId: 'A'.repeat(MAX_PROVIDER_ID_CHARS) })),
      'A'.repeat(MAX_PROVIDER_ID_CHARS),
    )
  })

  it('links a plain http URL and names no player', () => {
    const result = resolveWorkVideoEmbed(source({ sourceUrl: 'https://example.test/talk' }))
    expect(result).toEqual({ state: 'link', href: 'https://example.test/talk' })
  })

  it('is unavailable when there is nothing safe to open', () => {
    expect(resolveWorkVideoEmbed(source())).toEqual({ state: 'unavailable' })
    expect(resolveWorkVideoEmbed(source({ sourceUrl: 'not a url' }))).toEqual({ state: 'unavailable' })
  })

  it('reads only string source fields off a Work', () => {
    expect(workVideoSourceFromWork({
      provider: 'youtube',
      provider_id: ` ${ID} `,
      source_url: ' https://youtu.be/abcdefghijk ',
    })).toEqual({
      provider: 'youtube',
      providerId: ID,
      sourceUrl: 'https://youtu.be/abcdefghijk',
    })
    expect(workVideoSourceFromWork({
      provider: 1,
      provider_id: { id: ID },
      source_url: null,
    })).toEqual({ provider: '', providerId: '', sourceUrl: '' })
    expect(workVideoSourceFromWork(null)).toEqual({ provider: '', providerId: '', sourceUrl: '' })
  })

  it('keeps the recognized host list explicit', () => {
    expect(YOUTUBE_HOSTS).toEqual([
      'youtube.com',
      'www.youtube.com',
      'm.youtube.com',
      'youtu.be',
    ])
  })

  it('never emits an embed or link outside the safe shapes', () => {
    fc.assert(
      fc.property(fc.string(), fc.string(), fc.string(), (provider, providerId, sourceUrl) => {
        const result = resolveWorkVideoEmbed({ provider, providerId, sourceUrl })
        if (result.state === 'embed') {
          expect(result.src).toMatch(EMBED)
          const url = new URL(result.src)
          expect(url.search).toBe('')
          expect(url.hash).toBe('')
          expect(url.username).toBe('')
        }
        if (result.state === 'link') {
          const url = new URL(result.href)
          expect(url.protocol === 'http:' || url.protocol === 'https:').toBe(true)
        }
      }),
      { numRuns: 80 },
    )
  })
})
