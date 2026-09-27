import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dismissProgress, presentProgress, registerProgressBridge, resetProgressSessionForTests, type ProgressOwner } from './session'

afterEach(() => {
  resetProgressSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentProgress
  delete window.prksVueDismissProgress
  delete window.prksSyncSidebarActive
  delete window.prksWorkCardHtml
})

function host(): HTMLElement {
  const el = document.createElement('div')
  el.setAttribute('data-prks-progress-host', 'true')
  document.body.appendChild(el)
  return el
}

function owner(): ProgressOwner {
  return {}
}

interface CleanupOwner extends ProgressOwner {
  beginRoute(): void
}

function cleanupOwner(): CleanupOwner {
  const cleanups = new Set<() => void>()
  return {
    registerCleanup(fn: () => void) {
      cleanups.add(fn)
    },
    beginRoute() {
      const fns = Array.from(cleanups)
      cleanups.clear()
      fns.forEach((fn) => fn())
    },
  }
}

describe('Progress session bridge', () => {
  it('syncs the sidebar through the existing navigation function', () => {
    const calls: { name?: string; canonicalHash?: string; status?: string }[] = []
    window.prksSyncSidebarActive = (route) => {
      calls.push({
        name: route.name,
        canonicalHash: route.canonicalHash,
        status: route.params?.status,
      })
    }
    window.prksWorkCardHtml = () => '<div data-work-id="x"></div>'
    const pane = owner()
    const el = host()
    presentProgress({
      owner: pane,
      host: el,
      status: 'Paused',
      rows: [{ id: 'x', title: 'X', status: 'Paused' }],
      offlineCached: false,
      generation: 4,
    })
    expect(calls).toEqual([
      { name: 'progress', canonicalHash: '#/progress?status=Paused', status: 'Paused' },
    ])
    expect(el.querySelector('.progress-filter')).toBeNull()
    presentProgress({
      owner: pane,
      host: el,
      status: 'Completed',
      rows: [],
      offlineCached: false,
      generation: 5,
    })
    expect(calls[1]).toEqual({
      name: 'progress',
      canonicalHash: '#/progress?status=Completed',
      status: 'Completed',
    })
  })

  it('does not fetch and does not repaint a dismissed generation', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
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
    dismissProgress(pane)
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

  it('keeps Main mounted when an unrelated Secondary route dismisses', () => {
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    const main = cleanupOwner()
    const secondary = cleanupOwner()
    const mainHost = host()
    const secondaryHost = host()
    const calls: string[] = []
    window.prksSyncSidebarActive = () => {
      calls.push('sidebar')
    }
    presentProgress({
      owner: main,
      host: mainHost,
      status: 'In Progress',
      rows: [{ id: 'main', title: 'Main', status: 'In Progress' }],
      generation: 4,
      shell: true,
    })
    presentProgress({
      owner: secondary,
      host: secondaryHost,
      status: 'Paused',
      rows: [{ id: 'side', title: 'Side', status: 'Paused' }],
      generation: 1,
      shell: false,
    })
    secondary.beginRoute()
    dismissProgress(secondary)
    expect(mainHost.querySelector('[data-work-id="main"]')).not.toBeNull()
    expect(secondaryHost.querySelector('[data-prks-progress-view]')).toBeNull()
    expect(calls).toEqual(['sidebar'])
  })

  it('accepts generation 1 on a new owner after an older owner reached a higher generation', () => {
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    const older = owner()
    const newer = owner()
    const olderHost = host()
    const newerHost = host()
    presentProgress({
      owner: older,
      host: olderHost,
      status: 'Completed',
      rows: [{ id: 'old', title: 'Old', status: 'Completed' }],
      generation: 8,
    })
    dismissProgress(older)
    presentProgress({
      owner: newer,
      host: newerHost,
      status: 'Planned',
      rows: [{ id: 'new', title: 'New', status: 'Planned' }],
      generation: 1,
    })
    expect(newerHost.querySelector('[data-work-id="new"]')).not.toBeNull()
    expect(newerHost.querySelector('.prks-page-title')?.textContent).toBe('Files · Planned')
    expect(olderHost.querySelector('[data-prks-progress-view]')).toBeNull()
  })

  it('dismisses only the owner that is leaving', () => {
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    const main = owner()
    const other = owner()
    const mainHost = host()
    const otherHost = host()
    presentProgress({
      owner: main,
      host: mainHost,
      status: 'Not Started',
      rows: [{ id: 'main', title: 'Main', status: 'Not Started' }],
      generation: 2,
    })
    presentProgress({
      owner: other,
      host: otherHost,
      status: 'Paused',
      rows: [{ id: 'other', title: 'Other', status: 'Paused' }],
      generation: 2,
    })
    dismissProgress(other)
    expect(otherHost.querySelector('[data-prks-progress-view]')).toBeNull()
    expect(mainHost.querySelector('[data-work-id="main"]')).not.toBeNull()
    dismissProgress(main)
    expect(mainHost.querySelector('[data-prks-progress-view]')).toBeNull()
  })

  it('rejects a stale generation only within the same owner', async () => {
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    const pane = owner()
    const el = host()
    presentProgress({
      owner: pane,
      host: el,
      status: 'Planned',
      rows: [{ id: 'current', title: 'Current', status: 'Planned' }],
      generation: 3,
    })
    presentProgress({
      owner: pane,
      host: el,
      status: 'Completed',
      rows: [{ id: 'stale', title: 'Stale', status: 'Completed' }],
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('[data-work-id="current"]')).not.toBeNull()
    expect(el.querySelector('[data-work-id="stale"]')).toBeNull()
  })

  it('follows the owning context lifecycle and does not fetch', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    const main = cleanupOwner()
    const secondary = cleanupOwner()
    const mainHost = host()
    presentProgress({
      owner: main,
      host: mainHost,
      status: 'In Progress',
      rows: [{ id: 'main', title: 'Main', status: 'In Progress' }],
      generation: 2,
    })
    secondary.beginRoute()
    expect(mainHost.querySelector('[data-work-id="main"]')).not.toBeNull()
    main.beginRoute()
    expect(mainHost.querySelector('[data-prks-progress-view]')).toBeNull()
    presentProgress({
      owner: main,
      host: mainHost,
      status: 'Completed',
      rows: [{ id: 'next', title: 'Next', status: 'Completed' }],
      generation: 1,
    })
    expect(mainHost.querySelector('[data-work-id="next"]')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('registers one bridge and applies a request stored on its host', () => {
    const el = host()
    const pane = owner()
    const target = window
    ;(el as HTMLElement & { __prksProgressPresentRequest?: object }).__prksProgressPresentRequest = {
      owner: pane,
      host: el,
      status: 'In Progress',
      rows: [{ id: 'early', title: 'Early', status: 'In Progress' }],
      offlineCached: false,
      generation: 1,
    }
    window.prksWorkCardHtml = (work) => `<div data-work-id="${String(work.id)}"></div>`
    registerProgressBridge(target)
    expect(target.prksVuePresentProgress).toBeTypeOf('function')
    expect(target.prksVueDismissProgress).toBeTypeOf('function')
    expect(
      (el as HTMLElement & { __prksProgressPresentRequest?: unknown }).__prksProgressPresentRequest,
    ).toBeUndefined()
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files · In Progress')
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
  })
})
