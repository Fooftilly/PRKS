/**
 * The citation styles PRKS ships (Citation V1, #41), and the ones a Library imported.
 *
 * The shipped list mirrors `backend/domain/works/citation_styles.CITATION_STYLES`
 * and the classic `PRKS_CITATION_STYLE_IDS` (tests pin the three lists). Each
 * id is the Citation Style Language file name under `/vendor/csl/`.
 *
 * Imported styles (`/api/citation-styles`) are read once per page and again
 * after an import or a delete. A Work may name one that is gone, or one not
 * read yet: until the list says it exists, it is cited in the Library's style.
 */
import { ref } from 'vue'
import { IMPORTED_STYLE_ID, listImportedStyles, type ImportedStyleSummary } from '../../api/citation-styles'

export interface CitationStyle {
  readonly id: string
  readonly label: string
}

export const CITATION_STYLES: readonly CitationStyle[] = [
  { id: 'apa', label: 'APA (7th edition)' },
  { id: 'chicago-author-date', label: 'Chicago author-date (18th edition)' },
  { id: 'chicago-notes-bibliography', label: 'Chicago notes and bibliography (18th edition)' },
  { id: 'modern-language-association', label: 'MLA (9th edition)' },
  { id: 'harvard-cite-them-right', label: 'Harvard (Cite Them Right, 12th edition)' },
  { id: 'ieee', label: 'IEEE' },
  { id: 'nlm-citation-sequence', label: 'Vancouver (NLM)' },
]

export const DEFAULT_CITATION_STYLE = 'apa'

const STYLE_IDS = new Set(CITATION_STYLES.map((style) => style.id))

/** A style PRKS ships. */
export function isShippedStyle(id: unknown): id is string {
  return typeof id === 'string' && STYLE_IDS.has(id)
}

/** The shape of an imported style's id; it may name a style that is gone. */
export function isImportedStyleId(id: unknown): id is string {
  return typeof id === 'string' && IMPORTED_STYLE_ID.test(id)
}

/** The Library's imported styles, by title, as PRKS last listed them. */
export const importedStyles = ref<readonly CitationStyle[]>([])
/** Whether `importedStyles` has been read from PRKS on this page. */
const importedListed = ref(false)
let importedLoad: Promise<void> | null = null
/** Counts list reads, so only the latest one started sets the list. */
let importedGeneration = 0
/** The latest read started; an earlier read settles as it does. */
let latestRead: Promise<void> | null = null

function toStyle(summary: ImportedStyleSummary): CitationStyle {
  return { id: summary.id, label: summary.title }
}

/**
 * Read the imported styles once per page; `force` reads them again (after
 * an import or a delete). Only the latest read started sets the list: an
 * earlier one that answers last (say, the list Settings read on opening,
 * after an import's own read) must not put back the list before the import.
 * A superseded read settles as the latest one does.
 * A failed read leaves the last list and is retried on the next call.
 */
export function loadImportedStyles(force = false): Promise<void> {
  if (importedLoad && !force) return importedLoad
  const generation = ++importedGeneration
  const load: Promise<void> = listImportedStyles().then((styles) => {
    // Superseded: settle as the latest read does, so its failure is not hidden.
    if (generation !== importedGeneration) return latestRead ?? undefined
    importedStyles.value = styles.map(toStyle)
    importedListed.value = true
  })
  latestRead = load
  importedLoad = load
  load.catch(() => {
    if (importedLoad === load) importedLoad = null
  })
  return load
}

/** Tests start from no imported styles. */
export function resetImportedStylesForTests(): void {
  importedStyles.value = []
  importedListed.value = false
  importedLoad = null
  latestRead = null
  importedGeneration += 1
}

/** A style a citation can use now: shipped, or imported and still listed. */
export function isCitationStyle(id: unknown): id is string {
  return isShippedStyle(id) || (isImportedStyleId(id) && importedStyles.value.some((style) => style.id === id))
}

export function citationStyleLabel(id: string): string {
  return CITATION_STYLES.find((style) => style.id === id)?.label
    ?? importedStyles.value.find((style) => style.id === id)?.label
    ?? (isImportedStyleId(id) ? 'Imported style' : id)
}

/** Fired on `window` by the classic `prksSetCitationStyleCache` when the Library style changes. */
export const CITATION_STYLE_EVENT = 'prks:citation-style-change'

/**
 * Bumped on each change, so a `computed`, `watch` or template that reads
 * `libraryCitationStyle()` follows the Library style: a Settings change or
 * the first `/api/settings` read updates an already mounted preview.
 */
const libraryStyleVersion = ref(0)

if (typeof window !== 'undefined') {
  window.addEventListener(CITATION_STYLE_EVENT, () => {
    libraryStyleVersion.value += 1
  })
}

/**
 * The Library's default style, as the classic settings client last read it. Reactive.
 * PRKS answers an imported style only while it exists, so its id is taken as
 * is, unless the imported list read since then no longer has it.
 */
export function libraryCitationStyle(): string {
  void libraryStyleVersion.value
  const stored = (globalThis as { __prksCitationStyle?: unknown }).__prksCitationStyle
  if (isShippedStyle(stored)) return stored
  if (isImportedStyleId(stored) && (!importedListed.value || isCitationStyle(stored))) return stored
  return DEFAULT_CITATION_STYLE
}

/** A Work's own style when it is one a citation can use now, else the Library's default. */
export function effectiveCitationStyle(workStyle: unknown): string {
  return isCitationStyle(workStyle) ? workStyle : libraryCitationStyle()
}
