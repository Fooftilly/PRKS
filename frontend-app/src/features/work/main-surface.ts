import type { RelSummaryPart } from '../../components/relSummary'

/**
 * Work tile shell model. The route session paints `WorkMainSurface`.
 * Video HTML comes from renderVideoViewerPane. The PDF host is filled by
 * initPdfViewerForWork. Research Notes mount into the anchor afterwards.
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
  viewerHtml: string
  editorRegionId: string
}
