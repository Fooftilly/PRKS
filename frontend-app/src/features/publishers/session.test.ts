import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissPublishers,
  presentPublishers,
  registerPublishersBridge,
  resetPublishersSessionForTests,
} from './session'

afterEach(() => {
  resetPublishersSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentPublishers
  delete window.prksVueDismissPublishers
  delete window.prksVueClosePublishersAliasModal
  delete window.prksIcon
  delete window.prksTagPlusIconHtml
  delete window.prksRefreshIcons
  delete window.fetchPublishersInUse
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner() {
  return {
    isCurrent: () => true,
    lastResolvedRoute: { name: 'publishers' },
  }
}

const OUP = {
  id: 'p1',
  name: 'Oxford University Press',
  work_count: 2,
  aliases: ['OUP'],
}
const CUP = {
  id: 'p2',
  name: 'Cambridge',
  work_count: 1,
  aliases: [],
}

describe('Publishers route bridge', () => {
  it('paints each owner from the coordinator list and does not fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.fetchPublishersInUse = vi.fn()
    window.prksIcon = () => '<i data-lucide="building-2"></i>'
    window.prksTagPlusIconHtml = () => '<span class="tag-add-shell__icon"></span>'
    registerPublishersBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentPublishers({
      owner: main,
      host: mainHost,
      publishers: [OUP, CUP],
      generation: 3,
      shell: true,
    })
    presentPublishers({
      owner: secondary,
      host: secondaryHost,
      publishers: [{ id: 'side', name: 'Side', work_count: 1, aliases: [] }],
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Publishers')
    expect(mainHost.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-prks-route="#/search?publisher=Oxford%20University%20Press"]')).not.toBeNull()
    expect(mainHost.querySelector('.publishers-page__list-stats')?.textContent).toBe('2 files · 1 alias')
    expect(mainHost.querySelector('[data-publisher-alias-edit="side"]')).toBeNull()
    expect(secondaryHost.querySelector('[data-publisher-alias-edit="side"]')).not.toBeNull()
    expect(secondaryHost.querySelector('[data-publisher-alias-edit="p1"]')).toBeNull()
    expect(mainHost.querySelector('#publishers-page-new-name')).not.toBeNull()
    expect(readRouteSurface(main)).toMatchObject({
      name: 'publishers',
      canonicalHash: '#/publishers',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(window.fetchPublishersInUse).not.toHaveBeenCalled()

    mainHost.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p1"]')?.click()
    await nextTick()
    expect(mainHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Oxford University Press')
    expect(mainHost.querySelector('[data-publisher-alias-remove="OUP"]')).not.toBeNull()
    expect(secondaryHost.querySelector('#publishers-page-alias-modal')).toBeNull()
    window.prksVueClosePublishersAliasModal?.()
    await nextTick()
    expect(mainHost.querySelector('#publishers-page-alias-modal')).toBeNull()
  })

  it('reopens only the resumed alias dialog and ignores a stale generation', async () => {
    const pane = owner()
    const el = host()
    presentPublishers({ owner: pane, host: el, publishers: [], generation: 2 })
    expect(el.querySelector('.publishers-page__empty')?.textContent).toContain('No publisher groups yet')
    dismissPublishers(pane)
    presentPublishers({
      owner: pane,
      host: el,
      publishers: [OUP],
      generation: 2,
      resume: { aliasPublisherId: 'p1' },
    })
    await nextTick()
    expect(el.querySelector('#publishers-page-delete-btn')).toBeNull()
    presentPublishers({
      owner: pane,
      host: el,
      publishers: [OUP, CUP],
      generation: 3,
      resume: { aliasPublisherId: 'p1' },
    })
    expect(el.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Oxford University Press')
    expect(el.querySelector('#publishers-page-delete-btn')).not.toBeNull()
    const ids = [...el.querySelectorAll('[data-publisher-alias-edit]')].map((node) =>
      node.getAttribute('data-publisher-alias-edit'),
    )
    expect(ids).toEqual(['p1', 'p2'])
  })

  it('registers the bridge and paints the host that stored the request', () => {
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'publishers',
      owner: pane,
      host: decoy,
      publishers: [OUP],
      generation: 1,
      shell: true,
    }
    registerPublishersBridge(window)
    expect(window.prksVuePresentPublishers).toBeTypeOf('function')
    expect(window.prksVueDismissPublishers).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-publishers-page]')).toBeNull()
  })

  it('dismisses one owner and leaves the other mounted', () => {
    registerPublishersBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentPublishers?.({
      owner: main,
      host: mainHost,
      publishers: [OUP],
      generation: 2,
      shell: true,
    })
    window.prksVuePresentPublishers?.({
      owner: secondary,
      host: secondaryHost,
      publishers: [CUP],
      generation: 1,
      shell: false,
      resume: { aliasPublisherId: 'p2' },
    })
    expect(secondaryHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
    window.prksVueDismissPublishers?.(main)
    expect(mainHost.querySelector('[data-prks-publishers-page]')).toBeNull()
    expect(secondaryHost.querySelector('[data-publisher-alias-edit="p2"]')).not.toBeNull()
    expect(secondaryHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
  })
})
