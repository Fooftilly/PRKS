import { autoUpdate, computePosition, flip, offset, shift } from '@floating-ui/dom'

/**
 * Positions one floating element against a reference.
 * Collision is flip and shift. This does not store application state.
 * Absolute positioning matches the PDF pane, which is the containing block.
 */
export function bindFloatingPosition(
  reference: Element,
  floating: HTMLElement,
  boundary?: Element | null,
): () => void {
  const boundaryEl = boundary || undefined
  return autoUpdate(reference, floating, () => {
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
