import { afterEach, describe, expect, it } from 'vitest'
import {
  presentWorkPanelRead,
  refreshMountedWorkPanelRead,
  resetWorkPanelReadForTests,
  workPanelReadOwns,
} from './panel-session'

function mountPanel(ownerTabId: string, generation: number): HTMLElement {
  document.body.innerHTML = `
    <div id="panel-content" data-prks-owner-tab-id="${ownerTabId}" data-prks-owner-generation="${generation}">
      <div data-prks-role="work-panel-read-anchor" hidden></div>
      <div data-prks-role="work-panel-summary"></div>
      <div data-prks-role="work-panel-identity"></div>
      <div data-prks-role="work-panel-dates"></div>
      <div data-prks-role="work-bib-rows"></div>
      <div data-prks-role="work-panel-source"></div>
      <div data-prks-role="work-people-read"></div>
      <div id="work-tags-list" data-prks-role="work-tags-read"></div>
      <span data-prks-role="work-folder-read"></span>
      <p data-prks-role="work-playlist-read"></p>
    </div>`
  const panel = document.getElementById('panel-content')
  if (!panel) throw new Error('panel missing')
  return panel
}

const requestFor = (ownerTabId: string, generation: number, workId: string, title: string) => ({
  ownerTabId,
  ownerGeneration: generation,
  workId,
  work: { id: workId, title: `Server ${title}`, folder_id: 'f', folder_title: `${title} folder` },
  effectiveWork: { id: workId, title },
  tags: [{ id: 't', name: `${title} tag`, color: '#abc' }],
  sourceKind: 'pdf',
  docType: { value: 'article', label: 'Article', color: '#3b82f6', border: '#1d4ed8' },
})

afterEach(() => {
  resetWorkPanelReadForTests()
  document.body.innerHTML = ''
})

describe('work panel read session', () => {
  it('paints the focused dataset owner and ignores a late refresh from the previous Work', () => {
    const panel = mountPanel('main', 1)
    expect(presentWorkPanelRead(requestFor('main', 1, 'w-a', 'Alpha'))).toBe(true)
    expect(panel.querySelector('.card-title')?.textContent).toBe('Alpha')
    expect(workPanelReadOwns({ tabId: 'main', generation: 1, getEntity: () => ({ id: 'w-a' }) })).toBe(true)

    panel.dataset.prksOwnerTabId = 'side'
    panel.dataset.prksOwnerGeneration = '3'
    expect(
      refreshMountedWorkPanelRead({
        ownerTabId: 'main',
        ownerGeneration: 1,
        workId: 'w-a',
        effectiveWork: { title: 'Alpha late' },
      }),
    ).toBe(false)
    expect(panel.querySelector('.card-title')?.textContent).toBe('Alpha')

    expect(presentWorkPanelRead(requestFor('side', 3, 'w-b', 'Beta'))).toBe(true)
    expect(panel.querySelector('.card-title')?.textContent).toBe('Beta')
    expect(
      refreshMountedWorkPanelRead({
        ownerTabId: 'main',
        ownerGeneration: 1,
        workId: 'w-a',
        tags: [{ id: 'late', name: 'Late', color: '' }],
      }),
    ).toBe(false)
    expect(panel.querySelector('.work-tag-chip__link')?.textContent).toBe('Beta tag')
    expect(workPanelReadOwns({ tabId: 'main', generation: 1, getEntity: () => ({ id: 'w-a' }) })).toBe(false)
    expect(workPanelReadOwns({ tabId: 'side', generation: 3, getEntity: () => ({ id: 'w-b' }) })).toBe(true)
  })

  it('refuses to paint an owner that does not match the panel dataset', () => {
    mountPanel('main', 2)
    expect(presentWorkPanelRead(requestFor('side', 2, 'w-b', 'Beta'))).toBe(false)
    expect(document.querySelector('.card-title')).toBeNull()
  })

  it('keeps an in-flight editor refresh on the same owner from replacing the editor base', () => {
    const panel = mountPanel('main', 5)
    presentWorkPanelRead(requestFor('main', 5, 'w-a', 'Alpha'))
    expect(
      refreshMountedWorkPanelRead({
        ownerTabId: 'main',
        ownerGeneration: 5,
        workId: 'w-a',
        effectiveWork: { title: 'Still pending', publisher: 'Later Press' },
        publishedDisplay: '03/03/2003',
      }),
    ).toBe(true)
    expect(panel.querySelector('.card-title')?.textContent).toBe('Still pending')
    expect(panel.querySelector('[data-prks-role="work-bib-rows"]')?.textContent).toContain('Later Press')
    expect(workPanelReadOwns({ tabId: 'main', generation: 4, getEntity: () => ({ id: 'w-a' }) })).toBe(false)
  })
})
