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
  delete window.prksOfflineRuntimeState
  delete window.prksOfflineRuntimeSubscribe
  delete window.deletePlaylistFromDetail
  delete window.reorderPlaylist
  delete window.addWorkToPlaylist
  delete window.removeWorkFromPlaylist
  delete window.fetchWorks
  delete window.prksConsumeApiError
  delete window.prksInferWorkSourceKind
  delete window.prksSaveWorkFieldDurably
  delete window.prksAlertMessage
  delete window.prksIcon
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

async function flushView(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
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
    expect(window.prksOpenNewPlaylistModalFromPlaylistsPage).toHaveBeenCalledWith(
      expect.objectContaining({ tabId: 'side' }),
    )
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

  it('saves a title edit against the session baseline after a refresh changes the description', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => ({}))
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    await nextTick()
    const title = el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')
    title!.value = 'Retitled'
    title!.dispatchEvent(new Event('input'))
    await nextTick()
    presentPlaylistDetail({
      owner: pane,
      host: el,
      playlist: { ...playlist, description: 'Remote description' },
      editing: true,
      generation: 2,
    })
    await nextTick()
    expect(el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')?.value).toBe('Retitled')
    expect(el.querySelector<HTMLTextAreaElement>('#prks-playlist-edit-desc')?.value).toBe('A description')
    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')?.click()
    await vi.waitFor(() => expect(window.updatePlaylist).toHaveBeenCalled())
    expect(window.updatePlaylist).toHaveBeenCalledWith('PL-1', { title: 'Retitled' }, {})
  })

  it('does not let a finished save close a later edit of the same playlist', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    let releaseSave: (value: unknown) => void = () => {}
    window.updatePlaylist = vi.fn(
      () =>
        new Promise((resolve) => {
          releaseSave = resolve
        }),
    )
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    await nextTick()
    const description = el.querySelector<HTMLTextAreaElement>('#prks-playlist-edit-desc')
    description!.value = 'Changed on the first session'
    description!.dispatchEvent(new Event('input'))
    await nextTick()
    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')?.click()
    await vi.waitFor(() => expect(window.updatePlaylist).toHaveBeenCalled())

    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-cancel')?.click()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: false, generation: 1 })
    await nextTick()
    expect(el.querySelector('#prks-playlist-edit-title')).toBeNull()

    pane.ui.playlistEditing = true
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    await nextTick()
    const title = el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')
    expect(title?.value).toBe('Course')
    title!.value = ''
    title!.dispatchEvent(new Event('input'))
    await nextTick()
    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')?.click()
    await nextTick()
    expect(el.querySelector('#prks-playlist-edit-status')?.textContent).toContain('Title is required.')
    title!.value = 'Second session title'
    title!.dispatchEvent(new Event('input'))
    await nextTick()
    vi.mocked(window.renderPlaylistDetail).mockClear()
    vi.mocked(window.prksReloadPlaylistDetail).mockClear()
    vi.mocked(window.updatePanelContent).mockClear()

    releaseSave({})
    await flushView()
    expect(pane.ui.playlistEditing).toBe(true)
    expect(el.querySelector('#prks-playlist-edit-title')).not.toBeNull()
    expect(el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')?.value).toBe('Second session title')
    expect(el.querySelector('#prks-playlist-edit-status')?.textContent).toContain('Title is required.')
    expect(window.prksReloadPlaylistDetail).not.toHaveBeenCalled()
    expect(window.renderPlaylistDetail).not.toHaveBeenCalled()
    expect(window.updatePanelContent).not.toHaveBeenCalled()
  })

  it('drops a canceled rename when edit ends and when the next edit starts', async () => {
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    el.querySelector<HTMLButtonElement>('[data-pl-rename="W1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#prks-pl-rename-input-W1')
    expect(input).not.toBeNull()
    input!.value = 'Draft that should not return'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: false, generation: 2 })
    await nextTick()
    expect(el.querySelector('#prks-pl-rename-input-W1')).toBeNull()

    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 3 })
    await nextTick()
    expect(el.querySelector('#prks-pl-rename-input-W1')).toBeNull()
    expect(el.querySelector('[data-pl-rename="W1"]')).not.toBeNull()
  })

  it('shows unavailable catalogue copy when videos cannot load', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.prksOfflineRuntimeState = () => 'offline'
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    await vi.waitFor(() => expect(el.textContent).toContain('Videos are not available right now.'))
    expect(el.textContent).not.toContain('No videos found')
  })

  it('reloads addable videos when the runtime comes online and keeps the draft', async () => {
    const listeners = new Set<(state: string) => void>()
    window.prksOfflineRuntimeSubscribe = (fn) => {
      listeners.add(fn)
      return () => {
        listeners.delete(fn)
      }
    }
    let runtime = 'offline'
    window.prksOfflineRuntimeState = () => runtime
    window.prksTabContextOwnsEntityRoute = () => true
    window.prksConsumeApiError = () => null
    window.prksInferWorkSourceKind = () => 'video'
    window.fetchWorks = vi.fn(async () => [])
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    const title = el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')
    title!.value = 'Draft title'
    title!.dispatchEvent(new Event('input'))
    await vi.waitFor(() => expect(el.textContent).toContain('Videos are not available right now.'))
    expect(el.textContent).not.toContain('No videos found')
    expect(window.fetchWorks).not.toHaveBeenCalled()

    window.fetchWorks = vi.fn(async () => [{ id: 'W9', title: 'Now online' }])
    runtime = 'online'
    listeners.forEach((fn) => fn('online'))
    await vi.waitFor(() => expect(el.textContent).toContain('Now online'))
    expect(el.textContent).not.toContain('Videos are not available right now.')
    expect(el.textContent).not.toContain('No videos found')
    expect(el.querySelector('#prks-playlist-edit-title')).not.toBeNull()
    expect(el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')?.value).toBe('Draft title')
  })

  it('discards a catalogue that finishes after this detail no longer owns the route', async () => {
    const listeners = new Set<(state: string) => void>()
    window.prksOfflineRuntimeSubscribe = (fn) => {
      listeners.add(fn)
      return () => {
        listeners.delete(fn)
      }
    }
    let runtime = 'offline'
    window.prksOfflineRuntimeState = () => runtime
    let owns = true
    window.prksTabContextOwnsEntityRoute = () => owns
    window.prksConsumeApiError = () => null
    window.prksInferWorkSourceKind = () => 'video'
    window.fetchWorks = vi.fn(async () => [])
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    await vi.waitFor(() => expect(el.textContent).toContain('Videos are not available right now.'))

    let release: (rows: Array<{ id?: string; title?: string }>) => void = () => {}
    window.fetchWorks = vi.fn(
      () =>
        new Promise<Array<{ id?: string; title?: string }>>((resolve) => {
          release = resolve
        }),
    )
    runtime = 'online'
    listeners.forEach((fn) => fn('online'))
    await vi.waitFor(() => expect(window.fetchWorks).toHaveBeenCalled())
    owns = false
    release([{ id: 'W9', title: 'Stale video' }])
    await flushView()
    expect(el.textContent).not.toContain('Stale video')
    expect(el.textContent).toContain('Videos are not available right now.')
    expect(el.textContent).not.toContain('No videos found')
    expect(el.querySelector('#prks-playlist-edit-title')).not.toBeNull()
  })

  it('marks awaited playlist controls busy until the request settles', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.prksOfflineRuntimeState = () => 'online'
    window.prksConsumeApiError = () => null
    window.prksInferWorkSourceKind = () => 'video'
    window.fetchWorks = vi.fn(async () => [{ id: 'W9', title: 'Extra video' }])
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    let releaseSave: (value: unknown) => void = () => {}
    let releaseReorder: (value: unknown) => void = () => {}
    let releaseAdd: (value: unknown) => void = () => {}
    let releaseRemove: (value: unknown) => void = () => {}
    let releaseRename: (value: { code?: string; error?: string } | null) => void = () => {}
    window.updatePlaylist = vi.fn(
      () =>
        new Promise((resolve) => {
          releaseSave = resolve
        }),
    )
    window.reorderPlaylist = vi.fn(
      () =>
        new Promise((resolve) => {
          releaseReorder = resolve
        }),
    )
    window.addWorkToPlaylist = vi.fn(
      () =>
        new Promise((resolve) => {
          releaseAdd = resolve
        }),
    )
    window.removeWorkFromPlaylist = vi.fn(
      () =>
        new Promise((resolve) => {
          releaseRemove = resolve
        }),
    )
    window.prksSaveWorkFieldDurably = vi.fn(
      () =>
        new Promise<{ code?: string; error?: string } | null>((resolve) => {
          releaseRename = resolve
        }),
    )
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    await nextTick()
    const up = () => el.querySelector<HTMLButtonElement>('[data-pl-up="W1"]')
    expect(up()?.disabled).toBe(false)
    const description = el.querySelector<HTMLTextAreaElement>('#prks-playlist-edit-desc')
    description!.value = 'Changed while saving'
    description!.dispatchEvent(new Event('input'))
    await nextTick()

    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')?.click()
    await nextTick()
    const saveBtn = el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')
    expect(saveBtn?.getAttribute('aria-busy')).toBe('true')
    expect(saveBtn?.disabled).toBe(true)
    expect(saveBtn?.textContent).toContain('Saving…')
    expect(el.querySelector<HTMLButtonElement>('[data-pl-down="W1"]')?.disabled).toBe(true)
    expect(el.querySelector('[data-pl-down="W1"]')?.getAttribute('aria-busy')).toBeNull()
    releaseSave({})
    await flushView()
    expect(saveBtn?.getAttribute('aria-busy')).toBeNull()
    expect(saveBtn?.disabled).toBe(false)
    expect(saveBtn?.textContent).toContain('Save')

    el.querySelector<HTMLButtonElement>('[data-pl-down="W1"]')?.click()
    await nextTick()
    const down = el.querySelector<HTMLButtonElement>('[data-pl-down="W1"]')
    expect(down?.getAttribute('aria-busy')).toBe('true')
    expect(down?.disabled).toBe(true)
    expect(down?.textContent).toContain('Reordering…')
    expect(up()?.disabled).toBe(true)
    expect(up()?.getAttribute('aria-busy')).toBeNull()
    releaseReorder(null)
    await flushView()
    expect(down?.getAttribute('aria-busy')).toBeNull()
    expect(down?.disabled).toBe(false)
    expect(up()?.disabled).toBe(false)

    el.querySelector<HTMLInputElement>('#prks-playlist-add-search')?.dispatchEvent(new FocusEvent('focus'))
    await vi.waitFor(() => expect(el.textContent).toContain('Extra video'))
    const addBtn = el.querySelector<HTMLButtonElement>('#prks-playlist-add-results button')
    addBtn?.click()
    await nextTick()
    expect(addBtn?.getAttribute('aria-busy')).toBe('true')
    expect(addBtn?.disabled).toBe(true)
    expect(addBtn?.textContent).toContain('Adding…')
    releaseAdd(null)
    await flushView()
    expect(addBtn?.getAttribute('aria-busy')).toBeNull()
    expect(addBtn?.textContent).toContain('Add')

    el.querySelector<HTMLButtonElement>('[data-pl-remove="W2"]')?.click()
    await nextTick()
    const removeBtn = el.querySelector<HTMLButtonElement>('[data-pl-remove="W2"]')
    expect(removeBtn?.getAttribute('aria-busy')).toBe('true')
    expect(removeBtn?.disabled).toBe(true)
    expect(removeBtn?.textContent).toContain('Removing…')
    releaseRemove(null)
    await flushView()
    expect(removeBtn?.getAttribute('aria-busy')).toBeNull()
    expect(removeBtn?.disabled).toBe(false)

    el.querySelector<HTMLButtonElement>('[data-pl-rename="W1"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-pl-rename-save="W1"]')?.click()
    await nextTick()
    const renameBtn = el.querySelector<HTMLButtonElement>('[data-pl-rename-save="W1"]')
    expect(renameBtn?.getAttribute('aria-busy')).toBe('true')
    expect(renameBtn?.disabled).toBe(true)
    expect(renameBtn?.textContent).toContain('Renaming…')
    releaseRename({ code: '' })
    await flushView()
    expect(el.querySelector('[data-pl-rename-save="W1"]')).toBeNull()
    expect(el.querySelector('[data-pl-rename="W1"]')).not.toBeNull()
  })

  it('does not let a finished action from playlist A change playlist B', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.prksOfflineRuntimeState = () => 'online'
    window.prksConsumeApiError = () => null
    window.prksInferWorkSourceKind = () => 'video'
    window.fetchWorks = vi.fn(async () => [{ id: 'W9', title: 'Extra video' }])
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    window.prksAlertMessage = vi.fn()
    let releaseAdd: (value: unknown) => void = () => {}
    window.addWorkToPlaylist = vi.fn(
      () =>
        new Promise((resolve) => {
          releaseAdd = resolve
        }),
    )
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    el.querySelector<HTMLInputElement>('#prks-playlist-add-search')?.dispatchEvent(new FocusEvent('focus'))
    await vi.waitFor(() => expect(el.querySelector('#prks-playlist-add-results button')).not.toBeNull())
    el.querySelector<HTMLButtonElement>('#prks-playlist-add-results button')?.click()
    await vi.waitFor(() => expect(window.addWorkToPlaylist).toHaveBeenCalled())

    const other = { ...playlist, id: 'PL-2', title: 'Other playlist' }
    presentPlaylistDetail({ owner: pane, host: el, playlist: other, editing: true, generation: 2 })
    await nextTick()
    const search = el.querySelector<HTMLInputElement>('#prks-playlist-add-search')
    search!.value = 'keep-b'
    search!.dispatchEvent(new Event('input'))
    search!.dispatchEvent(new FocusEvent('focus'))
    await nextTick()
    releaseAdd(null)
    await flushView()
    expect(el.querySelector<HTMLInputElement>('#prks-playlist-add-search')?.value).toBe('keep-b')
    expect(el.textContent).not.toContain('Added.')
    expect(el.querySelector('#prks-playlist-add-results')?.classList.contains('hidden')).toBe(false)
    expect(el.textContent).toContain('Other playlist')
  })

  it('does not let a finished save or rename from playlist A change playlist B', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    window.prksAlertMessage = vi.fn()
    let releaseSave: (value: unknown) => void = () => {}
    window.updatePlaylist = vi.fn(
      () =>
        new Promise((resolve) => {
          releaseSave = resolve
        }),
    )
    const pane = owner()
    pane.ui.playlistEditing = true
    const el = host()
    const other = { ...playlist, id: 'PL-2', title: 'Other playlist' }
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 1 })
    await nextTick()
    const saveBtn = el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')
    expect(saveBtn?.disabled).toBe(false)
    expect(el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')?.value).toBe('Course')
    const description = el.querySelector<HTMLTextAreaElement>('#prks-playlist-edit-desc')
    description!.value = 'Changed on A'
    description!.dispatchEvent(new Event('input'))
    await nextTick()
    saveBtn?.click()
    await vi.waitFor(() => expect(window.updatePlaylist).toHaveBeenCalled())
    presentPlaylistDetail({ owner: pane, host: el, playlist: other, editing: true, generation: 2 })
    await nextTick()
    const title = el.querySelector<HTMLInputElement>('#prks-playlist-edit-title')
    title!.value = ''
    title!.dispatchEvent(new Event('input'))
    await nextTick()
    el.querySelector<HTMLButtonElement>('#prks-playlist-edit-save')?.click()
    await nextTick()
    expect(el.querySelector('#prks-playlist-edit-status')?.textContent).toContain('Title is required.')
    releaseSave({})
    await flushView()
    expect(el.querySelector('#prks-playlist-edit-status')?.textContent).toContain('Title is required.')

    let releaseRename: (value: { code?: string; error?: string } | null) => void = () => {}
    window.prksSaveWorkFieldDurably = vi.fn(
      () =>
        new Promise<{ code?: string; error?: string } | null>((resolve) => {
          releaseRename = resolve
        }),
    )
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: true, generation: 3 })
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-pl-rename="W1"]')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-pl-rename-save="W1"]')?.click()
    await vi.waitFor(() => expect(window.prksSaveWorkFieldDurably).toHaveBeenCalled())
    presentPlaylistDetail({ owner: pane, host: el, playlist: other, editing: true, generation: 4 })
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-pl-rename="W1"]')?.click()
    await nextTick()
    const renameInput = el.querySelector<HTMLInputElement>('#prks-pl-rename-input-W1')
    renameInput!.value = 'B keeps this title'
    renameInput!.dispatchEvent(new Event('input'))
    await nextTick()
    releaseRename({ code: '' })
    await flushView()
    expect(el.querySelector<HTMLInputElement>('#prks-pl-rename-input-W1')?.value).toBe('B keeps this title')
    expect(window.prksAlertMessage).not.toHaveBeenCalled()
  })

  it('owns the delete busy label and clears it when the playlist changes', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    let release: (() => void) | undefined
    window.deletePlaylistFromDetail = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    const pane = owner()
    const el = host()
    presentPlaylistDetail({ owner: pane, host: el, playlist, editing: false, generation: 1 })
    el.querySelector<HTMLButtonElement>('#prks-playlist-delete-btn')?.click()
    await vi.waitFor(() =>
      expect(el.querySelector('#prks-playlist-delete-btn')?.textContent).toContain('Deleting…'),
    )
    expect(el.querySelector<HTMLButtonElement>('#prks-playlist-delete-btn')?.disabled).toBe(true)

    presentPlaylistDetail({
      owner: pane,
      host: el,
      playlist: { ...playlist, id: 'PL-2', title: 'Other' },
      editing: false,
      generation: 2,
    })
    await nextTick()
    const next = el.querySelector<HTMLButtonElement>('#prks-playlist-delete-btn')
    expect(next?.textContent).toContain('Delete playlist')
    expect(next?.textContent).not.toContain('Deleting…')
    expect(next?.disabled).toBe(false)
    release?.()
  })

  it('activates an index row on Enter', () => {
    const pane = owner('main')
    const el = host()
    presentPlaylistsIndex({
      owner: pane,
      host: el,
      items: [{ id: 'PL-1', title: 'Main list', description: '', item_count: 2 }],
      generation: 1,
    })
    const row = el.querySelector<HTMLElement>('[data-prks-route="#/playlists/PL-1"]')
    expect(row?.getAttribute('role')).toBe('link')
    expect(row?.getAttribute('tabindex')).toBe('0')
    const clicks = vi.fn()
    row?.addEventListener('click', clicks)
    row?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    expect(clicks).toHaveBeenCalledOnce()
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
