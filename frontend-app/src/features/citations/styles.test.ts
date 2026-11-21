import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CITATION_STYLE_EVENT,
  citationStyleLabel,
  effectiveCitationStyle,
  isCitationStyle,
  libraryCitationStyle,
  loadImportedStyles,
  resetImportedStylesForTests,
} from './styles'

const HOUSE = `custom-${'c'.repeat(32)}`
const GONE = `custom-${'d'.repeat(32)}`
const win = window as Window & { __prksCitationStyle?: string }
let listed: Array<{ id: string; title: string; created_at: string }>

function setLibraryStyle(style: string): void {
  win.__prksCitationStyle = style
  window.dispatchEvent(new CustomEvent(CITATION_STYLE_EVENT))
}

beforeEach(() => {
  resetImportedStylesForTests()
  listed = [{ id: HOUSE, title: 'House Style', created_at: 'x' }]
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ styles: listed }))))
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete win.__prksCitationStyle
})

describe('citation style registry', () => {
  it('uses an imported style once PRKS lists it, and the Library style otherwise', async () => {
    setLibraryStyle('ieee')
    expect(effectiveCitationStyle(HOUSE)).toBe('ieee')
    expect(citationStyleLabel(HOUSE)).toBe('Imported style')
    await loadImportedStyles()
    expect(isCitationStyle(HOUSE)).toBe(true)
    expect(effectiveCitationStyle(HOUSE)).toBe(HOUSE)
    expect(citationStyleLabel(HOUSE)).toBe('House Style')
    expect(effectiveCitationStyle(GONE)).toBe('ieee')
    expect(effectiveCitationStyle('harvard1')).toBe('ieee')
  })

  it('takes the Library style PRKS answered, until the list says it is gone', async () => {
    setLibraryStyle(GONE)
    expect(libraryCitationStyle()).toBe(GONE)
    await loadImportedStyles()
    expect(libraryCitationStyle()).toBe('apa')
    setLibraryStyle(HOUSE)
    expect(libraryCitationStyle()).toBe(HOUSE)
    listed = []
    await loadImportedStyles(true)
    expect(libraryCitationStyle()).toBe('apa')
    expect(effectiveCitationStyle('')).toBe('apa')
  })

  it('reads the list once and retries after a failure', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 503 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(loadImportedStyles()).rejects.toBeTruthy()
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ styles: listed })))
    await loadImportedStyles()
    await loadImportedStyles()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(isCitationStyle(HOUSE)).toBe(true)
  })

  it('keeps the latest read’s list when an earlier read answers after it', async () => {
    const answers: Array<(styles: typeof listed) => void> = []
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      answers.push((styles) => resolve(new Response(JSON.stringify({ styles }))))
    })))
    const earlier = loadImportedStyles(true)
    const latest = loadImportedStyles(true)
    await vi.waitFor(() => expect(answers).toHaveLength(2))
    const added = { id: GONE, title: 'Just Imported', created_at: 'x' }
    answers[1]?.([...listed, added])
    await latest
    answers[0]?.(listed)
    await earlier
    expect(isCitationStyle(GONE)).toBe(true)
    expect(citationStyleLabel(GONE)).toBe('Just Imported')
    // A later read without force reuses the latest read, not the earlier one.
    await loadImportedStyles()
    expect(isCitationStyle(GONE)).toBe(true)
  })

  it('fails an earlier read when the latest read fails, so the refusal is not hidden', async () => {
    const answers: Array<(ok: boolean) => void> = []
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      answers.push((ok) => resolve(ok
        ? new Response(JSON.stringify({ styles: listed }))
        : new Response(JSON.stringify({ error: 'down' }), { status: 503 })))
    })))
    const earlier = loadImportedStyles(true)
    const latest = loadImportedStyles(true)
    await vi.waitFor(() => expect(answers).toHaveLength(2))
    answers[1]?.(false)
    await expect(latest).rejects.toBeTruthy()
    answers[0]?.(true)
    await expect(earlier).rejects.toBeTruthy()
    expect(isCitationStyle(HOUSE)).toBe(false)
  })

  it('settles an earlier read that fails after the latest read succeeded as the latest one did', async () => {
    const answers: Array<(ok: boolean) => void> = []
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      answers.push((ok) => resolve(ok
        ? new Response(JSON.stringify({ styles: listed }))
        : new Response(JSON.stringify({ error: 'down' }), { status: 503 })))
    })))
    const earlier = loadImportedStyles(true)
    const latest = loadImportedStyles(true)
    await vi.waitFor(() => expect(answers).toHaveLength(2))
    answers[1]?.(true)
    await latest
    answers[0]?.(false)
    await expect(earlier).resolves.toBeUndefined()
    expect(isCitationStyle(HOUSE)).toBe(true)
  })
})
