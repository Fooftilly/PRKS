import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dismissRouteSurface, readRouteSurface } from '../../route-surface/lifecycle'
import { presentProgress, registerProgressBridge, resetProgressSessionForTests } from './session'

afterEach(() => {
  resetProgressSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRoute
  delete (window as Window & { prksSyncSidebarActive?: unknown }).prksSyncSidebarActive
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner() {
  return {}
}

describe('Progress route bridge', () => {
  it('renders the effective rows and does not publish shell state', () => {
    const calls: string[] = []
    ;(window as Window & { prksSyncSidebarActive?: () => void }).prksSyncSidebarActive = () => {
      calls.push('sidebar')
    }
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentProgress({
      owner: main,
      host: mainHost,
      status: 'Paused',
      rows: [{ id: 'main', title: 'Main', status: 'Paused' }],
      offlineCached: false,
      generation: 4,
      shell: true,
    })
    presentProgress({
      owner: secondary,
      host: secondaryHost,
      status: 'Completed',
      rows: [{ id: 'side', title: 'Side', status: 'Completed' }],
      offlineCached: false,
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Files · Paused')
    expect(secondaryHost.querySelector('[data-work-id="side"]')).not.toBeNull()
    expect(mainHost.querySelector('.progress-filter')).toBeNull()
    expect(readRouteSurface(main)).toMatchObject({
      name: 'progress',
      canonicalHash: '#/progress?status=Paused',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(calls).toEqual([])
  })

  it('does not fetch and does not repaint a dismissed generation', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const pane = owner()
    const el = host()
    presentProgress({
      owner: pane,
      host: el,
      status: 'Planned',
      rows: [{ id: 'a', title: 'A', status: 'Planned' }],
      generation: 2,
    })
    expect(fetchMock).not.toHaveBeenCalled()
    dismissRouteSurface(pane)
    expect(el.querySelector('[data-prks-progress-view]')).toBeNull()
    presentProgress({
      owner: pane,
      host: el,
      status: 'Completed',
      rows: [{ id: 'b', title: 'B', status: 'Completed' }],
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('[data-work-id="b"]')).toBeNull()
    presentProgress({
      owner: pane,
      host: el,
      status: 'Completed',
      rows: [{ id: 'b', title: 'B', status: 'Completed' }],
      generation: 3,
    })
    expect(el.querySelector('[data-work-id="b"]')).not.toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('registers the progress bridge and applies a progress request stored on its host', () => {
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'progress',
      owner: pane,
      host: decoy,
      status: 'In Progress',
      rows: [{ id: 'early', title: 'Early', status: 'In Progress' }],
      offlineCached: false,
      generation: 1,
      shell: true,
    }
    registerProgressBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksVueDismissRoute).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files · In Progress')
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-progress-view]')).toBeNull()
    expect(readRouteSurface(pane)?.name).toBe('progress')
  })

  it('dismisses one owner through the bridge and leaves the other mounted', () => {
    registerProgressBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentRoute?.({
      feature: 'progress',
      owner: main,
      host: mainHost,
      status: 'Paused',
      rows: [{ id: 'main', title: 'Main', status: 'Paused' }],
      generation: 2,
      shell: true,
    })
    window.prksVuePresentRoute?.({
      feature: 'progress',
      owner: secondary,
      host: secondaryHost,
      status: 'Completed',
      rows: [{ id: 'side', title: 'Side', status: 'Completed' }],
      generation: 1,
      shell: false,
    })
    window.prksVueDismissRoute?.(secondary)
    expect(secondaryHost.querySelector('[data-work-id="side"]')).toBeNull()
    expect(mainHost.querySelector('[data-work-id="main"]')).not.toBeNull()
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Files · Paused')
  })
})
