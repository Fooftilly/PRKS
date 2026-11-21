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
})
