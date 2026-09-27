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
import { setCustomNativeDragPreview } from '@atlaskit/pragmatic-drag-and-drop/element/set-custom-native-drag-preview'
import { autoScrollForElements } from '@atlaskit/pragmatic-drag-and-drop-auto-scroll/element'
import { browserCommitHandlers, commitDropIntent, type PocCommitHandlers } from './commit'
import {
  pickNestedLeafHit,
  resolveDropIntent,
  type DragSource,
  type DropHit,
  type WorkspaceDropIntent,
} from './drop-intent'
import { createHoverController, type HoverController } from './hover'

type CleanupFn = () => void

export const PRKS_DND_POC_FLAG = 'prksExperimentalPragmaticDnd'

const ZONE_LABEL = { left: 'left of', right: 'right of', above: 'above', below: 'below' } as const

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
  /** When false, skip MutationObserver auto-reconcile (tests may call reconcile()). */
  readonly observeDom?: boolean
}

export interface PocAdapterSession {
  readonly active: boolean
  readonly source: DragSource | null
  readonly intent: WorkspaceDropIntent | null
  /** Recompute intent at a pointer position (also used by onDrop). */
  resolveAt(clientX: number, clientY: number, dragSource?: DragSource | null): WorkspaceDropIntent | null
  /** Re-register draggables/drop targets after Vue projection DOM changes. */
  reconcile(): void
  /**
   * Explicit rebind for PoC fixtures. Same as reconcile(); not wired to the
   * production `prksWorkspaceOnShellCommit` hook (MutationObserver / manual call only).
   */
  refresh(): void
  cancel(): void
  destroy(): void
}

type SourceData = {
  readonly type: 'prks-workspace-poc'
  readonly source: DragSource
}

function cssEscape(id: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(id)
  return String(id).replace(/["\\]/g, '\\$&')
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

function sameIntent(a: WorkspaceDropIntent | null, b: WorkspaceDropIntent | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  if (a.kind !== b.kind) return false
  if (a.kind === 'tab-reorder' && b.kind === 'tab-reorder') {
    return a.beforeTabId === b.beforeTabId && a.index === b.index
  }
  if (a.kind === 'secondary-edge' && b.kind === 'secondary-edge') {
    return (
      a.tabId === b.tabId &&
      a.zone === b.zone &&
      a.valid === b.valid &&
      a.axis === b.axis &&
      a.placement === b.placement
    )
  }
  if (a.kind === 'secondary-empty' && b.kind === 'secondary-empty') return a.valid === b.valid
  return true
}

export function hitFromPointer(
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
      let sourceIndex = -1
      let fullIndex = 0
      for (const wrap of wraps) {
        const id = wrap.getAttribute('data-tab-id')
        if (!id) continue
        if (id === source.tabId) {
          sourceIndex = fullIndex
          fullIndex += 1
          continue
        }
        const r = wrap.getBoundingClientRect()
        otherTabRects.push({ id, left: r.left, right: r.right })
        fullIndex += 1
      }
      return { kind: 'strip', x: clientX, otherTabRects, sourceIndex }
    }
  }

  const leaves = snap.secondaryLeafTabIds
    .map((tabId) => {
      const tile = root.querySelector(
        '.prks-tile[data-prks-tab-id="' + cssEscape(tabId) + '"]',
      ) as HTMLElement | null
      if (!tile) return null
      const r = tile.getBoundingClientRect()
      return {
        tabId,
        rect: { left: r.left, top: r.top, width: r.width, height: r.height },
      }
    })
    .filter((entry): entry is NonNullable<typeof entry> => !!entry)

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

export function resolveIntentAtPoint(
  root: ParentNode,
  snap: PocSnapshot,
  source: DragSource,
  clientX: number,
  clientY: number,
  routeSupportsTile: (route: string) => boolean,
): WorkspaceDropIntent | null {
  const tab = snap.tabs.find((t) => t.id === source.tabId)
  return resolveDropIntent({
    source,
    mainTabId: snap.mainTabId,
    secondaryLeafTabIds: snap.secondaryLeafTabIds,
    hasSecondaryTree: snap.hasSecondaryTree,
    narrowFallback: snap.narrowFallback,
    canAddSecondaryLeaf: snap.canAddSecondaryLeaf,
    sourceRouteSupportsTile: tab ? routeSupportsTile(tab.route) : false,
    hit: hitFromPointer(root, source, clientX, clientY, snap),
  })
}

function titleFor(snap: PocSnapshot | null, tabId: string): string {
  const tab = snap?.tabs.find((t) => t.id === tabId)
  return tab?.title || 'page'
}

function announceTargetChange(snap: PocSnapshot | null, target: WorkspaceDropIntent | null): void {
  if (!target) {
    announce('No drop target.')
    return
  }
  if (target.kind === 'tab-reorder') {
    announce(
      target.beforeTabId
        ? 'Move tab before ' + titleFor(snap, target.beforeTabId) + '.'
        : 'Move tab to the end.',
    )
    return
  }
  if (target.kind === 'park') {
    announce('Park pane.')
    return
  }
  if (target.kind === 'secondary-empty') {
    announce('Open in split view.')
    return
  }
  if (target.kind === 'secondary-edge') {
    if (!target.valid) {
      announce(target.reason === 'cap' ? 'Maximum of 4 visible panes.' : 'This page cannot be split.')
      return
    }
    announce('Split ' + ZONE_LABEL[target.zone] + ' ' + titleFor(snap, target.tabId) + '.')
  }
}

function announceDropOutcome(
  snap: PocSnapshot | null,
  source: DragSource,
  intent: WorkspaceDropIntent | null,
  ok: boolean,
): void {
  if (!ok || !intent) {
    announce('Move cancelled.')
    return
  }
  const title = titleFor(snap, source.tabId)
  if (intent.kind === 'tab-reorder' || intent.kind === 'secondary-edge') {
    announce(title + (intent.kind === 'secondary-edge' && source.kind === 'tab' ? ' added to split view.' : ' moved.'))
    return
  }
  if (intent.kind === 'park') {
    announce(title + ' parked.')
    return
  }
  if (intent.kind === 'secondary-empty') {
    announce(title + ' opened in split view.')
  }
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
    if (tile) hover.showEdgeOverlay(tile, intent.zone, intent.valid, intent.reason)
  }
}

function sourceElement(root: ParentNode, dragSource: DragSource): HTMLElement | null {
  const selector =
    dragSource.kind === 'pane'
      ? '.prks-tile[data-prks-tab-id="' + cssEscape(dragSource.tabId) + '"]'
      : '.prks-workspace-tab[data-tab-id="' + cssEscape(dragSource.tabId) + '"]'
  return root.querySelector(selector) as HTMLElement | null
}

/**
 * Bind the experimental adapter onto a mounted Vue workspace shell DOM.
 * Returns a session handle. Call destroy() on unmount.
 */
export function bindPocAdapter(options: BindPocAdapterOptions): PocAdapterSession {
  const root = options.root ?? document
  const hover = options.hover ?? createHoverController()
  const handlers = options.handlers ?? browserCommitHandlers()
  const permanentCleanups: CleanupFn[] = []
  let bindingCleanups: CleanupFn[] = []
  let active = false
  let source: DragSource | null = null
  let intent: WorkspaceDropIntent | null = null
  let cancelled = false
  let destroyed = false
  let reconcileTimer: ReturnType<typeof setTimeout> | null = null

  const priorCancel = window.prksWorkspaceCancelActiveDrag

  function resetSessionVisuals(): void {
    hover.clear()
    document.body.classList.remove('prks-workspace-dragging')
    document.querySelectorAll('.is-drag-source').forEach((el) => el.classList.remove('is-drag-source'))
  }

  function endSession(reason: 'drop' | 'cancel'): void {
    // Idempotent: cancel() then Pragmatic onDrop must not fire onSessionEnd twice.
    if (!active && !source) return
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

  function resolveAt(
    clientX: number,
    clientY: number,
    dragSource: DragSource | null = source,
  ): WorkspaceDropIntent | null {
    if (!dragSource) return null
    const snap = options.getSnapshot()
    if (!snap) return null
    return resolveIntentAtPoint(root, snap, dragSource, clientX, clientY, options.routeSupportsTile)
  }

  function applyIntent(next: WorkspaceDropIntent | null, snap: PocSnapshot | null): void {
    if (sameIntent(intent, next)) {
      intent = next
      paintHover(hover, root, next)
      return
    }
    intent = next
    paintHover(hover, root, next)
    announceTargetChange(snap, next)
    options.onHoverIntent?.(next)
  }

  function bindElements(): void {
    while (bindingCleanups.length) {
      const fn = bindingCleanups.pop()
      try {
        fn?.()
      } catch {
        /* idempotent */
      }
    }

    const tabEls = Array.from(root.querySelectorAll('.prks-workspace-tab')) as HTMLElement[]
    for (const tabEl of tabEls) {
      const tabId = tabEl.getAttribute('data-tab-id')
      if (!tabId) continue
      const handle =
        (tabEl.querySelector('.prks-workspace-tab__activate') as HTMLElement | null) || tabEl
      bindingCleanups.push(
        draggable({
          element: tabEl,
          dragHandle: handle,
          canDrag: () => !tabEl.querySelector('.prks-workspace-tab__close:hover'),
          getInitialData: (): SourceData => ({
            type: 'prks-workspace-poc',
            source: { kind: 'tab', tabId },
          }),
          onGenerateDragPreview: ({ nativeSetDragImage }) => {
            const snap = options.getSnapshot()
            const label = titleFor(snap, tabId)
            setCustomNativeDragPreview({
              nativeSetDragImage,
              getOffset: () => ({ x: 14, y: 10 }),
              render({ container }) {
                const el = document.createElement('div')
                el.className = 'prks-drag-preview'
                el.setAttribute('aria-hidden', 'true')
                el.dataset.prksDndPoc = '1'
                const text = document.createElement('span')
                text.className = 'prks-drag-preview__label'
                text.textContent = label
                el.appendChild(text)
                container.appendChild(el)
              },
            })
          },
        }),
      )
    }

    const grips = Array.from(root.querySelectorAll('.prks-tile-header__grip')) as HTMLElement[]
    for (const grip of grips) {
      const tile = grip.closest('[data-prks-tab-id]') as HTMLElement | null
      const tabId = tile?.getAttribute('data-prks-tab-id')
      if (!tile || !tabId) continue
      bindingCleanups.push(
        draggable({
          element: tile,
          dragHandle: grip,
          getInitialData: (): SourceData => ({
            type: 'prks-workspace-poc',
            source: { kind: 'pane', tabId },
          }),
          onGenerateDragPreview: ({ nativeSetDragImage }) => {
            const snap = options.getSnapshot()
            const label = titleFor(snap, tabId)
            setCustomNativeDragPreview({
              nativeSetDragImage,
              getOffset: () => ({ x: 14, y: 10 }),
              render({ container }) {
                const el = document.createElement('div')
                el.className = 'prks-drag-preview'
                el.setAttribute('aria-hidden', 'true')
                el.dataset.prksDndPoc = '1'
                const text = document.createElement('span')
                text.className = 'prks-drag-preview__label'
                text.textContent = label
                el.appendChild(text)
                container.appendChild(el)
              },
            })
          },
        }),
      )
    }

    const strip = root.querySelector('#prks-workspace-tabs') as HTMLElement | null
    if (strip) {
      bindingCleanups.push(
        dropTargetForElements({
          element: strip,
          getData: () => ({ prksTarget: 'strip' }),
          canDrop: ({ source: src }) => isSourceData(src.data),
        }),
      )
      bindingCleanups.push(
        autoScrollForElements({
          element: strip,
          canScroll: ({ source: src }) => isSourceData(src.data) && src.data.source.kind === 'tab',
        }),
      )
    }

    const tiles = Array.from(root.querySelectorAll('.prks-tile[data-prks-tab-id]')) as HTMLElement[]
    for (const tile of tiles) {
      bindingCleanups.push(
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
      bindingCleanups.push(
        dropTargetForElements({
          element: canvas,
          getData: () => ({ prksTarget: 'canvas' }),
          canDrop: ({ source: src }) => isSourceData(src.data),
        }),
      )
    }
  }

  function reconcile(): void {
    if (destroyed) return
    if (active && source) {
      const el = sourceElement(root, source)
      if (!el || !document.contains(el)) {
        cancel()
      }
    }
    bindElements()
  }

  function scheduleReconcile(): void {
    if (destroyed) return
    if (reconcileTimer != null) clearTimeout(reconcileTimer)
    reconcileTimer = setTimeout(() => {
      reconcileTimer = null
      reconcile()
    }, 0)
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
  permanentCleanups.push(() => {
    document.removeEventListener('keydown', onKeyDown, true)
    window.removeEventListener('blur', onWindowBlur)
  })

  function cancelHook(): void {
    cancel()
    if (typeof priorCancel === 'function') {
      try {
        priorCancel()
      } catch {
        /* prior hook best-effort */
      }
    }
  }
  window.prksWorkspaceCancelActiveDrag = cancelHook
  permanentCleanups.push(() => {
    if (window.prksWorkspaceCancelActiveDrag === cancelHook) {
      window.prksWorkspaceCancelActiveDrag = priorCancel
    }
  })

  bindElements()

  if (options.observeDom !== false && typeof MutationObserver !== 'undefined') {
    const observer = new MutationObserver(() => scheduleReconcile())
    const tabs = root.querySelector('#prks-workspace-tabs')
    const page = root.querySelector('#page-content')
    if (tabs) observer.observe(tabs, { childList: true, subtree: true })
    if (page) observer.observe(page, { childList: true, subtree: true })
    permanentCleanups.push(() => observer.disconnect())
  }

  permanentCleanups.push(
    monitorForElements({
      canMonitor: ({ source: src }) => isSourceData(src.data),
      onDragStart: ({ source: src }) => {
        if (!isSourceData(src.data)) return
        cancelled = false
        active = true
        source = src.data.source
        intent = null
        document.body.classList.add('prks-workspace-dragging')
        sourceElement(root, source)?.classList.add('is-drag-source')
        const snap = options.getSnapshot()
        announce('Dragging ' + titleFor(snap, source.tabId) + '.')
      },
      onDrag: ({ location, source: src }) => {
        if (cancelled || !isSourceData(src.data)) return
        const dragSource = src.data.source
        const snap = options.getSnapshot()
        if (!snap) {
          applyIntent(null, null)
          return
        }
        const next = resolveIntentAtPoint(
          root,
          snap,
          dragSource,
          location.current.input.clientX,
          location.current.input.clientY,
          options.routeSupportsTile,
        )
        applyIntent(next, snap)
      },
      onDrop: ({ location, source: src }) => {
        if (cancelled) {
          endSession('cancel')
          return
        }
        if (!isSourceData(src.data)) {
          endSession('cancel')
          return
        }
        const dropSource = src.data.source
        const snap = options.getSnapshot()
        // Native cancel (Esc / lost capture) can fire onDrop with cancelled still false
        // and empty dropTargets — abort before resolve/commit.
        if (!snap || location.current.dropTargets.length === 0) {
          announce('Move cancelled.')
          endSession('cancel')
          return
        }
        // Source may already be gone (e.g. hideLeaf parked the pane before deferred
        // reconcile cancel) — do not resolve/commit or a stale drop can remount via split-leaf.
        const liveSource = sourceElement(root, dropSource)
        if (!liveSource || !document.contains(liveSource)) {
          announce('Move cancelled.')
          endSession('cancel')
          return
        }
        // Final-pointer re-resolve only when drop targets are still active.
        const finalIntent = resolveIntentAtPoint(
          root,
          snap,
          dropSource,
          location.current.input.clientX,
          location.current.input.clientY,
          options.routeSupportsTile,
        )
        const leaves = snap.secondaryLeafTabIds
        const validIntent =
          finalIntent &&
          (finalIntent.kind !== 'secondary-edge' || finalIntent.valid)
            ? finalIntent
            : null
        endSession('drop')
        void commitDropIntent(dropSource, validIntent, leaves, handlers).then((result) => {
          announceDropOutcome(snap, dropSource, validIntent, result.ok)
        })
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
    resolveAt,
    reconcile,
    refresh: reconcile,
    cancel,
    destroy() {
      destroyed = true
      if (reconcileTimer != null) {
        clearTimeout(reconcileTimer)
        reconcileTimer = null
      }
      cancel()
      while (bindingCleanups.length) {
        try {
          bindingCleanups.pop()?.()
        } catch {
          /* idempotent */
        }
      }
      while (permanentCleanups.length) {
        try {
          permanentCleanups.pop()?.()
        } catch {
          /* idempotent */
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
