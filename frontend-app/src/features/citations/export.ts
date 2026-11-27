/**
 * Copy and download citations (Citation V1, #41).
 *
 * A formatted copy puts HTML (italic titles survive a paste into a word
 * processor) and plain text on the clipboard; where the browser cannot write
 * HTML it falls back to the shared plain-text copy. Downloads are made in the
 * page from a Blob: nothing is uploaded or fetched from elsewhere.
 */
import { CITATION_EXPORT_MAX, fetchBibtex, fetchCslItems } from '../../api/citations'
import { PrksApiError } from '../../api/http'
import { settleBibtexProfile } from './bibtex-profile'
import { formatCitations } from './engine'
import { libraryCitationStyle } from './styles'

export type CitationExportKind = 'formatted' | 'bibtex' | 'bib-file' | 'csl-json-file'

export interface CitationExportResult {
  /** How many Works were cited. */
  readonly cited: number
  /** How many selected Works no longer exist. */
  readonly missing: number
}

export class CitationExportError extends Error {
  override name = 'CitationExportError'
}

async function copyPlainText(text: string): Promise<void> {
  const copy = (globalThis as { prksCopyTextToClipboard?: (text: string) => Promise<void> }).prksCopyTextToClipboard
  if (typeof copy === 'function') {
    await copy(text)
    return
  }
  await navigator.clipboard.writeText(text)
}

/** Rich copy where the browser supports it, else plain text. */
export async function copyRichText(html: string, text: string): Promise<void> {
  const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
  if (clipboard && typeof clipboard.write === 'function' && typeof ClipboardItem === 'function') {
    try {
      await clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        }),
      ])
      return
    } catch {
      /* Permission or format refused: the plain-text path below still copies. */
    }
  }
  await copyPlainText(text)
}

export function downloadText(fileName: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.hidden = true
  document.body.appendChild(link)
  try {
    link.click()
  } finally {
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

function nothingLeft(): CitationExportError {
  return new CitationExportError('None of the selected files exist any more.')
}

/** A refusal PRKS explained (a 400, such as a selection over the limit), told as its own message. */
async function explained<T>(read: () => Promise<T>): Promise<T> {
  try {
    return await read()
  } catch (error) {
    if (error instanceof PrksApiError && error.status === 400 && error.message) {
      throw new CitationExportError(error.message)
    }
    throw error
  }
}

/** Copy or download the Works' citations; formatted copies use the Library's default style. */
export async function exportCitations(
  workIds: readonly string[],
  kind: CitationExportKind,
  styleId: string = libraryCitationStyle(),
): Promise<CitationExportResult> {
  if (new Set(workIds).size > CITATION_EXPORT_MAX) {
    throw new CitationExportError(`Select at most ${CITATION_EXPORT_MAX} files to cite at once.`)
  }
  if (kind === 'bibtex' || kind === 'bib-file') {
    if (!await settleBibtexProfile()) {
      throw new CitationExportError('The BibTeX export settings could not be saved. Try again when PRKS is reachable.')
    }
    const { text, missing } = await explained(() => fetchBibtex(workIds))
    if (!text.trim()) throw nothingLeft()
    if (kind === 'bibtex') await copyPlainText(text)
    else downloadText('prks-citations.bib', text + '\n', 'application/x-bibtex')
    return { cited: new Set(workIds).size - missing.length, missing: missing.length }
  }
  const { items, missing } = await explained(() => fetchCslItems(workIds))
  if (!items.length) throw nothingLeft()
  if (kind === 'csl-json-file') {
    downloadText('prks-citations.json', JSON.stringify(items, null, 2) + '\n', 'application/vnd.citationstyles.csl+json')
  } else {
    const formatted = await formatCitations(items, styleId)
    await copyRichText(formatted.html, formatted.text)
  }
  return { cited: items.length, missing: missing.length }
}
