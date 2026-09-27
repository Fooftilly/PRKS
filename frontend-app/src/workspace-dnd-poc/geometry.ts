/**
 * Pure workspace drag geometry (#234 PoC).
 *
 * Ported from frontend/js/workspace-drag.js so drop-intent resolution can be
 * unit-tested without DOM or Pragmatic sensors. Band / midpoint rules must
 * stay aligned with production until a migration intentionally changes them.
 */

export const EDGE_BAND = 0.28

export type EdgeZone = 'left' | 'right' | 'above' | 'below'

export interface RectLike {
  readonly left: number
  readonly top: number
  readonly width: number
  readonly height: number
}

export interface TabRect {
  readonly id: string
  readonly left: number
  readonly right: number
}

/** Edge band for a point inside `rect`. Center (beyond band) is null — no guess. */
export function computeEdgeZone(
  rect: RectLike | null | undefined,
  x: number,
  y: number,
  band: number = EDGE_BAND,
): EdgeZone | null {
  if (!rect || !rect.width || !rect.height) return null
  const b = typeof band === 'number' && band > 0 && band < 0.5 ? band : EDGE_BAND
  const px = x - rect.left
  const py = y - rect.top
  if (px < 0 || py < 0 || px > rect.width || py > rect.height) return null
  const dTop = py / rect.height
  const dBottom = (rect.height - py) / rect.height
  const dLeft = px / rect.width
  const dRight = (rect.width - px) / rect.width
  const min = Math.min(dTop, dBottom, dLeft, dRight)
  if (min > b) return null
  if (min === dLeft) return 'left'
  if (min === dRight) return 'right'
  if (min === dTop) return 'above'
  return 'below'
}

/** Insertion index among other tabs by midpoint geometry (dragged tab already excluded). */
export function computeReorderIndex(rects: readonly TabRect[] | null | undefined, x: number): number {
  if (!rects || !rects.length) return 0
  for (let i = 0; i < rects.length; i++) {
    const mid = (rects[i].left + rects[i].right) / 2
    if (x < mid) return i
  }
  return rects.length
}

export const EDGE_ZONE_TO_SPLIT: Record<
  EdgeZone,
  { readonly axis: 'left-right' | 'top-bottom'; readonly placement: 'first' | 'second' }
> = {
  left: { axis: 'left-right', placement: 'first' },
  right: { axis: 'left-right', placement: 'second' },
  above: { axis: 'top-bottom', placement: 'first' },
  below: { axis: 'top-bottom', placement: 'second' },
}
