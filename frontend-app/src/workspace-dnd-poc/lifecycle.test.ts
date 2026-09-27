/**
 * Lifecycle callback tests for the PoC adapter (#234 / CodeRabbit).
 *
 * Captures monitorForElements onDragStart/onDrag/onDrop and exercises
 * start→drag→drop and start→cancel→drop without bypassing adapter callbacks.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DragLocationHistory, ElementDragType } from '@atlaskit/pragmatic-drag-and-drop/types'

type MonitorArgs = Parameters<
  typeof import('@atlaskit/pragmatic-drag-and-drop/element/adapter').monitorForElements
>[0]

const monitorCapture: { args: MonitorArgs | null } = { args: null }

vi.mock('@atlaskit/pragmatic-drag-and-drop/element/adapter', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@atlaskit/pragmatic-drag-and-drop/element/adapter')>()
  return {
    ...actual,
    monitorForElements: (args: MonitorArgs) => {
      monitorCapture.args = args
      return () => {
        /* cleanup */
      }
    },
    draggable: () => () => {
      /* cleanup */
    },
    dropTargetForElements: () => () => {
      /* cleanup */
    },
  }
})

vi.mock('@atlaskit/pragmatic-drag-and-drop-auto-scroll/element', () => ({
  autoScrollForElements: () => () => {
    /* cleanup */
  },
}))

vi.mock('@atlaskit/pragmatic-drag-and-drop/element/set-custom-native-drag-preview', () => ({
  setCustomNativeDragPreview: () => {
    /* no-op */
  },
}))

const { bindPocAdapter } = await import('./adapter')
const { createHoverController } = await import('./hover')

function locationAt(clientX: number, clientY: number): DragLocationHistory {
  const input = {
    altKey: false,
    button: 0,
    buttons: 1,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    clientX,
    clientY,
    pageX: clientX,
    pageY: clientY,
  }
  const entry = {
    input,
    dropTargets: [{ element: document.body, data: { prksTarget: 'strip' }, dropEffect: 'move' as const, isActiveDueToStickiness: false }],
  }
  return { initial: entry, previous: entry, current: entry }
}

function sourcePayload(tabId: string, kind: 'tab' | 'pane' = 'tab') {
  return {
    data: { type: 'prks-workspace-poc', source: { kind, tabId } },
    element: document.body,
    dragHandle: null,
  }
}

describe('PoC adapter Pragmatic monitor lifecycle', () => {
  let session: ReturnType<typeof bindPocAdapter> | null = null
  let onSessionEnd: ReturnType<typeof vi.fn>
  let reorderTab: ReturnType<typeof vi.fn>
  let movePane: ReturnType<typeof vi.fn>

  beforeEach(() => {
    monitorCapture.args = null
    document.body.innerHTML = ''
    const tabs = document.createElement('div')
    tabs.id = 'prks-workspace-tabs'
    const a = document.createElement('div')
    a.className = 'prks-workspace-tab'
    a.setAttribute('data-tab-id', 'A')
    const b = document.createElement('div')
    b.className = 'prks-workspace-tab'
    b.setAttribute('data-tab-id', 'B')
    const activate = document.createElement('button')
    activate.className = 'prks-workspace-tab__activate'
    b.appendChild(activate)
    tabs.append(a, b)
    const page = document.createElement('div')
    page.id = 'page-content'
    const tile = document.createElement('div')
    tile.className = 'prks-tile'
    tile.setAttribute('data-prks-tab-id', 'B')
    page.appendChild(tile)
    const live = document.createElement('div')
    live.id = 'prks-workspace-live'
    document.body.append(tabs, page, live)

    // Geometry for strip hit resolution
    tabs.getBoundingClientRect = () =>
      ({ left: 0, top: 0, right: 200, bottom: 40, width: 200, height: 40, x: 0, y: 0, toJSON() {} }) as DOMRect
    a.getBoundingClientRect = () =>
      ({ left: 0, top: 0, right: 100, bottom: 40, width: 100, height: 40, x: 0, y: 0, toJSON() {} }) as DOMRect
    b.getBoundingClientRect = () =>
      ({ left: 100, top: 0, right: 200, bottom: 40, width: 100, height: 40, x: 100, y: 0, toJSON() {} }) as DOMRect

    onSessionEnd = vi.fn()
    reorderTab = vi.fn(() => true)
    movePane = vi.fn(() => true)
    session = bindPocAdapter({
      getSnapshot: () => ({
        mainTabId: 'A',
        secondaryLeafTabIds: ['B'],
        hasSecondaryTree: true,
        narrowFallback: false,
        canAddSecondaryLeaf: true,
        tabs: [
          { id: 'A', route: '#/folders', title: 'Main' },
          { id: 'B', route: '#/folders', title: 'Left' },
        ],
      }),
      routeSupportsTile: () => true,
      observeDom: false,
      hover: createHoverController(),
      onSessionEnd: onSessionEnd as (reason: 'drop' | 'cancel') => void,
      handlers: {
        reorderTab: reorderTab as (tabId: string, beforeTabId: string | null) => boolean,
        hideLeaf: vi.fn(() => true),
        tileTab: vi.fn(() => true),
        movePane: movePane as (
          sourceTabId: string,
          targetTabId: string,
          axis: string,
          placement: string,
        ) => boolean,
        splitLeaf: vi.fn(() => true),
      },
    })
    expect(monitorCapture.args).toBeTruthy()
  })

  afterEach(() => {
    session?.destroy()
    session = null
    document.body.innerHTML = ''
    document.body.classList.remove('prks-workspace-dragging')
    delete window.prksWorkspaceCancelActiveDrag
    monitorCapture.args = null
  })

  it('start → drag → drop commits once through monitor callbacks', async () => {
    const monitor = monitorCapture.args!
    const src = sourcePayload('B')
    monitor.onDragStart?.({
      source: src as ElementDragType['payload'],
      location: locationAt(150, 20),
    } as never)
    expect(session?.active).toBe(true)
    expect(document.body.classList.contains('prks-workspace-dragging')).toBe(true)

    monitor.onDrag?.({
      source: src as ElementDragType['payload'],
      location: locationAt(20, 20),
    } as never)
    expect(document.getElementById('prks-dnd-poc-insertion-marker')).toBeTruthy()
    expect(document.getElementById('prks-workspace-live')?.textContent).toMatch(/Move tab/)

    monitor.onDrop?.({
      source: src as ElementDragType['payload'],
      location: locationAt(20, 20),
    } as never)
    await Promise.resolve()
    expect(reorderTab).toHaveBeenCalledTimes(1)
    expect(reorderTab).toHaveBeenCalledWith('B', 'A')
    expect(onSessionEnd).toHaveBeenCalledTimes(1)
    expect(onSessionEnd).toHaveBeenCalledWith('drop')
    expect(session?.active).toBe(false)
    expect(document.getElementById('prks-dnd-poc-insertion-marker')).toBeNull()
  })

  it('start → cancel → drop is idempotent (single onSessionEnd, no commit)', async () => {
    const monitor = monitorCapture.args!
    const src = sourcePayload('B')
    monitor.onDragStart?.({
      source: src as ElementDragType['payload'],
      location: locationAt(150, 20),
    } as never)
    monitor.onDrag?.({
      source: src as ElementDragType['payload'],
      location: locationAt(20, 20),
    } as never)
    expect(document.getElementById('prks-dnd-poc-insertion-marker')).toBeTruthy()

    session!.cancel()
    expect(onSessionEnd).toHaveBeenCalledTimes(1)
    expect(onSessionEnd).toHaveBeenCalledWith('cancel')
    expect(session?.active).toBe(false)
    expect(document.getElementById('prks-dnd-poc-insertion-marker')).toBeNull()

    // Pragmatic still fires onDrop after cancel — must not double-end or commit.
    monitor.onDrop?.({
      source: src as ElementDragType['payload'],
      location: locationAt(20, 20),
    } as never)
    await Promise.resolve()
    expect(onSessionEnd).toHaveBeenCalledTimes(1)
    expect(reorderTab).not.toHaveBeenCalled()
    expect(movePane).not.toHaveBeenCalled()
  })

  it('refresh() rebinds without touching production shell commit hooks', () => {
    const win = window as Window & { prksWorkspaceOnShellCommit?: unknown }
    const prior = win.prksWorkspaceOnShellCommit
    win.prksWorkspaceOnShellCommit = vi.fn()
    try {
      session!.refresh()
      expect(win.prksWorkspaceOnShellCommit).not.toHaveBeenCalled()
      expect(typeof session!.reconcile).toBe('function')
    } finally {
      win.prksWorkspaceOnShellCommit = prior
    }
  })
})
