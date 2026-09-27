import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dismissProgress, presentProgress, registerProgressBridge, resetProgressSessionForTests } from './session'

afterEach(() => {
  resetProgressSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentProgress
  delete window.prksVueDismissProgress
  delete window.__prksProgressPresentRequest
  delete window.prksSyncSidebarActive
  delete window.prksWorkCardHtml
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
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
    const el = host()
    presentProgress({
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
    const el = host()
    presentProgress({
      host: el,
      status: 'Planned',
      rows: [{ id: 'a', title: 'A', status: 'Planned' }],
      generation: 2,
    })
    expect(fetchMock).not.toHaveBeenCalled()
    dismissProgress()
    expect(el.querySelector('[data-prks-progress-view]')).toBeNull()
    presentProgress({
      host: el,
      status: 'Completed',
      rows: [{ id: 'b', title: 'B', status: 'Completed' }],
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('[data-work-id="b"]')).toBeNull()
    presentProgress({
      host: el,
      status: 'Completed',
      rows: [{ id: 'b', title: 'B', status: 'Completed' }],
      generation: 3,
    })
    expect(el.querySelector('[data-work-id="b"]')).not.toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('registers one bridge and applies a request that arrived first', () => {
    const el = host()
    const target = window
    target.__prksProgressPresentRequest = {
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
    expect(target.__prksProgressPresentRequest).toBeUndefined()
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files · In Progress')
    expect(el.querySelector('[data-work-id="early"]')).not.toBeNull()
  })
})
