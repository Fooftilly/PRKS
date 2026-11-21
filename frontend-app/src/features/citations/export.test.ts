import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import citeprocSource from '../../../../frontend/vendor/citeproc/citeproc.js?raw'
import mlaStyle from '../../../../frontend/vendor/csl/modern-language-association.csl?raw'
import enUsLocale from '../../../../frontend/vendor/csl/locales-en-US.xml?raw'
import { setCiteprocLoaderForTests, type CiteprocModule } from './engine'
import { CitationExportError, exportCitations } from './export'
import { citationsBridge } from './session'

const citeproc = new Function(`${citeprocSource}\nreturn CSL;`)() as CiteprocModule
const ITEM = { id: 'W-1', type: 'book', title: 'A Book', author: [{ family: 'Byron', given: 'Ada' }], issued: { 'date-parts': [[1999]] } }

let answer: unknown
let copied: string[]
let downloads: { name: string; type: string; text: string }[]

beforeEach(() => {
  setCiteprocLoaderForTests(() => Promise.resolve(citeproc))
  copied = []
  downloads = []
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    if (path === '/api/citations') return new Response(JSON.stringify(answer))
    if (path === '/vendor/csl/modern-language-association.csl') return new Response(mlaStyle)
    if (path === '/vendor/csl/locales-en-US.xml') return new Response(enUsLocale)
    return new Response('', { status: 404 })
  }))
  vi.stubGlobal('prksCopyTextToClipboard', async (text: string) => {
    copied.push(text)
  })
  vi.stubGlobal('ClipboardItem', undefined)
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:prks-test')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push({ name: this.download, type: '', text: this.href })
  })
})

afterEach(() => {
  setCiteprocLoaderForTests(null)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete (globalThis as { __prksCitationStyle?: string }).__prksCitationStyle
})

describe('exportCitations', () => {
  it('copies a bibliography in the Library default style as plain text when rich copy is unavailable', async () => {
    ;(globalThis as { __prksCitationStyle?: string }).__prksCitationStyle = 'modern-language-association'
    answer = { format: 'csl-json', items: [ITEM], missing: ['W-2'] }
    await expect(exportCitations(['W-1', 'W-2'], 'formatted')).resolves.toEqual({ cited: 1, missing: 1 })
    expect(copied).toEqual(['Byron, Ada. A Book. 1999.'])
  })

  it('copies BibTeX and downloads .bib and CSL-JSON files', async () => {
    answer = { format: 'bibtex', text: '@book{a}\n\n@book{b}', missing: [] }
    await expect(exportCitations(['W-1', 'W-1', 'W-2'], 'bibtex')).resolves.toEqual({ cited: 2, missing: 0 })
    expect(copied).toEqual(['@book{a}\n\n@book{b}'])
    await exportCitations(['W-1'], 'bib-file')
    answer = { format: 'csl-json', items: [ITEM], missing: [] }
    await exportCitations(['W-1'], 'csl-json-file')
    expect(downloads.map((download) => download.name)).toEqual(['prks-citations.bib', 'prks-citations.json'])
  })

  it('says so when none of the selected Works exist any more', async () => {
    answer = { format: 'csl-json', items: [], missing: ['W-1'] }
    await expect(exportCitations(['W-1'], 'formatted')).rejects.toBeInstanceOf(CitationExportError)
    answer = { format: 'bibtex', text: '', missing: ['W-1'] }
    await expect(exportCitations(['W-1'], 'bib-file')).rejects.toThrow('None of the selected files exist any more.')
  })

  it('the bridge refuses an unknown kind and labels the Library style', async () => {
    const bridge = citationsBridge()
    await expect(bridge.exportSelection(['W-1'], 'ris' as never)).rejects.toThrow('Unknown citation export.')
    expect(bridge.libraryStyleLabel()).toBe('APA (7th edition)')
  })
})
