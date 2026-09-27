/**
 * Closest supported Pragmatic test harness for jsdom (#234).
 *
 * Official guidance: https://atlassian.design/components/pragmatic-drag-and-drop/core-package/testing/jest-and-jsdom
 * jsdom lacks DragEvent/DOMRect; polyfills come from
 * `@atlaskit/pragmatic-drag-and-drop-unit-testing`. Events are native HTML5 DnD
 * events that Pragmatic's element adapter listens for — not direct resolver calls.
 *
 * Limitation: this is jsdom + polyfilled DragEvent, not Chromium pointer hardware.
 * It still executes Pragmatic mount/monitor/drop-target lifecycle callbacks.
 */
import '@atlaskit/pragmatic-drag-and-drop-unit-testing/drag-event-polyfill'
import '@atlaskit/pragmatic-drag-and-drop-unit-testing/dom-rect-polyfill'

type RafCallback = FrameRequestCallback

let rafQueue: RafCallback[] = []
let rafInstalled = false
let hitTestInstalled = false

/**
 * jsdom lacks document.elementsFromPoint and Element.scrollBy; Pragmatic uses
 * both for drag-handle checks and auto-scroll.
 */
export function installElementsFromPoint(): void {
  if (hitTestInstalled) return
  hitTestInstalled = true
  const doc = document
  doc.elementsFromPoint = (x: number, y: number) => {
    const nodes = Array.from(doc.querySelectorAll('*')) as Element[]
    const hits: Element[] = []
    for (const el of nodes) {
      const r = el.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) continue
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) hits.push(el)
    }
    // Deepest first (approximate browser ordering).
    return hits.reverse()
  }
  doc.elementFromPoint = (x: number, y: number) => doc.elementsFromPoint(x, y)[0] ?? null

  if (typeof Element !== 'undefined' && typeof Element.prototype.scrollBy !== 'function') {
    Element.prototype.scrollBy = function scrollBy(this: Element, ...args: unknown[]) {
      const el = this as HTMLElement
      let dx = 0
      let dy = 0
      if (typeof args[0] === 'number') {
        dx = args[0]
        dy = typeof args[1] === 'number' ? args[1] : 0
      } else if (args[0] && typeof args[0] === 'object') {
        const opts = args[0] as { left?: number; top?: number }
        dx = opts.left ?? 0
        dy = opts.top ?? 0
      }
      el.scrollLeft = (el.scrollLeft || 0) + dx
      el.scrollTop = (el.scrollTop || 0) + dy
    }
  }
}

export function installRafController(): void {
  installElementsFromPoint()
  if (rafInstalled) {
    rafQueue = []
    return
  }
  rafInstalled = true
  rafQueue = []
  const originalRaf = globalThis.requestAnimationFrame.bind(globalThis)
  const originalCancel = globalThis.cancelAnimationFrame.bind(globalThis)
  globalThis.requestAnimationFrame = ((cb: RafCallback) => {
    rafQueue.push(cb)
    return rafQueue.length
  }) as typeof requestAnimationFrame
  globalThis.cancelAnimationFrame = ((id: number) => {
    const idx = id - 1
    if (idx >= 0 && idx < rafQueue.length) rafQueue[idx] = () => {}
  }) as typeof cancelAnimationFrame
  ;(installRafController as unknown as { _restore?: () => void })._restore = () => {
    globalThis.requestAnimationFrame = originalRaf
    globalThis.cancelAnimationFrame = originalCancel
    rafInstalled = false
    rafQueue = []
  }
}

export function restoreRafController(): void {
  ;(installRafController as unknown as { _restore?: () => void })._restore?.()
}

export function flushAnimationFrames(count = 1): void {
  for (let i = 0; i < count; i++) {
    const batch = rafQueue.splice(0)
    for (const cb of batch) cb(performance.now())
  }
}

export function fireDrag(
  type: string,
  target: EventTarget,
  init: {
    clientX?: number
    clientY?: number
    bubbles?: boolean
    cancelable?: boolean
  } = {},
): DragEvent {
  const event = new DragEvent(type, {
    bubbles: init.bubbles !== false,
    cancelable: init.cancelable !== false,
    clientX: init.clientX ?? 0,
    clientY: init.clientY ?? 0,
  })
  target.dispatchEvent(event)
  return event
}

/** Lift → one animation frame (Pragmatic onDragStart) → enter/over target → frame (onDrag). */
export function startDragOver(
  source: HTMLElement,
  over: HTMLElement,
  clientX: number,
  clientY: number,
): void {
  fireDrag('dragstart', source, { clientX, clientY })
  flushAnimationFrames(1)
  fireDrag('dragenter', over, { clientX, clientY })
  fireDrag('dragover', over, { clientX, clientY })
  flushAnimationFrames(1)
}

export function dropOn(over: HTMLElement, clientX: number, clientY: number): void {
  fireDrag('drop', over, { clientX, clientY })
  flushAnimationFrames(1)
}

export function dragEnd(source: EventTarget = window): void {
  fireDrag('dragend', source)
  // Honey-pot / broken-drag cleanup path also listens for pointermove.
  document.dispatchEvent(new Event('pointermove', { bubbles: true }))
  flushAnimationFrames(2)
}

export function mockClientRect(
  el: Element,
  rect: { left: number; top: number; width: number; height: number },
): void {
  const full = {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    right: rect.left + rect.width,
    bottom: rect.top + rect.height,
    x: rect.left,
    y: rect.top,
    toJSON() {
      return full
    },
  }
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true,
    value: () => full,
  })
}
