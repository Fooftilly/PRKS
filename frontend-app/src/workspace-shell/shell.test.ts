import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import hostSource from '../../../frontend/js/workspace-hosts.js?raw'
import WorkspaceShell from './WorkspaceShell.vue'
import type { ProjectionNode, ProjectionTab, WorkspaceIntents, WorkspaceProjection } from './types'

function installHosts(): void {
  const run = new Function(hostSource)
  run()
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
    mode: 'stacked' as const,
    mainTabId: 'A',
    focusedTabId: 'A',
    secondaryTree: null as ProjectionNode | null,
    tabs: [tab('A')],
    mainSplitRatio: 0.58,
    ...(overrides.state ?? {}),
  }
  return {
    visualTiled: false,
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

describe('Vue workspace shell', () => {
  let wrapper: VueWrapper | null = null

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  function mountShell(value: WorkspaceProjection, actions = intents()) {
    installHosts()
    const tabs = document.createElement('div')
    tabs.id = 'prks-workspace-tabs'
    const page = document.createElement('div')
    page.id = 'page-content'
    document.body.append(tabs, page)
    wrapper = mount(WorkspaceShell, {
      attachTo: document.body,
      props: { projection: value, intents: actions },
    })
    return { actions, tabs, page }
  }

  function host(id: string): HTMLElement | null {
    return document.querySelector('[data-prks-content-host="' + id + '"]')
  }

  function mark(id: string): HTMLElement {
    const node = host(id)
    if (!node) throw new Error('missing host ' + id)
    const sentinel = document.createElement('span')
    sentinel.dataset.sentinel = id
    node.appendChild(sentinel)
    return node
  }

  it('shows only Main when stacked, including a parked tab without a pane host', async () => {
    const tree = leaf('B')
    const value = projection({
      visualTiled: false,
      state: {
        version: 1,
        mode: 'stacked',
        mainTabId: 'A',
        focusedTabId: 'A',
        secondaryTree: tree,
        tabs: [tab('A', 'Main'), tab('B', 'Parked'), tab('C', 'Also parked')],
        mainSplitRatio: 0.58,
      },
    })
    mountShell(value)
    await nextTick()
    const canvas = document.querySelector('.prks-workspace-canvas')
    expect(canvas?.classList.contains('prks-workspace-canvas--stacked')).toBe(true)
    expect(canvas?.querySelectorAll('.prks-tile').length).toBe(1)
    expect(canvas?.querySelector('[data-prks-tab-id="A"]')).not.toBeNull()
    expect(canvas?.querySelector('[data-prks-tab-id="B"]')).toBeNull()
    expect(host('A')).not.toBeNull()
    expect(host('B')).toBeNull()
    expect(host('C')).toBeNull()
    expect(document.querySelectorAll('#prks-workspace-tabs .prks-workspace-tab').length).toBe(3)
    expect(document.querySelector('[data-tab-id="C"]')?.classList.contains('is-parked')).toBe(true)
    expect(value.state.secondaryTree).toBe(tree)
  })

  it('renders a tiled recursive Secondary tree and Main versus focused Secondary', async () => {
    const tree = split('split-root', 'top-bottom', leaf('B'), split('split-inner', 'left-right', leaf('C'), leaf('D')))
    const value = projection({
      visualTiled: true,
      state: {
        version: 1,
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: 'D',
        secondaryTree: tree,
        tabs: [tab('A', 'Work A'), tab('B', 'Work B'), tab('C', 'Work C'), tab('D', 'Work D')],
        mainSplitRatio: 0.58,
      },
    })
    mountShell(value)
    await nextTick()
    const canvas = document.querySelector('.prks-workspace-canvas') as HTMLElement
    const tileA = canvas.querySelector('[data-prks-tab-id="A"]')
    const tileD = canvas.querySelector('[data-prks-tab-id="D"]')
    const outer = canvas.querySelector('[data-prks-split-id="split-root"]')
    const inner = canvas.querySelector('[data-prks-split-id="split-inner"]')
    expect(tileA?.parentElement).toBe(canvas)
    expect(outer?.getAttribute('data-prks-secondary-root')).toBe('1')
    expect(outer?.getAttribute('data-prks-axis')).toBe('top-bottom')
    expect(inner?.getAttribute('data-prks-axis')).toBe('left-right')
    expect(inner?.parentElement).toBe(outer)
    expect(canvas.querySelector('[data-prks-tab-id="C"]')?.parentElement).toBe(inner)
    expect(tileD?.parentElement).toBe(inner)
    expect(tileA?.classList.contains('prks-tile--main')).toBe(true)
    expect(tileA?.classList.contains('prks-tile--focused')).toBe(false)
    expect(tileD?.classList.contains('prks-tile--secondary')).toBe(true)
    expect(tileD?.classList.contains('prks-tile--focused')).toBe(true)
    expect(tileA?.querySelector('.prks-tile-header__grip')).toBeNull()
    expect(tileA?.querySelector('.prks-tile-header__menu')).toBeNull()
    expect(tileA?.getAttribute('aria-label')).toBe('Main pane: Work A')
    const menu = canvas.querySelector('[data-prks-tab-id="B"] .prks-tile-header__menu')
    expect(menu?.getAttribute('aria-label')).toBe('Pane actions')
    expect(canvas.querySelector('[data-prks-tab-id="B"] .prks-tile-header__close')).not.toBeNull()
    expect(canvas.querySelector('.prks-splitter')).not.toBeNull()
    expect(document.querySelector('[data-tab-id="B"]')?.classList.contains('is-tiled')).toBe(true)
    expect(document.querySelector('[data-tab-id="D"]')?.classList.contains('is-focused')).toBe(true)
  })

  it('keeps content hosts across focus, ratio, reorder, and unrelated navigation', async () => {
    const tree = split('split-1', 'left-right', leaf('B'), leaf('C'))
    const initial = projection({
      visualTiled: true,
      state: {
        version: 1,
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: 'A',
        secondaryTree: tree,
        tabs: [tab('A', 'A'), tab('B', 'B'), tab('C', 'C')],
        mainSplitRatio: 0.58,
      },
    })
    mountShell(initial)
    await nextTick()
    const hostA = mark('A')
    const hostB = mark('B')
    const sentinelA = hostA.querySelector('[data-sentinel="A"]')
    const reorderedTabs = [tab('C', 'C'), tab('A', 'A renamed'), tab('B', 'B')]
    const reorderedTree = split('split-1', 'left-right', leaf('C'), leaf('B'), 0.42)
    await wrapper?.setProps({
      projection: projection({
        visualTiled: true,
        state: {
          version: 1,
          mode: 'tiled',
          mainTabId: 'A',
          focusedTabId: 'B',
          secondaryTree: reorderedTree,
          tabs: reorderedTabs,
          mainSplitRatio: 0.7,
        },
      }),
    })
    await nextTick()
    expect(host('A')).toBe(hostA)
    expect(host('B')).toBe(hostB)
    expect(hostA.querySelector('[data-sentinel="A"]')).toBe(sentinelA)
    expect(document.querySelector('.prks-workspace-tab__title')?.textContent).toBe('C')
    const dragged = document.querySelector('[data-prks-tab-id="B"]') as HTMLElement
    dragged.classList.add('is-drag-source')
    await wrapper?.setProps({
      projection: projection({
        visualTiled: true,
        state: {
          version: 1,
          mode: 'tiled',
          mainTabId: 'A',
          focusedTabId: 'B',
          secondaryTree: reorderedTree,
          tabs: [tab('C', 'C'), tab('A', 'A route', '#/people/1'), tab('B', 'B')],
          mainSplitRatio: 0.7,
        },
      }),
    })
    await nextTick()
    expect(host('A')).toBe(hostA)
    expect(host('B')).toBe(hostB)
    expect(dragged.classList.contains('is-drag-source')).toBe(true)
  })

  it('removes exactly the closed tab host', async () => {
    const value = projection({
      visualTiled: true,
      state: {
        version: 1,
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: 'A',
        secondaryTree: leaf('B'),
        tabs: [tab('A'), tab('B')],
        mainSplitRatio: 0.58,
      },
    })
    mountShell(value)
    await nextTick()
    const hostA = mark('A')
    const hostB = host('B')
    expect(hostB).not.toBeNull()
    await wrapper?.setProps({
      projection: projection({
        visualTiled: false,
        state: {
          version: 1,
          mode: 'stacked',
          mainTabId: 'A',
          focusedTabId: 'A',
          secondaryTree: null,
          tabs: [tab('A')],
          mainSplitRatio: 0.58,
        },
      }),
    })
    await nextTick()
    expect(host('B')).toBeNull()
    expect(window.prksWorkspaceContentHostIds?.()).not.toContain('B')
    expect(host('A')).toBe(hostA)
    expect(document.querySelector('[data-prks-tab-id="B"]')).toBeNull()
  })

  it('hides Secondary hosts without mutating the tree and shows them again', async () => {
    const tree = split('split-1', 'left-right', leaf('B'), leaf('C'), 0.33)
    const shown = projection({
      visualTiled: true,
      narrowFallback: false,
      state: {
        version: 1,
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: 'A',
        secondaryTree: tree,
        tabs: [tab('A'), tab('B'), tab('C')],
        mainSplitRatio: 0.58,
      },
    })
    mountShell(shown)
    await nextTick()
    const hostA = mark('A')
    const hostB = host('B')
    const hostC = host('C')
    expect(hostB).not.toBeNull()
    const hidden = projection({
      visualTiled: false,
      narrowFallback: true,
      state: shown.state,
    })
    await wrapper?.setProps({ projection: hidden })
    await nextTick()
    expect(hidden.state.secondaryTree).toBe(tree)
    expect(host('B')).toBeNull()
    expect(host('C')).toBeNull()
    expect(window.prksWorkspaceContentHostIds?.()).toContain('B')
    expect(host('A')).toBe(hostA)
    expect(document.querySelector('.prks-workspace-split')).toBeNull()
    await wrapper?.setProps({ projection: shown })
    await nextTick()
    expect(shown.state.secondaryTree).toBe(tree)
    expect(host('A')).toBe(hostA)
    expect(host('B')).toBe(hostB)
    expect(host('C')).toBe(hostC)
    expect(document.querySelector('[data-prks-split-id="split-1"]')).not.toBeNull()
  })

  it('leaves the rendered snapshot unchanged when a command is rejected', async () => {
    const value = projection()
    const actions = intents()
    mountShell(value, actions)
    await nextTick()
    const hostA = mark('A')
    const button = document.querySelector('.prks-workspace-tab__close') as HTMLButtonElement
    await button.click()
    expect(actions.close).toHaveBeenCalledWith('A')
    expect(value.state.mainTabId).toBe('A')
    await wrapper?.setProps({ projection: value })
    await nextTick()
    expect(host('A')).toBe(hostA)
    expect(document.querySelector('[data-sentinel="A"]')).not.toBeNull()
  })

  it('does not mutate a read-only snapshot', async () => {
    const value = projection({
      visualTiled: true,
      state: {
        version: 1,
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: 'B',
        secondaryTree: leaf('B'),
        tabs: [tab('A'), tab('B', 'Secondary')],
        mainSplitRatio: 0.4,
      },
    })
    const frozen = Object.freeze({
      ...value,
      state: Object.freeze({ ...value.state, tabs: Object.freeze(value.state.tabs.map((item) => Object.freeze({ ...item }))) }),
    })
    const actions = intents()
    mountShell(frozen, actions)
    await nextTick()
    const activate = document.querySelector('[data-tab-id="B"] .prks-workspace-tab__activate') as HTMLButtonElement
    await activate.click()
    expect(actions.activate).toHaveBeenCalledWith('B')
    expect(frozen.state.mainTabId).toBe('A')
    expect(frozen.state.focusedTabId).toBe('B')
    expect(() => {
      ;(frozen.state as { mainTabId: string }).mainTabId = 'B'
    }).toThrow()
  })
})
