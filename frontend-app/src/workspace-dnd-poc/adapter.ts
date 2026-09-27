/**
 * Experimental Pragmatic Drag and Drop adapter for #234.
 *
 * Isolated PoC: not imported by main.ts or production WorkspaceShell.
 * Does not replace frontend/js/workspace-drag.js. Activate only under an
 * explicit experimental flag or from tests/fixtures.
 *
 * Architecture:
 *   Pragmatic sensors/lifecycle/targets/preview/autoscroll/cancel
 *     → pure resolveDropIntent
 *     → commitDropIntent → coordinator commands
 *     → WorkspaceState + Vue WorkspaceShell projection
 */
import {
  draggable,
  dropTargetForElements,
  monitorForElements,
} from '@atlaskit/pragmatic-drag-and-drop/element/adapter'
import { disableNativeDragPreview } from '@atlaskit/pragmatic-drag-and-drop/element/disable-native-drag-preview'
import { autoScrollForElements } from '@atlaskit/pragmatic-drag-and-drop-auto-scroll/element'

type CleanupFn = () => void

function cssEscape(id: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(id)
  return String(id).replace(/["\\]/g, '\\$&')
}
import { browserCommitHandlers, commitDropIntent, type PocCommitHandlers } from './commit'
import {
  pickNestedLeafHit,
  resolveDropIntent,
  type DragSource,
  type DropHit,
  type WorkspaceDropIntent,
} from './drop-intent'
import { createHoverController, type HoverController } from './hover'

export const PRKS_DND_POC_FLAG = 'prksExperimentalPragmaticDnd'

export interface PocSnapshot {
  readonly mainTabId: string | null
  readonly secondaryLeafTabIds: readonly string[]
  readonly hasSecondaryTree: boolean
  readonly narrowFallback: boolean
  readonly canAddSecondaryLeaf: boolean
  readonly tabs: readonly { readonly id: string; readonly route: string; readonly title?: string }[]
}

export interface BindPocAdapterOptions {
  readonly root?: ParentNode
  readonly getSnapshot: () => PocSnapshot | null
  readonly routeSupportsTile: (route: string) => boolean
  readonly handlers?: PocCommitHandlers
  readonly hover?: HoverController
  readonly onHoverIntent?: (intent: WorkspaceDropIntent | null) => void
  readonly onSessionEnd?: (reason: 'drop' | 'cancel') => void
}

export interface PocAdapterSession {
  readonly active: boolean
  readonly source: DragSource | null
  readonly intent: WorkspaceDropIntent | null
  cancel(): void
  destroy(): void
}

type SourceData = {
  readonly type: 'prks-workspace-poc'
  readonly source: DragSource
}

function isSourceData(data: Record<string | symbol, unknown>): data is SourceData {
  return data.type === 'prks-workspace-poc' && !!data.source && typeof data.source === 'object'
}

function announce(text: string): void {
  const el = document.getElementById('prks-workspace-live')
  if (!el) return
  el.textContent = ''
  el.textContent = text
}

function hitFromPointer(
  root: ParentNode,
  source: DragSource,
  clientX: number,
  clientY: number,
  snap: PocSnapshot,
): DropHit {
  const list = root.querySelector('#prks-workspace-tabs') as HTMLElement | null
  if (list) {
    const rect = list.getBoundingClientRect()
    const pad = 14
    if (
      clientX >= rect.left - pad &&
      clientX <= rect.right + pad &&
      clientY >= rect.top - pad &&
      clientY <= rect.bottom + pad
    ) {
      const wraps = Array.from(list.querySelectorAll('.prks-workspace-tab'))
      const otherTabRects = []
      for (const wrap of wraps) {
        const id = wrap.getAttribute('data-tab-id')
        if (!id || id === source.tabId) continue
        const r = wrap.getBoundingClientRect()
        otherTabRects.push({ id, left: r.left, right: r.right })
      }
      return { kind: 'strip', x: clientX, otherTabRects }
    }
  }

  const leaves = snap.secondaryLeafTabIds.map((tabId) => {
    const tile = root.querySelector(
      '.prks-tile[data-prks-tab-id="' + cssEscape(tabId) + '"]',
    ) as HTMLElement | null
    if (!tile) return null
    const r = tile.getBoundingClientRect()
    return {
      tabId,
      rect: { left: r.left, top: r.top, width: r.width, height: r.height },
    }
  }).filter((entry): entry is NonNullable<typeof entry> => !!entry)

  const leafHit = pickNestedLeafHit(leaves, clientX, clientY, source.tabId)
  if (leafHit) return leafHit

  if (!snap.hasSecondaryTree && source.kind === 'tab') {
    const canvas = root.querySelector('.prks-workspace-canvas') as HTMLElement | null
    if (canvas) {
      const r = canvas.getBoundingClientRect()
      return {
        kind: 'empty-secondary',
        canvasRect: { left: r.left, top: r.top, width: r.width, height: r.height },
        x: clientX,
        y: clientY,
      }
    }
  }
  return null
}

function paintHover(
  hover: HoverController,
  root: ParentNode,
  intent: WorkspaceDropIntent | null,
): void {
  if (!intent) {
    hover.clear()
    return
  }
  const list = root.querySelector('#prks-workspace-tabs') as HTMLElement | null
  if (intent.kind === 'tab-reorder' && list) {
    hover.showReorderMarker(list, intent.beforeTabId)
    return
  }
  if (intent.kind === 'park' && list) {
    hover.showParkTarget(list)
    return
  }
  if (intent.kind === 'secondary-empty') {
    const canvas = root.querySelector('.prks-workspace-canvas') as HTMLElement | null
    if (canvas) hover.showEmptySecondary(canvas)
    return
  }
  if (intent.kind === 'secondary-edge') {
    const tile = root.querySelector(
      '.prks-tile[data-prks-tab-id="' + cssEscape(intent.tabId) + '"]',
    ) as HTMLElement | null
    if (tile) hover.showEdgeOverlay(tile, intent.zone, intent.valid)
  }
}

/**
 * Bind the experimental adapter onto a mounted Vue workspace shell DOM.
 * Returns a session handle. Call destroy() on unmount.
 */
export function bindPocAdapter(options: BindPocAdapterOptions): PocAdapterSession {
  const root = options.root ?? document
  const hover = options.hover ?? createHoverController()
  const handlers = options.handlers ?? browserCommitHandlers()
  const cleanups: CleanupFn[] = []
  let active = false
  let source: DragSource | null = null
  let intent: WorkspaceDropIntent | null = null
  let cancelled = false

  function resetSessionVisuals(): void {
    hover.clear()
    document.body.classList.remove('prks-workspace-dragging')
    document.querySelectorAll('.is-drag-source').forEach((el) => el.classList.remove('is-drag-source'))
  }

  function endSession(reason: 'drop' | 'cancel'): void {
    active = false
    source = null
    intent = null
    resetSessionVisuals()
    options.onSessionEnd?.(reason)
  }

  function cancel(): void {
    if (!active && !source) {
      resetSessionVisuals()
      return
    }
    cancelled = true
    announce('Move cancelled.')
    endSession('cancel')
  }

  function onKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && active) {
      event.preventDefault()
      cancel()
    }
  }

  function onWindowBlur(): void {
    if (active) cancel()
  }

  document.addEventListener('keydown', onKeyDown, true)
  window.addEventListener('blur', onWindowBlur)
  cleanups.push(() => {
    document.removeEventListener('keydown', onKeyDown, true)
    window.removeEventListener('blur', onWindowBlur)
  })

  const tabEls = Array.from(root.querySelectorAll('.prks-workspace-tab')) as HTMLElement[]
  for (const tabEl of tabEls) {
    const tabId = tabEl.getAttribute('data-tab-id')
    if (!tabId) continue
    const handle =
      (tabEl.querySelector('.prks-workspace-tab__activate') as HTMLElement | null) || tabEl
    cleanups.push(
      draggable({
        element: tabEl,
        dragHandle: handle,
        canDrag: () => !tabEl.querySelector('.prks-workspace-tab__close:hover'),
        getInitialData: (): SourceData => ({
          type: 'prks-workspace-poc',
          source: { kind: 'tab', tabId },
        }),
        onGenerateDragPreview: ({ nativeSetDragImage }) => {
          disableNativeDragPreview({ nativeSetDragImage })
        },
      }),
    )
  }

  const grips = Array.from(root.querySelectorAll('.prks-tile-header__grip')) as HTMLElement[]
  for (const grip of grips) {
    const tile = grip.closest('[data-prks-tab-id]') as HTMLElement | null
    const tabId = tile?.getAttribute('data-prks-tab-id')
    if (!tile || !tabId) continue
    cleanups.push(
      draggable({
        element: tile,
        dragHandle: grip,
        getInitialData: (): SourceData => ({
          type: 'prks-workspace-poc',
          source: { kind: 'pane', tabId },
        }),
        onGenerateDragPreview: ({ nativeSetDragImage }) => {
          disableNativeDragPreview({ nativeSetDragImage })
        },
      }),
    )
  }

  const strip = root.querySelector('#prks-workspace-tabs') as HTMLElement | null
  if (strip) {
    cleanups.push(
      dropTargetForElements({
        element: strip,
        getData: () => ({ prksTarget: 'strip' }),
        canDrop: ({ source: src }) => isSourceData(src.data),
      }),
    )
    cleanups.push(
      autoScrollForElements({
        element: strip,
        canScroll: ({ source: src }) => isSourceData(src.data) && src.data.source.kind === 'tab',
      }),
    )
  }

  const tiles = Array.from(root.querySelectorAll('.prks-tile[data-prks-tab-id]')) as HTMLElement[]
  for (const tile of tiles) {
    cleanups.push(
      dropTargetForElements({
        element: tile,
        getData: () => ({
          prksTarget: 'leaf',
          tabId: tile.getAttribute('data-prks-tab-id'),
        }),
        canDrop: ({ source: src }) => isSourceData(src.data),
      }),
    )
  }

  const canvas = root.querySelector('.prks-workspace-canvas') as HTMLElement | null
  if (canvas) {
    cleanups.push(
      dropTargetForElements({
        element: canvas,
        getData: () => ({ prksTarget: 'canvas' }),
        canDrop: ({ source: src }) => isSourceData(src.data),
      }),
    )
  }

  cleanups.push(
    monitorForElements({
      canMonitor: ({ source: src }) => isSourceData(src.data),
      onDragStart: ({ source: src }) => {
        if (!isSourceData(src.data)) return
        cancelled = false
        active = true
        source = src.data.source
        document.body.classList.add('prks-workspace-dragging')
        const selector =
          source.kind === 'pane'
            ? '.prks-tile[data-prks-tab-id="' + cssEscape(source.tabId) + '"]'
            : '.prks-workspace-tab[data-tab-id="' + cssEscape(source.tabId) + '"]'
        document.querySelector(selector)?.classList.add('is-drag-source')
        announce('Dragging.')
      },
      onDrag: ({ location, source: src }) => {
        if (cancelled || !isSourceData(src.data)) return
        const dragSource = src.data.source
        const snap = options.getSnapshot()
        if (!snap) {
          intent = null
          paintHover(hover, root, null)
          options.onHoverIntent?.(null)
          return
        }
        const tab = snap.tabs.find((t) => t.id === dragSource.tabId)
        const hit = hitFromPointer(
          root,
          dragSource,
          location.current.input.clientX,
          location.current.input.clientY,
          snap,
        )
        intent = resolveDropIntent({
          source: dragSource,
          mainTabId: snap.mainTabId,
          secondaryLeafTabIds: snap.secondaryLeafTabIds,
          hasSecondaryTree: snap.hasSecondaryTree,
          narrowFallback: snap.narrowFallback,
          canAddSecondaryLeaf: snap.canAddSecondaryLeaf,
          sourceRouteSupportsTile: tab ? options.routeSupportsTile(tab.route) : false,
          hit,
        })
        paintHover(hover, root, intent)
        options.onHoverIntent?.(intent)
      },
      onDrop: ({ source: src }) => {
        if (cancelled) {
          endSession('cancel')
          return
        }
        if (!isSourceData(src.data)) {
          endSession('cancel')
          return
        }
        const snap = options.getSnapshot()
        const finalIntent = intent
        const dropSource = src.data.source
        const leaves = snap?.secondaryLeafTabIds ?? []
        // Clear hover before commit — never leave ephemeral DOM as state.
        endSession('drop')
        void commitDropIntent(dropSource, finalIntent, leaves, handlers)
      },
    }),
  )

  return {
    get active() {
      return active
    },
    get source() {
      return source
    },
    get intent() {
      return intent
    },
    cancel,
    destroy() {
      cancel()
      while (cleanups.length) {
        const fn = cleanups.pop()
        try {
          fn?.()
        } catch {
          /* idempotent teardown */
        }
      }
      hover.clear()
    },
  }
}

export function isPocEnabled(win: Window = window): boolean {
  try {
    return (
      win.sessionStorage?.getItem(PRKS_DND_POC_FLAG) === '1' ||
      (win as Window & { __prksExperimentalPragmaticDnd?: boolean }).__prksExperimentalPragmaticDnd ===
        true
    )
  } catch {
    return false
  }
}
