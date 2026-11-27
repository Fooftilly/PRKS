import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { browserPlaylistIntents, type PlaylistIntentOwner } from './intents'
import type { PlaylistFieldDraft } from './types'

/* Every field at revision 1, as Edit pins it when the form shows the read value. */
function pinAll(fields: PlaylistFieldDraft) {
  return Object.fromEntries(Object.entries(fields).map(([field, value]) => [field, { value, revision: 1 }]))
}

beforeEach(() => {
  window.prksPinPlaylistEdit = vi.fn(async (_id: string, fields: Record<string, string>) =>
    pinAll(fields as unknown as PlaylistFieldDraft))
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.prksPinPlaylistEdit
  delete window.prksOpenNewPlaylistModalFromPlaylistsPage
  delete window.updatePlaylist
  delete window.reorderPlaylist
  delete window.removeWorkFromPlaylist
  delete window.addWorkToPlaylist
  delete window.fetchWorks
  delete window.prksInferWorkSourceKind
  delete window.prksConsumeApiError
  delete window.prksOfflineRuntimeState
  delete window.prksSaveWorkFieldDurably
  delete window.prksRefreshPendingWorkMetadata
  delete window.deletePlaylistFromDetail
  delete window.prksReloadPlaylistDetail
  delete window.renderPlaylistDetail
  delete window.updatePanelContent
  delete window.prksTabContextOwnsEntityRoute
})

const shown: PlaylistFieldDraft = {
  title: 'Course',
  description: 'Old',
  original_url: 'https://example.test/old',
}

function owner(overrides: Partial<PlaylistIntentOwner> = {}): PlaylistIntentOwner {
  return {
    tabId: 'main',
    generation: 2,
    isCurrent: () => true,
    lastResolvedRoute: { name: 'playlist-detail', params: { playlistId: 'PL-1' } },
    root: document.createElement('div'),
    ui: {
      playlistEditing: true,
      playlistRename: {},
      playlistEditPin: { playlistId: 'PL-1', session: 0, base: Promise.resolve(pinAll(shown)) },
    },
    getEntity: () => ({ id: 'PL-1' }),
    ...overrides,
  }
}

describe('Playlist intents', () => {
  it('opens the existing create modal for this owner', () => {
    window.prksOpenNewPlaylistModalFromPlaylistsPage = vi.fn()
    const pane = owner()
    browserPlaylistIntents(pane, 2).create()
    expect(window.prksOpenNewPlaylistModalFromPlaylistsPage).toHaveBeenCalledOnce()
    expect(window.prksOpenNewPlaylistModalFromPlaylistsPage).toHaveBeenCalledWith(pane)
  })

  it('sends only dirty fields and leaves Cancel unsaved', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => ({}))
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    const pane = owner()
    const intents = browserPlaylistIntents(pane, 2)
    intents.cancelEdit('PL-1')
    expect(window.updatePlaylist).not.toHaveBeenCalled()
    expect(pane.ui?.playlistEditing).toBe(false)

    pane.ui!.playlistEditing = true
    intents.pinEditBase('PL-1', shown)
    vi.mocked(window.renderPlaylistDetail).mockClear()
    vi.mocked(window.updatePanelContent).mockClear()
    const saved = await intents.saveFields(
      'PL-1',
      { title: 'Course', description: 'New', original_url: 'https://example.test/old' },
      shown,
    )
    expect(saved.ok).toBe(true)
    expect(window.updatePlaylist).toHaveBeenCalledWith('PL-1', { description: 'New' }, { base: pinAll(shown), still: expect.any(Function) })
    expect(window.prksReloadPlaylistDetail).toHaveBeenCalledWith(pane, 'PL-1')
    expect(window.renderPlaylistDetail).toHaveBeenCalled()
    expect(window.updatePanelContent).toHaveBeenCalledWith('details')
  })

  it('saves against the revisions pinned when Edit opened, not newer ones (#894)', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => ({}))
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    const pane = owner()
    const intents = browserPlaylistIntents(pane, 2)
    intents.invalidateEditSession()
    expect(pane.ui?.playlistEditPin).toBeNull()
    window.prksPinPlaylistEdit = vi.fn(async () => ({
      title: { value: 'Course', revision: 4 },
      description: { value: 'Old', revision: 0 },
      original_url: { value: 'https://example.test/old', revision: 2 },
    }))
    intents.pinEditBase('PL-1', shown)
    expect(window.prksPinPlaylistEdit).toHaveBeenCalledWith('PL-1', shown)
    /* Another tab saves the description afterwards; this tab's Save keeps revision 0. */
    const saved = await intents.saveFields('PL-1', { ...shown, description: 'Stale tab' }, shown)
    expect(saved.ok).toBe(true)
    expect(window.updatePlaylist).toHaveBeenCalledWith('PL-1', { description: 'Stale tab' }, {
      still: expect.any(Function),
      base: {
        title: { value: 'Course', revision: 4 },
        description: { value: 'Old', revision: 0 },
        original_url: { value: 'https://example.test/old', revision: 2 },
      },
    })
  })

  it('reloads the Playlist when Cancel follows a changed-elsewhere refusal', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => ({}))
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    window.renderPlaylistDetail = vi.fn()
    const pane = owner()
    const intents = browserPlaylistIntents(pane, 2)
    intents.cancelEdit('PL-1')
    expect(window.prksReloadPlaylistDetail).not.toHaveBeenCalled()

    pane.ui!.playlistEditing = true
    pane.ui!.playlistEditPin = {
      playlistId: 'PL-1',
      session: pane.ui!.playlistEditSession ?? 0,
      base: Promise.resolve({ ...pinAll(shown), description: null }),
    }
    const refused = await intents.saveFields('PL-1', { ...shown, description: 'Mine' }, shown)
    expect(refused.ok).toBe(false)
    intents.cancelEdit('PL-1')
    expect(window.prksReloadPlaylistDetail).toHaveBeenCalledWith(pane, 'PL-1')
    expect(pane.ui!.playlistEditing).toBe(false)
    await Promise.resolve()
    await Promise.resolve()
    // A reload that painted nothing keeps the mark, so Edit stays closed and the next Cancel retries.
    expect(pane.ui!.playlistStale).toBe('PL-1')
    window.prksReloadPlaylistDetail = vi.fn(async () => ({ id: 'PL-1' }))
    intents.cancelEdit('PL-1')
    await vi.waitFor(() => expect(pane.ui!.playlistStale).toBeNull())
    intents.cancelEdit('PL-1')
    expect(window.prksReloadPlaylistDetail).toHaveBeenCalledOnce()
  })

  it('reloads the Playlist when Cancel follows a refusal over another tab\'s unsent edit', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => {
      throw Object.assign(new Error('Another tab on this device has an unsent change here.'),
        { prksLocalStoreCode: 'pending_elsewhere' })
    })
    window.prksReloadPlaylistDetail = vi.fn(async () => ({ id: 'PL-1' }))
    window.renderPlaylistDetail = vi.fn()
    const pane = owner()
    const intents = browserPlaylistIntents(pane, 2)
    pane.ui!.playlistEditing = true
    pane.ui!.playlistEditPin = {
      playlistId: 'PL-1',
      session: pane.ui!.playlistEditSession ?? 0,
      base: Promise.resolve(pinAll(shown)),
    }
    const refused = await intents.saveFields('PL-1', { ...shown, title: 'Workshops' }, shown)
    expect(refused).toEqual({ ok: false, message: 'Another tab on this device has an unsent change here.' })
    expect(pane.ui!.playlistStale).toBe('PL-1')
    intents.cancelEdit('PL-1')
    expect(window.prksReloadPlaylistDetail).toHaveBeenCalledWith(pane, 'PL-1')
  })

  it('refuses a changed field whose pinned base is unknown and keeps the draft', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => ({}))
    const pane = owner()
    pane.ui!.playlistEditPin = {
      playlistId: 'PL-1',
      session: 0,
      base: Promise.resolve({ ...pinAll(shown), description: null }),
    }
    const intents = browserPlaylistIntents(pane, 2)
    const refused = await intents.saveFields('PL-1', { ...shown, description: 'Mine' }, shown)
    expect(refused.ok).toBe(false)
    expect(refused.message).toContain('changed in another tab')
    expect(window.updatePlaylist).not.toHaveBeenCalled()
    expect(pane.ui?.playlistEditing).toBe(true)

    // A failed pin does not strand the session: the Save pins again against
    // the values the form showed, and is refused only when that fails too.
    const pinPlaylist = window.prksPinPlaylistEdit as ReturnType<typeof vi.fn>
    pinPlaylist.mockImplementationOnce(async () => null)
    pane.ui!.playlistEditPin = { playlistId: 'PL-1', session: 0, base: Promise.resolve(null) }
    const unavailable = await intents.saveFields('PL-1', { ...shown, title: 'Mine' }, shown)
    expect(unavailable.ok).toBe(false)
    expect(unavailable.message).toContain('cannot be changed offline yet')
    expect(window.prksPinPlaylistEdit).toHaveBeenLastCalledWith('PL-1', shown)

    pane.ui!.playlistEditPin = { playlistId: 'PL-1', session: 0, base: Promise.resolve(null) }
    const retried = await intents.saveFields('PL-1', { ...shown, title: 'Mine' }, shown)
    expect(retried.ok).toBe(true)
    expect(window.prksPinPlaylistEdit).toHaveBeenLastCalledWith('PL-1', shown)
    expect(window.updatePlaylist).toHaveBeenCalledTimes(1)
  })

  it('refreshes the details panel when a save changes nothing', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn()
    window.prksReloadPlaylistDetail = vi.fn()
    window.renderPlaylistDetail = vi.fn()
    window.updatePanelContent = vi.fn()
    const pane = owner()
    const result = await browserPlaylistIntents(pane, 2).saveFields('PL-1', { ...shown }, shown)
    expect(result.ok).toBe(true)
    expect(window.updatePlaylist).not.toHaveBeenCalled()
    expect(window.prksReloadPlaylistDetail).not.toHaveBeenCalled()
    expect(pane.ui?.playlistEditing).toBe(false)
    expect(window.renderPlaylistDetail).toHaveBeenCalled()
    expect(window.updatePanelContent).toHaveBeenCalledWith('details')
  })

  it('keeps a useful save error and does not reload', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.updatePlaylist = vi.fn(async () => {
      throw new Error('Part of this playlist is syncing or needs a decision. Try again shortly.')
    })
    window.prksReloadPlaylistDetail = vi.fn()
    const pane = owner()
    const result = await browserPlaylistIntents(pane, 2).saveFields(
      'PL-1',
      { ...shown, title: 'Changed' },
      shown,
    )
    expect(result.ok).toBe(false)
    expect(result.message).toContain('syncing')
    expect(window.prksReloadPlaylistDetail).not.toHaveBeenCalled()
    expect(pane.ui?.playlistEditing).toBe(true)
  })

  it('does not reload after the route has moved on', async () => {
    let live = true
    window.prksTabContextOwnsEntityRoute = () => live
    window.updatePlaylist = vi.fn(async () => {
      live = false
    })
    window.reorderPlaylist = vi.fn(async () => {
      live = false
    })
    window.prksReloadPlaylistDetail = vi.fn()
    window.renderPlaylistDetail = vi.fn()
    const intents = browserPlaylistIntents(owner(), 2)
    const saved = await intents.saveFields('PL-1', { ...shown, title: 'Later' }, shown)
    expect(saved.ok).toBe(true)
    expect(window.prksReloadPlaylistDetail).not.toHaveBeenCalled()
    expect(window.renderPlaylistDetail).not.toHaveBeenCalled()

    live = true
    const reordered = await intents.reorder('PL-1', ['W2', 'W1'])
    expect(reordered.ok).toBe(true)
    expect(window.reorderPlaylist).toHaveBeenCalledWith('PL-1', ['W2', 'W1'])
    expect(window.prksReloadPlaylistDetail).not.toHaveBeenCalled()
  })

  it('adds, removes, and deletes through the public wrappers', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.addWorkToPlaylist = vi.fn(async () => null)
    window.removeWorkFromPlaylist = vi.fn(async () => null)
    window.deletePlaylistFromDetail = vi.fn(async () => undefined)
    window.prksReloadPlaylistDetail = vi.fn(async () => null)
    const pane = owner()
    const intents = browserPlaylistIntents(pane, 2)
    expect((await intents.addWork('PL-1', 'W9')).ok).toBe(true)
    expect(window.addWorkToPlaylist).toHaveBeenCalledWith('PL-1', 'W9')
    expect((await intents.removeWork('PL-1', 'W9')).ok).toBe(true)
    expect(window.removeWorkFromPlaylist).toHaveBeenCalledWith('PL-1', 'W9')
    await intents.remove('PL-1', { id: 'PL-1', title: 'Course', items: [{ id: 'W1' }] })
    expect(window.deletePlaylistFromDetail).toHaveBeenCalledWith(
      pane,
      {
        id: 'PL-1',
        title: 'Course',
        items: [{ id: 'W1' }],
      },
      2,
    )
  })

  it('renames a video through the Work title family and ignores a stale completion', async () => {
    let live = true
    window.prksTabContextOwnsEntityRoute = () => live
    window.prksSaveWorkFieldDurably = vi.fn(async () => {
      live = false
      return { code: '' }
    })
    window.prksRefreshPendingWorkMetadata = vi.fn(async () => undefined)
    window.renderPlaylistDetail = vi.fn()
    const pane = owner()
    const result = await browserPlaylistIntents(pane, 2).saveWorkTitle('PL-1', 'W1', 'Renamed')
    expect(result.ok).toBe(true)
    expect(window.prksSaveWorkFieldDurably).toHaveBeenCalledWith('W1', 'title', 'Renamed', {
      label: 'Title',
    })
    expect(window.renderPlaylistDetail).not.toHaveBeenCalled()
  })

  it('lists addable videos only while this detail still owns the route', async () => {
    let live = true
    window.prksTabContextOwnsEntityRoute = () => live
    window.prksOfflineRuntimeState = () => 'online'
    window.prksInferWorkSourceKind = (work) =>
      work && typeof work === 'object' && 'kind' in work && work.kind === 'video' ? 'video' : 'pdf'
    window.fetchWorks = vi.fn(async () => {
      live = false
      return [
        { id: 'W1', title: 'Already in', kind: 'video' },
        { id: 'W2', title: 'Add me', kind: 'video' },
      ] as unknown as Array<{ id: string; title: string }>
    })
    const missed = await browserPlaylistIntents(owner(), 2).loadAddableVideos('PL-1', ['W1'])
    expect(missed).toEqual({ status: 'stale' })

    live = true
    window.prksConsumeApiError = () => null
    window.fetchWorks = vi.fn(async () => [
      { id: 'W1', title: 'Already in', kind: 'video' },
      { id: 'W2', title: 'Add me', kind: 'video' },
      { id: 'D1', title: 'Paper', kind: 'pdf' },
    ] as unknown as Array<{ id: string; title: string }>)
    const choices = await browserPlaylistIntents(owner(), 2).loadAddableVideos('PL-1', ['W1'])
    expect(choices).toEqual({ status: 'ready', choices: [{ id: 'W2', title: 'Add me' }] })
  })

  it('treats a failed catalogue as unavailable and an empty one as ready', async () => {
    window.prksTabContextOwnsEntityRoute = () => true
    window.prksOfflineRuntimeState = () => 'offline'
    window.fetchWorks = vi.fn(async () => [])
    const offline = await browserPlaylistIntents(owner(), 2).loadAddableVideos('PL-1', [])
    expect(offline).toEqual({ status: 'unavailable' })
    expect(window.fetchWorks).not.toHaveBeenCalled()

    window.prksOfflineRuntimeState = () => 'online'
    window.prksConsumeApiError = () => ({ message: 'Could not load files.' })
    window.fetchWorks = vi.fn(async () => [])
    const failed = await browserPlaylistIntents(owner(), 2).loadAddableVideos('PL-1', [])
    expect(failed).toEqual({ status: 'unavailable' })

    window.prksConsumeApiError = () => null
    const empty = await browserPlaylistIntents(owner(), 2).loadAddableVideos('PL-1', [])
    expect(empty).toEqual({ status: 'ready', choices: [] })

    const controller = new AbortController()
    controller.abort()
    window.fetchWorks = vi.fn(async () => [{ id: 'W2', title: 'Add me' }])
    const aborted = await browserPlaylistIntents(owner({ abortController: controller }), 2).loadAddableVideos(
      'PL-1',
      [],
    )
    expect(aborted).toEqual({ status: 'stale' })
  })
})
