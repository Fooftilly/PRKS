import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import citeprocSource from '../../../../frontend/vendor/citeproc/citeproc.js?raw'
import apaStyle from '../../../../frontend/vendor/csl/apa.csl?raw'
import ieeeStyle from '../../../../frontend/vendor/csl/ieee.csl?raw'
import enUsLocale from '../../../../frontend/vendor/csl/locales-en-US.xml?raw'
import type { CslItem } from '../../api/citations'
import { entryRuns, formatCitations, setCiteprocLoaderForTests, type CiteprocModule } from './engine'

const citeproc = new Function(`${citeprocSource}\nreturn CSL;`)() as CiteprocModule

const FILES: Record<string, string> = {
  '/vendor/csl/apa.csl': apaStyle,
  '/vendor/csl/ieee.csl': ieeeStyle,
  '/vendor/csl/locales-en-US.xml': enUsLocale,
}

const article: CslItem = {
  id: 'W-1',
  type: 'article-journal',
  title: 'An Article',
  author: [{ family: 'Doe', given: 'Jane' }, { family: 'Roe', given: 'John' }],
  issued: { 'date-parts': [[2020, 5, 2]] },
  'container-title': 'Journal',
  volume: '3',
  page: '1-9',
  DOI: '10.1/x',
}
const book: CslItem = {
  id: 'W-2',
  type: 'book',
  title: 'A Book',
  author: [{ family: 'Byron', given: 'Ada' }],
  issued: { 'date-parts': [[1999]] },
  publisher: 'Press',
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  setCiteprocLoaderForTests(() => Promise.resolve(citeproc))
  fetchMock = vi.fn(async (path: string) =>
    path in FILES ? new Response(FILES[path], { status: 200 }) : new Response('', { status: 404 }),
  )
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  setCiteprocLoaderForTests(null)
  vi.unstubAllGlobals()
})

describe('formatCitations', () => {
  it('formats a bibliography in APA order with italics kept as runs', async () => {
    const bibliography = await formatCitations([article, book], 'apa')
    expect(bibliography.entries.map((entry) => entry.text)).toEqual([
      'Byron, A. (1999). A Book. Press.',
      'Doe, J., & Roe, J. (2020). An Article. Journal, 3, 1–9. https://doi.org/10.1/x',
    ])
    expect(bibliography.text).toBe(bibliography.entries.map((entry) => entry.text).join('\n'))
    expect(bibliography.entries[0].runs).toEqual([
      { text: 'Byron, A. (1999). ', italic: false },
      { text: 'A Book', italic: true },
      { text: '. Press.', italic: false },
    ])
    expect(bibliography.html).toContain('<i>A Book</i>')
  })

  it('loads each style and the locale once and keeps a numbered entry readable', async () => {
    const first = await formatCitations([article], 'ieee')
    await formatCitations([book], 'ieee')
    expect(first.entries[0].runs.map((run) => run.text).join('')).toMatch(/^\[1\] J\. Doe and J\. Roe, /)
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/vendor/csl/locales-en-US.xml', '/vendor/csl/ieee.csl'])
  })

  it('refuses a style PRKS does not ship and retries a failed load', async () => {
    await expect(formatCitations([article], 'harvard1')).rejects.toThrow('Unknown citation style.')
    fetchMock.mockImplementationOnce(async () => new Response('', { status: 503 }))
    await expect(formatCitations([article], 'apa')).rejects.toThrow()
    await expect(formatCitations([article], 'apa')).resolves.toMatchObject({ entries: [{}] })
  })

  it('prints a year before the common era and a range of years', async () => {
    const ancient: CslItem = { id: 'W-3', type: 'book', title: 'Republic', author: [{ family: 'Plato' }], issued: { 'date-parts': [[-375]] } }
    const span: CslItem = { id: 'W-4', type: 'book', title: 'Diaries', author: [{ family: 'Woolf' }], issued: { 'date-parts': [[1915], [1941]] } }
    const { entries } = await formatCitations([ancient, span], 'apa')
    const texts = entries.map((entry) => entry.text)
    expect(texts.find((text) => text.includes('Republic'))).toMatch(/375 B\.C\.E\./)
    expect(texts.find((text) => text.includes('Diaries'))).toMatch(/1915.1941/)
  })

  it('formats with an imported style read from PRKS', async () => {
    const id = `custom-${'b'.repeat(32)}`
    FILES[`/api/citation-styles/${id}`] = JSON.stringify({ id, title: 'House IEEE', csl: ieeeStyle })
    try {
      const { entries } = await formatCitations([article], id)
      expect(entries[0].text).toMatch(/^\[1\] J\. Doe and J\. Roe, /)
      expect(fetchMock.mock.calls.map(([path]) => path)).toContain(`/api/citation-styles/${id}`)
    } finally {
      delete FILES[`/api/citation-styles/${id}`]
    }
  })

  it('answers an empty bibliography for no items', async () => {
    await expect(formatCitations([], 'apa')).resolves.toEqual({ entries: [], html: '', text: '' })
  })
})

describe('entryRuns', () => {
  it('keeps only text, never markup', () => {
    expect(entryRuns('<div class="csl-entry">A <i>B</i> <b>C</b> &#38; <script>x</script></div>')).toEqual([
      { text: 'A ', italic: false },
      { text: 'B', italic: true },
      { text: ' C & x', italic: false },
    ])
  })
})
