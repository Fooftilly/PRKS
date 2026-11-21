/**
 * The citations bridge for classic callers (Citation V1, #41).
 *
 * The selection toolbar (`frontend/js/work-selection.js`) offers Cite… for
 * the selected Works and calls `window.prksCitations`, so the request,
 * formatting, clipboard and download stay in this one module.
 */
import { exportCitations, type CitationExportKind, type CitationExportResult } from './export'
import { citationStyleLabel, libraryCitationStyle } from './styles'

export interface CitationsBridge {
  exportSelection(workIds: readonly string[], kind: CitationExportKind): Promise<CitationExportResult>
  libraryStyleLabel(): string
}

const KINDS = new Set<CitationExportKind>(['formatted', 'bibtex', 'bib-file', 'csl-json-file'])

export function citationsBridge(): CitationsBridge {
  return {
    exportSelection(workIds, kind) {
      if (!KINDS.has(kind)) return Promise.reject(new Error('Unknown citation export.'))
      const ids = Array.isArray(workIds) ? workIds.filter((id): id is string => typeof id === 'string') : []
      return exportCitations(ids, kind)
    },
    libraryStyleLabel: () => citationStyleLabel(libraryCitationStyle()),
  }
}

export function registerCitationsBridge(target: Window = window): void {
  target.prksCitations = citationsBridge()
}
