import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { presentProgress, resetProgressSessionForTests } from './session'
import { PROGRESS_STATUSES } from './status'

afterEach(() => {
  resetProgressSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksWorkCardHtml
  delete window.prksWorkBrowseModeToggleHtml
  delete window.prksWorkBrowseCollectionClass
  delete window.prksBindWorkBrowseMode
  delete window.prksSyncSidebarActive
  delete window.prksAbstractExcerpt
})

function host(): HTMLElement {
  const el = document.createElement('div')
  el.setAttribute('data-prks-progress-host', 'true')
  document.body.appendChild(el)
  return el
}

function owner(): Record<string, unknown> {
  return {}
}

function installCardSpy() {
  const calls: { id: unknown; options: { subtitle?: string; suppressThumbnail?: boolean } }[] = []
  window.prksWorkCardHtml = (work, options) => {
    calls.push({ id: work.id, options })
    return `<div class="project-card project-card--work-card" data-work-id="${String(work.id)}"></div>`
  }
  window.prksWorkBrowseModeToggleHtml = () =>
    '<div class="work-browse-mode" data-prks-role="work-browse-mode"></div>'
  window.prksWorkBrowseCollectionClass = () => 'work-browse-collection work-browse-collection--cards card-grid'
  window.prksBindWorkBrowseMode = () => {}
  window.prksSyncSidebarActive = () => {}
  return calls
}

describe('ProgressView', () => {
  it('renders each status, alphabetical cards, the file count, and the empty state', async () => {
    const calls = installCardSpy()
    const el = host()
    const pane = owner()
    const rows = [
      { id: 'b', title: 'b', status: 'Paused', abstract_excerpt: 'second' },
      { id: 'a', title: 'A', status: 'Paused', abstract_excerpt: 'first' },
      { id: 'other', title: 'Other', status: 'Planned', abstract_excerpt: 'no' },
    ]
    presentProgress({ owner: pane, host: el, status: 'Paused', rows, offlineCached: false, generation: 1 })
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files · Paused')
    expect(el.querySelector('.prks-scope-line')?.textContent).toBe('2 files')
    expect(Array.from(el.querySelectorAll('[data-work-id]')).map((node) => node.getAttribute('data-work-id'))).toEqual([
      'a',
      'b',
    ])
    expect(calls.map((call) => call.options.suppressThumbnail)).toEqual([undefined, undefined])
    expect(el.querySelector('[data-prks-role="work-browse-mode"]')).not.toBeNull()
    expect(el.querySelector('.work-browse-collection--cards')).not.toBeNull()

    for (const status of PROGRESS_STATUSES) {
      calls.length = 0
      presentProgress({
        owner: pane,
        host: el,
        status,
        rows: [{ id: status, title: 'Only', status, abstract_excerpt: 'x' }],
        offlineCached: false,
        generation: PROGRESS_STATUSES.indexOf(status) + 2,
      })
      await nextTick()
      expect(el.querySelector('.prks-page-title')?.textContent).toBe(`Files · ${status}`)
      expect(el.querySelector('.prks-scope-line')?.textContent).toBe('1 file')
      expect(el.querySelectorAll('[data-work-id]').length).toBe(1)
      expect(el.querySelector('[data-work-id]')?.getAttribute('data-work-id')).toBe(status)
    }

    presentProgress({ owner: pane, host: el, status: 'Not Started', rows: [], offlineCached: false, generation: 20 })
    await nextTick()
    expect(el.querySelector('.prks-state.prks-state--empty .prks-state__heading')?.textContent).toBe(
      'No files with this progress status yet.',
    )
    expect(el.querySelector('.progress-empty-msg')).toBeNull()
    expect(el.querySelector('[data-work-id]')).toBeNull()
    expect(el.querySelector('.prks-scope-line')?.textContent).toBe('0 files')
  })

  it('suppresses cached thumbnails and drops stale rows when the status changes', async () => {
    const calls = installCardSpy()
    const el = host()
    const pane = owner()
    presentProgress({
      owner: pane,
      host: el,
      status: 'Planned',
      rows: [
        { id: 'stay', title: 'Stay', status: 'Planned', abstract_excerpt: 'p' },
        { id: 'move', title: 'Move', status: 'Planned', abstract_excerpt: 'p' },
      ],
      offlineCached: true,
      generation: 1,
    })
    expect(calls.every((call) => call.options.suppressThumbnail === true)).toBe(true)

    calls.length = 0
    presentProgress({
      owner: pane,
      host: el,
      status: 'Completed',
      rows: [
        { id: 'stay', title: 'Stay', status: 'Planned', abstract_excerpt: 'p' },
        { id: 'move', title: 'Move', status: 'Completed', abstract_excerpt: 'c' },
      ],
      offlineCached: true,
      generation: 2,
    })
    await nextTick()
    expect(Array.from(el.querySelectorAll('[data-work-id]')).map((node) => node.getAttribute('data-work-id'))).toEqual([
      'move',
    ])
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files · Completed')
    expect(calls).toEqual([{ id: 'move', options: { subtitle: 'c…', suppressThumbnail: true } }])
  })

  it('canonicalizes an invalid status and ignores an older generation', async () => {
    installCardSpy()
    const el = host()
    const pane = owner()
    const hash = window.location.hash
    presentProgress({
      owner: pane,
      host: el,
      status: 'Finished',
      rows: [{ id: 'n', title: 'None', status: 'Not Started' }],
      offlineCached: false,
      generation: 3,
    })
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files · Not Started')
    expect(window.location.hash).toBe(hash)

    presentProgress({
      owner: pane,
      host: el,
      status: 'Paused',
      rows: [{ id: 'late', title: 'Late', status: 'Paused' }],
      offlineCached: false,
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector('.prks-page-title')?.textContent).toBe('Files · Not Started')
    expect(el.querySelector('[data-work-id="late"]')).toBeNull()
    expect(el.querySelector('[data-work-id="n"]')).not.toBeNull()
  })
})
