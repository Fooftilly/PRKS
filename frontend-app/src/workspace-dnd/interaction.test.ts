/**
 * Focused Pragmatic interaction tests on Vue WorkspaceShell DOM (#256).
 *
 * Uses the official Pragmatic jsdom DragEvent polyfill harness so adapter
 * monitor/draggable/drop-target callbacks run. Not a full production E2E suite.
 */
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import hostSource from '../../../frontend/js/workspace-hosts.js?raw'
import WorkspaceShell from '../workspace-shell/WorkspaceShell.vue'
import type { ProjectionNode, ProjectionTab, WorkspaceIntents, WorkspaceProjection } from '../workspace-shell/types'
import { bindWorkspaceDnd, type WorkspaceDndSnapshot } from './adapter'
import {
  dragEnd,
  dropOn,
  fireDrag,
  flushAnimationFrames,
  installRafController,
  mockClientRect,
  restoreRafController,
  startDragOver,
} from './pragmatic-harness'

function installHosts(): void {
  new Function(hostSource)()
}

function tab(id: string, title = 'Work ' + id, route = '#/folders'): ProjectionTab {
  return { id, route, title, icon: 'file-text' }
}

function leaf(tabId: string): ProjectionNode {
  return { type: 'leaf', tabId }
}

function split(
  id: string,
  axis: 'left-right' | 'top-bottom',
  first: ProjectionNode,
  second: ProjectionNode,
  ratio = 0.5,
): ProjectionNode {
  return { type: 'split', id, axis, ratio, first, second }
}

function projection(overrides: Partial<WorkspaceProjection> = {}): WorkspaceProjection {
  const state = {
    version: 1,
    mode: 'tiled' as const,
    mainTabId: 'A',
    focusedTabId: 'B',
    secondaryTree: split('s1', 'left-right', leaf('B'), leaf('C')),
    tabs: [tab('A', 'Main'), tab('B', 'Left'), tab('C', 'Right'), tab('D', 'Parked')],
    mainSplitRatio: 0.58,
    ...(overrides.state ?? {}),
  }
  return {
    visualTiled: true,
    narrowFallback: false,
    tabStatus: {},
    ...overrides,
    state,
  }
}

function intents(): WorkspaceIntents {
  return {
    activate: vi.fn(),
    close: vi.fn(),
    focus: vi.fn(),
    tile: vi.fn(),
    openTabMenu: vi.fn(),
    setMainRatio: vi.fn(),
    setNestedRatio: vi.fn(),
  }
}

describe('workspace-dnd Pragmatic interaction on Vue WorkspaceShell DOM', () => {
  let wrapper: VueWrapper | null = null
  let session: ReturnType<typeof bindWorkspaceDnd> | null = null
  let snap: WorkspaceDndSnapshot
  let value: WorkspaceProjection

  beforeEach(() => {
    installRafController()
  })

  afterEach(() => {
    // End any native Pragmatic drag left active (cancel alone does not fire dragend).
    try {
      dragEnd(window)
    } catch {
      /* ignore */
    }
    session?.destroy()
    session = null
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
    document.body.classList.remove('prks-workspace-dragging')
    restoreRafController()
    delete window.prksWorkspaceCancelActiveDrag
  })

  async function mountShell(proj = projection()) {
    installHosts()
    const tabs = document.createElement('div')
    tabs.id = 'prks-workspace-tabs'
    const page = document.createElement('div')
    page.id = 'page-content'
    const live = document.createElement('div')
    live.id = 'prks-workspace-live'
    document.body.append(tabs, page, live)
    value = proj
    wrapper = mount(WorkspaceShell, {
      attachTo: document.body,
      props: { projection: value, intents: intents() },
    })
    await nextTick()
    layoutShell()
    snap = {
      mainTabId: value.state.mainTabId,
      secondaryLeafTabIds: collectLeaves(value.state.secondaryTree),
      hasSecondaryTree: value.state.secondaryTree != null,
      narrowFallback: value.narrowFallback,
      canAddSecondaryLeaf: true,
      tabs: value.state.tabs.map((t) => ({ id: t.id, route: t.route, title: t.title })),
    }
    return value
  }

  function collectLeaves(node: ProjectionNode | null): string[] {
    if (!node) return []
    if (node.type === 'leaf') return [node.tabId]
    return collectLeaves(node.first).concat(collectLeaves(node.second))
  }

  function layoutShell(): void {
    const strip = document.getElementById('prks-workspace-tabs')!
    strip.style.overflowX = 'auto'
    strip.style.overflowY = 'hidden'
    mockClientRect(strip, { left: 0, top: 0, width: 400, height: 40 })
    Object.defineProperty(strip, 'scrollWidth', { configurable: true, get: () => 800 })
    Object.defineProperty(strip, 'clientWidth', { configurable: true, get: () => 400 })
    Object.defineProperty(strip, 'scrollHeight', { configurable: true, get: () => 40 })
    Object.defineProperty(strip, 'clientHeight', { configurable: true, get: () => 40 })
    strip.scrollLeft = 0

    const tabEls = Array.from(document.querySelectorAll('.prks-workspace-tab'))
    tabEls.forEach((el, i) => {
      mockClientRect(el, { left: i * 100, top: 0, width: 100, height: 40 })
      const activate = el.querySelector('.prks-workspace-tab__activate')
      if (activate) mockClientRect(activate, { left: i * 100, top: 0, width: 80, height: 40 })
    })

    const tileB = document.querySelector('.prks-tile[data-prks-tab-id="B"]') as HTMLElement | null
    const tileC = document.querySelector('.prks-tile[data-prks-tab-id="C"]') as HTMLElement | null
    if (tileB) {
      mockClientRect(tileB, { left: 0, top: 50, width: 200, height: 200 })
      const grip = tileB.querySelector('.prks-tile-header__grip')
      if (grip) mockClientRect(grip, { left: 4, top: 54, width: 24, height: 24 })
    }
    if (tileC) {
      mockClientRect(tileC, { left: 200, top: 50, width: 200, height: 200 })
      const grip = tileC.querySelector('.prks-tile-header__grip')
      if (grip) mockClientRect(grip, { left: 204, top: 54, width: 24, height: 24 })
    }

    const canvas = document.querySelector('.prks-workspace-canvas')
    if (canvas) mockClientRect(canvas, { left: 0, top: 50, width: 400, height: 200 })
  }

  function handlers() {
    return {
      reorderTab: vi.fn<(tabId: string, beforeTabId: string | null) => boolean>(() => true),
      hideLeaf: vi.fn<(tabId: string) => boolean>(() => true),
      tileTab: vi.fn<(tabId: string) => boolean>(() => true),
      movePane: vi.fn<
        (sourceTabId: string, targetTabId: string, axis: string, placement: string) => boolean
      >(() => true),
      splitLeaf: vi.fn<
        (
          targetTabId: string,
          axis: string,
          options: { tabId: string; placement: string },
        ) => boolean
      >(() => true),
    }
  }

  type TestHandlers = ReturnType<typeof handlers>

  function bind(h: TestHandlers) {
    session = bindWorkspaceDnd({
      getSnapshot: () => snap,
      routeSupportsTile: () => true,
      observeDom: false,
      handlers: h,
    })
    return session
  }

  it('1. tab drag/reorder through Pragmatic reaches reorder once', async () => {
    await mountShell()
    const h = handlers()
    bind(h)
    const source = document.querySelector('.prks-workspace-tab[data-tab-id="D"]') as HTMLElement
    const strip = document.getElementById('prks-workspace-tabs')!
    // Start over D's activate handle (dragHandle check); drop near start → before A
    startDragOver(source, strip, 320, 20)
    expect(session?.active).toBe(true)
    expect(document.body.classList.contains('prks-workspace-dragging')).toBe(true)
    // Move over the strip insertion point, then drop (final coords authoritative)
    fireDrag('dragover', strip, { clientX: 20, clientY: 20 })
    flushAnimationFrames(1)
    dropOn(strip, 20, 20)
    await Promise.resolve()
    expect(h.reorderTab).toHaveBeenCalledTimes(1)
    expect(h.reorderTab).toHaveBeenCalledWith('D', 'A')
    expect(h.movePane).not.toHaveBeenCalled()
    expect(document.body.classList.contains('prks-workspace-dragging')).toBe(false)
  })

  it('2. pane edge move through nested tile target reaches movePane once', async () => {
    await mountShell()
    const h = handlers()
    bind(h)
    const source = document.querySelector('.prks-tile[data-prks-tab-id="B"]') as HTMLElement
    const target = document.querySelector('.prks-tile[data-prks-tab-id="C"]') as HTMLElement
    // Start on B's grip (dragHandle), then over left edge of C
    startDragOver(source, target, 10, 60)
    fireDrag('dragover', target, { clientX: 210, clientY: 150 })
    flushAnimationFrames(1)
    dropOn(target, 210, 150)
    await Promise.resolve()
    expect(h.movePane).toHaveBeenCalledTimes(1)
    expect(h.movePane).toHaveBeenCalledWith('B', 'C', 'left-right', 'first')
    expect(h.reorderTab).not.toHaveBeenCalled()
  })

  it('3. final drop position is authoritative over a stale hover target', async () => {
    await mountShell()
    const h = handlers()
    bind(h)
    const source = document.querySelector('.prks-workspace-tab[data-tab-id="D"]') as HTMLElement
    const strip = document.getElementById('prks-workspace-tabs')!
    startDragOver(source, strip, 320, 20)
    fireDrag('dragover', strip, { clientX: 20, clientY: 20 })
    flushAnimationFrames(1)
    expect(session?.intent?.kind).toBe('tab-reorder')
    // Leave strip: drag over empty page area (no drop target), then drop there.
    fireDrag('dragenter', document.body, { clientX: 10, clientY: 400 })
    fireDrag('dragover', document.body, { clientX: 10, clientY: 400 })
    flushAnimationFrames(1)
    dropOn(document.body as unknown as HTMLElement, 10, 400)
    await Promise.resolve()
    expect(h.reorderTab).not.toHaveBeenCalled()
    expect(h.movePane).not.toHaveBeenCalled()
    expect(document.getElementById('prks-workspace-dnd-insertion-marker')).toBeNull()
  })

  it('3b. dragEnd without drop commits nothing (native cancel path)', async () => {
    await mountShell()
    const h = handlers()
    bind(h)
    const source = document.querySelector('.prks-workspace-tab[data-tab-id="D"]') as HTMLElement
    const strip = document.getElementById('prks-workspace-tabs')!
    startDragOver(source, strip, 320, 20)
    fireDrag('dragover', strip, { clientX: 20, clientY: 20 })
    flushAnimationFrames(1)
    expect(session?.active).toBe(true)
    // End the native drag from the dragged element (no drop) — no coordinator commit.
    dragEnd(source)
    await Promise.resolve()
    expect(h.reorderTab).not.toHaveBeenCalled()
    expect(h.movePane).not.toHaveBeenCalled()
    expect(h.hideLeaf).not.toHaveBeenCalled()
    expect(h.splitLeaf).not.toHaveBeenCalled()
    expect(h.tileTab).not.toHaveBeenCalled()
    expect(document.getElementById('prks-workspace-dnd-insertion-marker')).toBeNull()
    expect(document.body.classList.contains('prks-workspace-dragging')).toBe(false)
  })

  it('4. tab added after mount becomes draggable after reconcile', async () => {
    await mountShell(
      projection({
        state: {
          version: 1,
          mode: 'tiled',
          mainTabId: 'A',
          focusedTabId: 'B',
          secondaryTree: split('s1', 'left-right', leaf('B'), leaf('C')),
          tabs: [tab('A', 'Main'), tab('B', 'Left'), tab('C', 'Right')],
          mainSplitRatio: 0.58,
        },
      }),
    )
    const h = handlers()
    bind(h)
    expect(document.querySelector('.prks-workspace-tab[data-tab-id="E"]')).toBeNull()

    const next = projection({
      state: {
        version: 1,
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: 'B',
        secondaryTree: split('s1', 'left-right', leaf('B'), leaf('C')),
        tabs: [tab('A', 'Main'), tab('B', 'Left'), tab('C', 'Right'), tab('E', 'New')],
        mainSplitRatio: 0.58,
      },
    })
    await wrapper!.setProps({ projection: next })
    await nextTick()
    layoutShell()
    snap = {
      mainTabId: next.state.mainTabId,
      secondaryLeafTabIds: ['B', 'C'],
      hasSecondaryTree: true,
      narrowFallback: false,
      canAddSecondaryLeaf: true,
      tabs: next.state.tabs.map((t) => ({ id: t.id, route: t.route, title: t.title })),
    }
    session!.reconcile()

    const source = document.querySelector('.prks-workspace-tab[data-tab-id="E"]') as HTMLElement
    expect(source).toBeTruthy()
    const strip = document.getElementById('prks-workspace-tabs')!
    // Drop near strip start (not E's current end slot — that is a rejected self-drop).
    startDragOver(source, strip, 320, 20)
    fireDrag('dragover', strip, { clientX: 20, clientY: 20 })
    flushAnimationFrames(1)
    dropOn(strip, 20, 20)
    await Promise.resolve()
    expect(h.reorderTab).toHaveBeenCalledTimes(1)
    expect(h.reorderTab.mock.calls[0][0]).toBe('E')
    expect(h.reorderTab).toHaveBeenCalledWith('E', 'A')
  })

  it('5. prksWorkspaceCancelActiveDrag cancels with no leftover overlays/state', async () => {
    await mountShell()
    const h = handlers()
    bind(h)
    const source = document.querySelector('.prks-workspace-tab[data-tab-id="D"]') as HTMLElement
    const strip = document.getElementById('prks-workspace-tabs')!
    startDragOver(source, strip, 320, 20)
    fireDrag('dragover', strip, { clientX: 20, clientY: 20 })
    flushAnimationFrames(1)
    expect(session?.active).toBe(true)
    expect(document.getElementById('prks-workspace-dnd-insertion-marker')).toBeTruthy()

    window.prksWorkspaceCancelActiveDrag?.()
    dragEnd(window)
    expect(session?.active).toBe(false)
    expect(document.getElementById('prks-workspace-dnd-insertion-marker')).toBeNull()
    expect(document.body.classList.contains('prks-workspace-dragging')).toBe(false)
    expect(h.reorderTab).not.toHaveBeenCalled()

    // Source removal during drag also cancels via reconcile
    const source2 = document.querySelector('.prks-workspace-tab[data-tab-id="C"]') as HTMLElement
    startDragOver(source2, strip, 220, 20)
    fireDrag('dragover', strip, { clientX: 20, clientY: 20 })
    flushAnimationFrames(1)
    source2.remove()
    session!.reconcile()
    expect(session?.active).toBe(false)
    expect(document.getElementById('prks-workspace-dnd-insertion-marker')).toBeNull()
    dragEnd(window)
  })

  it('6. tab-strip edge autoscroll engages under the Pragmatic adapter', async () => {
    await mountShell()
    const h = handlers()
    bind(h)
    const source = document.querySelector('.prks-workspace-tab[data-tab-id="D"]') as HTMLElement
    const strip = document.getElementById('prks-workspace-tabs')!
    expect(strip.scrollWidth).toBeGreaterThan(strip.clientWidth)
    const before = strip.scrollLeft
    // Near the right edge of the strip hitbox
    startDragOver(source, strip, 320, 20)
    // Hold near the right edge so auto-scroll engages
    for (let i = 0; i < 12; i++) {
      fireDrag('dragover', strip, { clientX: 395, clientY: 20 })
      flushAnimationFrames(1)
    }
    expect(strip.scrollLeft).toBeGreaterThan(before)
    window.prksWorkspaceCancelActiveDrag?.()
    dragEnd(window)
    expect(h.reorderTab).not.toHaveBeenCalled()
  })

  it('7. confirmed drop hits the coordinator API exactly once', async () => {
    await mountShell()
    const h = handlers()
    bind(h)
    const source = document.querySelector('.prks-tile[data-prks-tab-id="B"]') as HTMLElement
    const target = document.querySelector('.prks-tile[data-prks-tab-id="C"]') as HTMLElement
    startDragOver(source, target, 10, 60)
    fireDrag('dragover', target, { clientX: 380, clientY: 150 })
    flushAnimationFrames(1)
    dropOn(target, 380, 150)
    await Promise.resolve()
    expect(h.movePane).toHaveBeenCalledTimes(1)
    expect(h.splitLeaf).not.toHaveBeenCalled()
    expect(h.reorderTab).not.toHaveBeenCalled()
    expect(h.hideLeaf).not.toHaveBeenCalled()
  })
})
