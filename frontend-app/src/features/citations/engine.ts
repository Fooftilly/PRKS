/**
 * Formatted citations from CSL-JSON items (Citation V1, #41).
 *
 * citeproc-js formats; PRKS loads it on demand from `/vendor/citeproc/`
 * (about 1 MB, so not part of the Vue bundle) with the chosen style and the
 * en-US locale from `/vendor/csl/`; an imported style's text comes from
 * `/api/citation-styles/:id`. Nothing here reaches outside PRKS.
 * Output is a bibliography: each entry as HTML (for a rich copy), as plain
 * text, and as text runs a component renders without `v-html`.
 */
import type { CslItem } from '../../api/citations'
import { readImportedStyle } from '../../api/citation-styles'
import { isImportedStyleId, isShippedStyle } from './styles'

const CITEPROC_SRC = '/vendor/citeproc/citeproc.js'
const LOCALE_SRC = '/vendor/csl/locales-en-US.xml'
const LOCALE = 'en-US'

interface CiteprocSys {
  retrieveLocale(lang: string): string
  retrieveItem(id: string): CslItem
}

interface CiteprocEngine {
  setOutputFormat(format: 'html' | 'text'): void
  updateItems(ids: string[]): void
  makeBibliography(): [unknown, string[]] | false
}

export interface CiteprocModule {
  Engine: new (sys: CiteprocSys, style: string, lang?: string) => CiteprocEngine
}

/** One stretch of an entry's text, italic or not (titles of books, journals, volumes). */
export interface CitationRun {
  readonly text: string
  readonly italic: boolean
}

export interface CitationEntry {
  readonly html: string
  readonly text: string
  readonly runs: readonly CitationRun[]
}

export interface FormattedBibliography {
  readonly entries: readonly CitationEntry[]
  /** The entries as one HTML fragment, for the clipboard's `text/html`. */
  readonly html: string
  /** The entries as plain text, one per line. */
  readonly text: string
}

export interface CitationResources {
  readonly csl: CiteprocModule
  readonly locale: string
  readonly style: string
}

type Loader = () => Promise<CiteprocModule>

let citeproc: Promise<CiteprocModule> | null = null
let locale: Promise<string> | null = null
const styles = new Map<string, Promise<string>>()
let loadCiteproc: Loader = loadCiteprocScript

function moduleFromGlobal(): CiteprocModule | null {
  const csl = (globalThis as { CSL?: unknown }).CSL
  if (csl && typeof (csl as CiteprocModule).Engine === 'function') return csl as CiteprocModule
  return null
}

function loadCiteprocScript(): Promise<CiteprocModule> {
  const ready = moduleFromGlobal()
  if (ready) return Promise.resolve(ready)
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = CITEPROC_SRC
    script.async = true
    script.onload = () => {
      const loaded = moduleFromGlobal()
      if (loaded) resolve(loaded)
      else reject(new Error('The citation formatter did not load.'))
    }
    script.onerror = () => reject(new Error('The citation formatter could not be loaded.'))
    document.head.appendChild(script)
  })
}

async function fetchText(path: string): Promise<string> {
  const response = await fetch(path, { credentials: 'same-origin' })
  if (!response.ok) throw new Error('A citation style file could not be loaded.')
  return response.text()
}

/** A cached load that is forgotten when it fails, so the next attempt retries. */
function remember<T>(load: () => Promise<T>, forget: () => void): Promise<T> {
  const pending = load()
  pending.catch(forget)
  return pending
}

/** citeproc-js, the locale and one style, each loaded once per page. */
export async function loadCitationResources(styleId: string): Promise<CitationResources> {
  if (!isShippedStyle(styleId) && !isImportedStyleId(styleId)) throw new Error('Unknown citation style.')
  citeproc ??= remember(() => loadCiteproc(), () => { citeproc = null })
  locale ??= remember(() => fetchText(LOCALE_SRC), () => { locale = null })
  let style = styles.get(styleId)
  if (!style) {
    const read = isShippedStyle(styleId)
      ? () => fetchText(`/vendor/csl/${styleId}.csl`)
      : async () => (await readImportedStyle(styleId)).csl
    style = remember(read, () => styles.delete(styleId))
    styles.set(styleId, style)
  }
  const [csl, localeText, styleText] = await Promise.all([citeproc, locale, style])
  return { csl, locale: localeText, style: styleText }
}

/** Text runs of one citeproc HTML entry; only italics carry over (`<i>`, `<em>`). */
export function entryRuns(html: string): CitationRun[] {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const runs: CitationRun[] = []
  const walk = (node: Node, italic: boolean): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent ?? ''
      if (!text) return
      const last = runs[runs.length - 1]
      if (last && last.italic === italic) runs[runs.length - 1] = { text: last.text + text, italic }
      else runs.push({ text, italic })
      return
    }
    const tag = node.nodeType === Node.ELEMENT_NODE ? (node as Element).tagName : ''
    const inner = italic || tag === 'I' || tag === 'EM'
    node.childNodes.forEach((child) => walk(child, inner))
    // A numbered style puts the number and the reference in sibling blocks.
    if (tag === 'DIV') walk(doc.createTextNode(' '), false)
  }
  walk(doc.body, false)
  const trimmed = runs
    .map((run) => ({ ...run, text: run.text.replace(/\s+/g, ' ') }))
    .filter((run, index, all) => !(run.text === ' ' && (index === 0 || all[index - 1].text.endsWith(' '))))
  if (trimmed.length) {
    trimmed[0] = { ...trimmed[0], text: trimmed[0].text.trimStart() }
    const end = trimmed.length - 1
    trimmed[end] = { ...trimmed[end], text: trimmed[end].text.trimEnd() }
  }
  return trimmed.filter((run) => run.text)
}

/** The items as a bibliography in the style, in the style's order. */
export function formatBibliography(items: readonly CslItem[], resources: CitationResources): FormattedBibliography {
  if (!items.length) return { entries: [], html: '', text: '' }
  const byId = new Map(items.map((item) => [item.id, item]))
  const sys: CiteprocSys = {
    retrieveLocale: () => resources.locale,
    retrieveItem: (id) => {
      const item = byId.get(id)
      if (!item) throw new Error('Unknown citation item.')
      return item
    },
  }
  const ids = items.map((item) => item.id)
  const render = (format: 'html' | 'text'): string[] => {
    const engine = new resources.csl.Engine(sys, resources.style, LOCALE)
    engine.setOutputFormat(format)
    engine.updateItems(ids)
    const result = engine.makeBibliography()
    return result ? result[1] : []
  }
  const html = render('html')
  const text = render('text')
  const entries = html.map((entry, index) => ({
    html: entry.trim(),
    text: (text[index] ?? '').trim(),
    runs: entryRuns(entry),
  }))
  return {
    entries,
    html: entries.map((entry) => entry.html).join('\n'),
    text: entries.map((entry) => entry.text).join('\n'),
  }
}

/** Load what the style needs, then format. */
export async function formatCitations(items: readonly CslItem[], styleId: string): Promise<FormattedBibliography> {
  return formatBibliography(items, await loadCitationResources(styleId))
}

/** Tests replace the script loader (jsdom does not run `<script src>`). */
export function setCiteprocLoaderForTests(loader: Loader | null): void {
  loadCiteproc = loader ?? loadCiteprocScript
  citeproc = null
  locale = null
  styles.clear()
}
