import { autoUpdate, computePosition, flip, offset, shift } from '@floating-ui/dom'

export type FloatingBoundary = Element | { x: number; y: number; width: number; height: number }

/**
 * Positions one floating element against a reference.
 * Collision is flip and shift. This does not store application state.
 * Absolute positioning matches the PDF pane, which is the containing block.
 * A boundary function is read on each update so a later overlay can clip it.
 */
export function bindFloatingPosition(
  reference: Element,
  floating: HTMLElement,
  boundary?: FloatingBoundary | null | (() => FloatingBoundary | null),
): () => void {
  return autoUpdate(reference, floating, () => {
    const resolved = typeof boundary === 'function' ? boundary() : boundary
    const boundaryEl = resolved || undefined
    void computePosition(reference, floating, {
      placement: 'top',
      strategy: 'absolute',
      middleware: [
        offset(8),
        flip({ boundary: boundaryEl, padding: 8 }),
        shift({ boundary: boundaryEl, padding: 8 }),
      ],
    }).then((position) => {
      floating.style.position = 'absolute'
      floating.style.left = `${position.x}px`
      floating.style.top = `${position.y}px`
    })
  })
}
