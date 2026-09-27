import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import hostSource from '../../../frontend/js/workspace-hosts.js?raw'
import WorkspaceShell from '../workspace-shell/WorkspaceShell.vue'
import type { ProjectionNode, ProjectionTab, WorkspaceIntents, WorkspaceProjection } from '../workspace-shell/types'
import { bindPocAdapter, type PocSnapshot } from './adapter'
import { commitDropIntent } from './commit'
import { createHoverController } from './hover'
import { resolveDropIntent } from './drop-intent'

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

describe('pragmatic dnd poc adapter on Vue WorkspaceShell DOM', () => {
  let wrapper: VueWrapper | null = null
  let session: ReturnType<typeof bindPocAdapter> | null = null

  afterEach(() => {
    session?.destroy()
    session = null
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
    document.body.classList.remove('prks-workspace-dragging')
  })

  async function mountTiledShell() {
    installHosts()
    const tabs = document.createElement('div')
    tabs.id = 'prks-workspace-tabs'
    const page = document.createElement('div')
    page.id = 'page-content'
    const live = document.createElement('div')
    live.id = 'prks-workspace-live'
    document.body.append(tabs, page, live)
    const value = projection()
    wrapper = mount(WorkspaceShell, {
      attachTo: document.body,
      props: { projection: value, intents: intents() },
    })
    await nextTick()
    return value
  }

  function snapshotFrom(value: WorkspaceProjection): PocSnapshot {
    return {
      mainTabId: value.state.mainTabId,
      secondaryLeafTabIds: ['B', 'C'],
      hasSecondaryTree: true,
      narrowFallback: false,
      canAddSecondaryLeaf: true,
      tabs: value.state.tabs.map((t) => ({ id: t.id, route: t.route, title: t.title })),
    }
  }

  it('binds to real Vue tab/pane hosts and cleans up on destroy', async () => {
    const value = await mountTiledShell()
    expect(document.querySelectorAll('.prks-workspace-tab').length).toBe(4)
    expect(document.querySelectorAll('.prks-tile-header__grip').length).toBe(2)

    const hover = createHoverController()
    session = bindPocAdapter({
      getSnapshot: () => snapshotFrom(value),
      routeSupportsTile: () => true,
      hover,
      handlers: {
        reorderTab: vi.fn(() => true),
        hideLeaf: vi.fn(() => true),
        tileTab: vi.fn(() => true),
        movePane: vi.fn(() => true),
        splitLeaf: vi.fn(() => true),
      },
    })

    const list = document.getElementById('prks-workspace-tabs')!
    hover.showReorderMarker(list, 'C')
    expect(document.getElementById('prks-dnd-poc-insertion-marker')).toBeTruthy()

    session.destroy()
    session = null
    expect(document.getElementById('prks-dnd-poc-insertion-marker')).toBeNull()
    expect(document.body.classList.contains('prks-workspace-dragging')).toBe(false)
  })

  it('cancel clears ephemeral hover and never commits', async () => {
    const value = await mountTiledShell()
    const movePane = vi.fn(() => true)
    const hover = createHoverController()
    session = bindPocAdapter({
      getSnapshot: () => snapshotFrom(value),
      routeSupportsTile: () => true,
      hover,
      handlers: {
        reorderTab: vi.fn(() => true),
        hideLeaf: vi.fn(() => true),
        tileTab: vi.fn(() => true),
        movePane,
        splitLeaf: vi.fn(() => true),
      },
    })
    const tile = document.querySelector('.prks-tile[data-prks-tab-id="C"]') as HTMLElement
    hover.showEdgeOverlay(tile, 'left', true)
    expect(document.getElementById('prks-dnd-poc-edge-overlay')).toBeTruthy()
    session.cancel()
    expect(document.getElementById('prks-dnd-poc-edge-overlay')).toBeNull()
    expect(movePane).not.toHaveBeenCalled()
  })

  it('confirmed drop commits reorder/move through handlers only', async () => {
    const reorderTab = vi.fn(() => true)
    const movePane = vi.fn(() => true)
    const handlers = {
      reorderTab,
      hideLeaf: vi.fn(() => true),
      tileTab: vi.fn(() => true),
      movePane,
      splitLeaf: vi.fn(() => true),
    }
    const reorderIntent = resolveDropIntent({
      source: { kind: 'tab', tabId: 'D' },
      mainTabId: 'A',
      secondaryLeafTabIds: ['B', 'C'],
      hasSecondaryTree: true,
      narrowFallback: false,
      canAddSecondaryLeaf: true,
      sourceRouteSupportsTile: true,
      hit: {
        kind: 'strip',
        x: 10,
        otherTabRects: [
          { id: 'A', left: 0, right: 40 },
          { id: 'B', left: 40, right: 80 },
          { id: 'C', left: 80, right: 120 },
        ],
      },
    })
    await commitDropIntent({ kind: 'tab', tabId: 'D' }, reorderIntent, ['B', 'C'], handlers)
    expect(reorderTab).toHaveBeenCalledWith('D', 'A')

    const moveIntent = resolveDropIntent({
      source: { kind: 'pane', tabId: 'B' },
      mainTabId: 'A',
      secondaryLeafTabIds: ['B', 'C'],
      hasSecondaryTree: true,
      narrowFallback: false,
      canAddSecondaryLeaf: true,
      sourceRouteSupportsTile: true,
      hit: {
        kind: 'leaf',
        tabId: 'C',
        rect: { left: 0, top: 0, width: 200, height: 200 },
        x: 10,
        y: 100,
      },
    })
    await commitDropIntent({ kind: 'pane', tabId: 'B' }, moveIntent, ['B', 'C'], handlers)
    expect(movePane).toHaveBeenCalledWith('B', 'C', 'left-right', 'first')
  })

  it('documents parked→split via empty-secondary and edge split intents', async () => {
    const tileTab = vi.fn(() => true)
    const splitLeaf = vi.fn(() => true)
    const handlers = {
      reorderTab: vi.fn(() => true),
      hideLeaf: vi.fn(() => true),
      tileTab,
      movePane: vi.fn(() => true),
      splitLeaf,
    }
    const empty = resolveDropIntent({
      source: { kind: 'tab', tabId: 'D' },
      mainTabId: 'A',
      secondaryLeafTabIds: [],
      hasSecondaryTree: false,
      narrowFallback: false,
      canAddSecondaryLeaf: true,
      sourceRouteSupportsTile: true,
      hit: {
        kind: 'empty-secondary',
        canvasRect: { left: 0, top: 0, width: 1000, height: 400 },
        x: 700,
        y: 100,
      },
    })
    await commitDropIntent({ kind: 'tab', tabId: 'D' }, empty, [], handlers)
    expect(tileTab).toHaveBeenCalledWith('D')

    const edge = resolveDropIntent({
      source: { kind: 'tab', tabId: 'D' },
      mainTabId: 'A',
      secondaryLeafTabIds: ['B'],
      hasSecondaryTree: true,
      narrowFallback: false,
      canAddSecondaryLeaf: true,
      sourceRouteSupportsTile: true,
      hit: {
        kind: 'leaf',
        tabId: 'B',
        rect: { left: 0, top: 0, width: 200, height: 200 },
        x: 100,
        y: 10,
      },
    })
    await commitDropIntent({ kind: 'tab', tabId: 'D' }, edge, ['B'], handlers)
    expect(splitLeaf).toHaveBeenCalledWith('B', 'top-bottom', { tabId: 'D', placement: 'first' })
  })
})
