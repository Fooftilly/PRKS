import type { RelSummaryPart } from '../../components/relSummary'
import type { WorkVideoSource } from './video-embed'

/**
 * Work tile shell model. The route session paints `WorkMainSurface`.
 * A video kind carries the effective source; `WorkVideoPane` resolves the
 * player. The PDF host is filled by initPdfViewerForWork. Research Notes
 * mount into the anchor afterwards.
 */

export type WorkMainSurfaceKind = 'pdf' | 'video' | 'empty'

export interface WorkMainSurfaceModel {
  workId: string
  generation?: number | null
  kind: WorkMainSurfaceKind
  hasFile: boolean
  showHeader: boolean
  title: string
  docTypeHtml: string
  relSummaryParts: RelSummaryPart[]
  video: WorkVideoSource | null
  editorRegionId: string
}
