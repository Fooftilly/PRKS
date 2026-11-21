/**
 * The citation styles PRKS ships (Citation V1, #41).
 *
 * Mirrors `backend/domain/works/citation_styles.CITATION_STYLES` and the
 * classic `PRKS_CITATION_STYLE_IDS` (tests pin the three lists). Each id is
 * the Citation Style Language file name under `/vendor/csl/`.
 */

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

export function isCitationStyle(id: unknown): id is string {
  return typeof id === 'string' && STYLE_IDS.has(id)
}

export function citationStyleLabel(id: string): string {
  return CITATION_STYLES.find((style) => style.id === id)?.label ?? id
}

/** The Library's default style, as the classic settings client last read it. */
export function libraryCitationStyle(): string {
  const stored = (globalThis as { __prksCitationStyle?: unknown }).__prksCitationStyle
  return isCitationStyle(stored) ? stored : DEFAULT_CITATION_STYLE
}

/** A Work's own style when it has one PRKS ships, else the Library's default. */
export function effectiveCitationStyle(workStyle: unknown): string {
  return isCitationStyle(workStyle) ? workStyle : libraryCitationStyle()
}
