import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissPlaylists,
  PLAYLISTS_RETAIN_SURFACE_KEY,
  presentPlaylistDetail,
  presentPlaylistsIndex,
  registerPlaylistsBridge,
  resetPlaylistsSessionForTests,
} from './session'

afterEach(() => {
  resetPlaylistsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentPlaylistsIndex
  delete window.prksVuePresentPlaylistDetail
  delete window.prksVueDismissPlaylists
  delete window.prksOpenNewPlaylistModalFromPlaylistsPage
  delete window.updatePlaylist
  delete window.prksReloadPlaylistDetail
  delete window.renderPlaylistDetail
  delete window.updatePanelContent
  delete window.prksTabContextOwnsEntityRoute
  delete window.prksIcon
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner(tabId = 'tab') {
  return {
    tabId,
    isCurrent: () => true,
    lastResolvedRoute: { name: 'playlist-detail' as const, params: { playlistId: 'PL-1' } },
    ui: { playlistEditing: false, playlistRename: {} as Record<string, boolean> },
    getEntity: () => ({ id: 'PL-1' }),
  }
}

const playlist = {
  id: 'PL-1',
  title: 'Course',
  description: 'A description',
  original_url: 'https://example.test/course',
  items: [
    { id: 'W1', title: 'First', author_text: 'Channel', published_date: '2021-03-04' },
    { id: 'W2', title: 'Second', author_text: 'Other', published_date: '' },
  ],
}

describe('Playlists route surface', () => {
  it('renders ready, empty, and unavailable indexes independently', () => {
    window.prksOpenNewPlaylistModalFromPlaylistsPage = vi.fn()
    const main = owner('main')
    const secondary = owner('side')
    const mainHost = host()
    const sideHost = host()
    presentPlaylistsIndex({
      owner: main,
      host: mainHost,
      items: [{ id: 'PL-1', title: 'Main list', description: '', item_count: 2 }],
      generation: 4,
      shell: true,
    })
    presentPlaylistsIndex({
      owner: secondary,
      host: sideHost,
      items: [],
      generation: 1,
      shell: false,
    })
    expect(mainHost.textContent).toContain('Main list')
    expect(mainHost.textContent).toContain('2 items')
    expect(mainHost.querySelector('#prks-playlists-header-new')).not.toBeNull()
    expect(mainHost.textContent).not.toContain('No playlists yet.')
    expect(sideHost.textContent).toContain('No playlists yet.')
    expect(sideHost.querySelector('#prks-playlists-empty-new')?.textContent).toContain('New playlist')
    expect(sideHost.querySelector('#prks-playlists-header-new')).not.toBeNull()
    sideHost.querySelector<HTMLButtonElement>('#prks-playlists-empty-new')?.click()
    expect(window.prksOpenNewPlaylistModalFromPlaylistsPage).toHaveBeenCalledOnce()
    expect(readRouteSurface(main)?.ownsMainShell).toBe(true)
    expect(readRouteSurface(main)?.canonicalHash).toBe('#/playlists')
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(mainHost.textContent).not.toContain('No playlists yet.')

    presentPlaylistsIndex({
      owner: main,
      host: mainHost,
      availability: 'unavailable',
      items: [{ id: 'stale', title: 'Should not show', item_count: 1 }],
      generation: 5,
    })
    expect(mainHost.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
    expect(mainHost.textContent).toContain('Playlists not available offline')
    expect(mainHost.textContent).not.toContain('No playlists yet.')
    expect(mainHost.textContent).not.toContain('Should not show')
    expect(sideHost.textContent).toContain('No playlists yet.')
  })

  it('shows a pending local create and an effective rename, and omits a deleted row', () => {
    const pane = owner()
    const el = host()
    presentPlaylistsIndex({
      owner: pane,
      host: el,
      items: [
        { id: 'local-1', title: 'Unsent playlist', description: '', item_count: 0 },
        { id: 'PL-2', title: 'Renamed offline', description: 'new', item_count: 1 },
      ],
      generation: 1,
    })
    expect(el.textContent).toContain('Unsent playlist')
    expect(el.textContent).toContain('0 items')
    expect(el.textContent).toContain('Renamed offline')
    expect(el.textContent).not.toContain('Deleted playlist')
    const link = el.querySelector('[data-prks-route="#/playlists/local-1"]')
    expect(link).not.toBeNull()
  })

  it('renders detail states from the projection', () => {
    const pane = owner()
    const el = host()
    presentPlaylistDetail({
      owner: pane,
      host: el,
      playlist,
      editing: false,
      generation: 1,
    })
    expect(el.querySelector('.prks-playlist-detail')).not.toBeNull()
    expect(el.textContent).toContain('Course')
    expect(el.textContent).toContain('A description')
    expect(el.textContent).toContain('First')
    expect(el.textContent).toContain('Channel · 04/03/2021')
    expect(el.textContent).toContain('Second')
    expect(el.querySelector('[data-pl-nav="W1"]')).not.toBeNull()
    expect(el.querySelector('#prks-playlist-delete-btn')).not.toBeNull()
    expect(el.querySelector('#prks-playlist-edit-title')).toBeNull()
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/playlists/PL-1')

    presentPlaylistDetail({
      owner: pane,
      host: el,
      availability: 'unavailable',
      playlistId: 'PL-1',
      generation: 2,
    })
    expect(el.querySelector('[data-prks-role="offline-unavailable"]')).not.toBeNull()
    expect(el.textContent).toContain('Playlist not available offline')
    expect(el.textContent).not.toContain('Playlist not found')
    expect(el.querySelector('.prks-playlist-detail')).toBeNull()

    presentPlaylistDetail({
      owner: pane,
      host: el,
      availability: 'not-found',
      playlistId: 'missing',
      generation: 3,
    })
    expect(el.textContent).toContain('Playlist not found')
    expect(el.querySelector('[data-prks-role="offline-unavailable"]')).toBeNull()

    presentPlaylistDetail({
      owner: pane,
      host: el,
      playlist: {
        id: 'local-9',
        title: 'Unsent playlist',
        description: 'local text',
        original_url: '',
        items: [{ id: 'W9', title: 'Pending video', author_text: '', published_date: '' }],
      },
      generation: 4,
    })
    expect(el.textContent).toContain('Unsent playlist')
    expect(el.textContent).toContain('Pending video')
    expect(el.textContent).not.toContain('Playlist not found')
  })

  it('keeps the editor draft across a same-playlist refresh and saves dirty fields only', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => ({}))
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    const title = el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')
    const description = el.querySelector<HTMLTextAreaElement>('#prks-playlist-edit-desc')
    expect(title?.value).toBe('Course')
    expect(el.querySelector('#prks-playlist-add-search')).not.toBeNull()
    expect(el.querySelector('[data-pl-up="W1"]')).not.toBeNull()
    expect(el.querySelector('[data-pl-rename="W1"]')).not.toBeNull()
    description!.value = 'Edited'
    description!.dispatchEvent(new Event('input'))
    await nextTick()
    presentPlaylistDetail({
      owner: pane,
      host: el,
      playlist: { ...playlist, items: [...playlist.items].reverse() },
      editing: true,
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector<HTMLTextAreaElement>('#prks-playlist-edit-desc')?.value).toBe('Edited')
    expect(el.querySelector('.prks-playlist-item .card-title')?.textContent).toBe('Second')
    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')?.click()
    await nextTick()
    await vi.waitFor(() => expect(window.updatePlaylist).toHaveBeenCalled())
    expect(window.updatePlaylist).toHaveBeenCalledWith('PL-1', { description: 'Edited' }, {})

    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 3 })
    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-cancel')?.click()
    expect(pane.ui.playlistEditing).toBe(false)
    expect(window.updatePlaylist).toHaveBeenCalledTimes(1)
  })

  it('keeps the index host across a retained refresh and unmounts a failed one', async () => {
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [PLAYLISTS_RETAIN_SURFACE_KEY]?: boolean
    } = {
      tabId: 'coord',
      isCurrent: () => true,
      registerCleanup(fn) {
        cleanups.add(fn)
        return () => {
          cleanups.delete(fn)
        }
      },
    }
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentPlaylistsIndex({
      owner: pane,
      host: routeHost,
      items: [{ id: 'PL-1', title: 'Kept', description: '', item_count: 1 }],
      generation: 1,
    })
    expect(routeHost.querySelector('[data-prks-playlists-index-view]')).not.toBeNull()

    pane[PLAYLISTS_RETAIN_SURFACE_KEY] = true
    const drained = Array.from(cleanups)
    cleanups.clear()
    drained.forEach((fn) => fn())
    pane[PLAYLISTS_RETAIN_SURFACE_KEY] = false
    expect(cleanups.size).toBe(1)
    expect(routeHost.textContent).toContain('Kept')

    presentPlaylistsIndex({
      owner: pane,
      host: routeHost,
      items: [{ id: 'PL-1', title: 'Refreshed', description: '', item_count: 1 }],
      generation: 2,
    })
    await nextTick()
    expect(routeHost.textContent).toContain('Refreshed')
    expect(routeHost.querySelector('[data-prks-playlists-index-view]')).not.toBeNull()

    pane[PLAYLISTS_RETAIN_SURFACE_KEY] = true
    const failed = Array.from(cleanups)
    cleanups.clear()
    failed.forEach((fn) => fn())
    pane[PLAYLISTS_RETAIN_SURFACE_KEY] = false
    dismissPlaylists(pane)
    expect(routeHost.querySelector('[data-prks-playlists-index-view]')).toBeNull()
    expect(readRouteSurface(pane)?.mounted).toBe(false)
    contentDiv.innerHTML = '<p><button type="button" id="prks-route-retry">Retry</button></p>'
    expect(contentDiv.querySelector('#prks-route-retry')).not.toBeNull()
    expect(contentDiv.querySelector('[data-prks-playlists-index-view]')).toBeNull()
    expect(document.body.querySelector('[data-prks-playlists-index-view]')).toBeNull()
  })

  it('unmounts one owner without affecting the other', () => {
    const a = owner('a')
    const b = owner('b')
    const aHost = host()
    const bHost = host()
    presentPlaylistsIndex({
      owner: a,
      host: aHost,
      items: [{ id: '1', title: 'Alpha', description: '', item_count: 1 }],
      generation: 1,
    })
    presentPlaylistDetail({
      owner: b,
      host: bHost,
      playlist: { ...playlist, id: 'PL-2', title: 'Beta' },
      generation: 1,
    })
    dismissPlaylists(a)
    expect(aHost.querySelector('[data-prks-playlists-index-view]')).toBeNull()
    expect(bHost.textContent).toContain('Beta')
  })

  it('registers bridges onto the host that stored the request', () => {
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner('early')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'playlists',
      owner: pane,
      host: decoy,
      items: [{ id: 'PL-1', title: 'Early', description: '', item_count: 1 }],
      generation: 1,
      shell: true,
    }
    registerPlaylistsBridge(window)
    expect(window.prksVuePresentPlaylistsIndex).toBeTypeOf('function')
    expect(window.prksVuePresentPlaylistDetail).toBeTypeOf('function')
    expect(el.textContent).toContain('Early')
    expect(decoy.textContent).not.toContain('Early')
  })
})
