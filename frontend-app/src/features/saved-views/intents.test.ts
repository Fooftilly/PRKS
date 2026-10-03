import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from '../../api/http'
import type { SavedView } from '../../api/saved-views'
import { browserSavedViewIntents, type SavedViewIntentOwner } from './intents'
import { SavedViewOfflineRefusal, savedViewActionMessage, type SavedViewRecords } from './records'

afterEach(() => {
  delete window.prksOpenSavedViewModal
  delete window.prksConfirmDestructive
  delete window.prksTabContextOwnsEntityRoute
  delete window.prksOpenCommandPalette
  delete window.prksNavigate
})

const VIEW: SavedView = {
  id: 'SV-1',
  name: 'Critical theory',
  search: { mode: 'all', q: 'x', tag: '', author: '', publisher: '' },
  created_at: '2026-10-03 10:00:00',
  updated_at: '2026-10-03 10:00:00',
}

function owner(state: { generation: number; entityId: string | null }): SavedViewIntentOwner {
  return {
    tabId: 'tab-main',
    isCurrent: (generation) => generation === state.generation,
    getEntity: (type) => (type === 'savedView' && state.entityId ? { id: state.entityId } : null),
    lastResolvedRoute: { name: 'saved-view-detail' },
  }
}

function indexOwner(state: { generation: number }): SavedViewIntentOwner {
  return {
    tabId: 'tab-index',
    isCurrent: (generation) => generation === state.generation,
    lastResolvedRoute: { name: 'saved-views' },
  }
}

function fakeRecords(overrides: Partial<SavedViewRecords> = {}): SavedViewRecords {
  return {
    list: vi.fn(async () => [VIEW]),
    get: vi.fn(async () => VIEW),
    create: vi.fn(async () => VIEW),
    update: vi.fn(async () => VIEW),
    remove: vi.fn(async () => {}),
    actionMessage: savedViewActionMessage,
    ...overrides,
  }
}

function confirmWith(answer: boolean | (() => Promise<boolean>)) {
  const confirm = vi.fn(typeof answer === 'function' ? answer : async () => answer)
  window.prksConfirmDestructive = confirm
  return confirm
}

describe('saved view intents', () => {
  it('opens the shared modal for the owned view only', () => {
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    const state = { generation: 2, entityId: 'SV-1' }
    browserSavedViewIntents(owner(state), 2, fakeRecords()).edit(VIEW)
    expect(open).toHaveBeenCalledWith({ viewId: 'SV-1', name: 'Critical theory', definition: VIEW.search })
    state.generation = 3
    browserSavedViewIntents(owner(state), 2, fakeRecords()).edit(VIEW)
    state.entityId = 'SV-2'
    browserSavedViewIntents(owner(state), 3, fakeRecords()).edit(VIEW)
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('uses the shared TabContext entity-route check when present', () => {
    const check = vi.fn(() => false)
    window.prksTabContextOwnsEntityRoute = check
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    const o = owner({ generation: 1, entityId: 'SV-1' })
    browserSavedViewIntents(o, 1, fakeRecords()).edit(VIEW)
    expect(check).toHaveBeenCalledWith(o, 1, 'savedView', 'SV-1', 'saved-view-detail')
    expect(open).not.toHaveBeenCalled()
  })

  it('confirms, deletes the detail view, and sends only that owner back to the index', async () => {
    const confirm = confirmWith(true)
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const records = fakeRecords()
    const state = { generation: 5, entityId: 'SV-1' }
    await expect(browserSavedViewIntents(owner(state), 5, records).remove('SV-1')).resolves.toEqual({ status: 'success' })
    expect(confirm).toHaveBeenCalledWith({
      title: 'Delete Saved View?',
      message: 'Deleting this Saved View will not delete any files.',
      confirmLabel: 'Delete Saved View',
    })
    expect(records.remove).toHaveBeenCalledWith('SV-1')
    expect(navigate).toHaveBeenCalledWith('#/views', { replace: true, tabId: 'tab-main' })
  })

  it('does not delete after a cancel, or after confirm once the detail owner left', async () => {
    const records = fakeRecords()
    confirmWith(false)
    const state = { generation: 5, entityId: 'SV-1' }
    await expect(browserSavedViewIntents(owner(state), 5, records).remove('SV-1')).resolves.toEqual({ status: 'quiet' })
    confirmWith(async () => {
      state.generation = 6
      return true
    })
    await expect(browserSavedViewIntents(owner(state), 5, records).remove('SV-1')).resolves.toEqual({ status: 'quiet' })
    expect(records.remove).not.toHaveBeenCalled()
  })

  it('reports a failed detail delete with the server text and does not navigate', async () => {
    confirmWith(true)
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const records = fakeRecords({
      remove: vi.fn(async () => {
        throw new PrksApiError('Saved View not found.', 404)
      }),
    })
    const state = { generation: 5, entityId: 'SV-1' }
    await expect(browserSavedViewIntents(owner(state), 5, records).remove('SV-1')).resolves.toEqual({
      status: 'error',
      message: 'Saved View not found.',
    })
    expect(navigate).not.toHaveBeenCalled()
  })

  it('does not navigate a detail owner that moved on while the delete ran', async () => {
    confirmWith(true)
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const state = { generation: 5, entityId: 'SV-1' }
    const records = fakeRecords({
      remove: vi.fn(async () => {
        state.generation = 6
      }),
    })
    await expect(browserSavedViewIntents(owner(state), 5, records).remove('SV-1')).resolves.toEqual({ status: 'quiet' })
    expect(records.remove).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('reads an index row and opens the modal only while that index is current', async () => {
    const state = { generation: 2 }
    let release: (view: SavedView) => void = () => {}
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    const slow = fakeRecords({
      get: () =>
        new Promise((resolve) => {
          release = resolve
        }),
    })
    const pending = browserSavedViewIntents(indexOwner(state), 2, slow).editById('SV-1')
    state.generation = 3
    release(VIEW)
    await expect(pending).resolves.toEqual({ status: 'quiet' })
    expect(open).not.toHaveBeenCalled()

    state.generation = 4
    const records = fakeRecords()
    await expect(browserSavedViewIntents(indexOwner(state), 4, records).editById('SV-1')).resolves.toEqual({
      status: 'success',
    })
    expect(records.get).toHaveBeenCalledWith('SV-1')
    expect(open).toHaveBeenCalledWith({ viewId: 'SV-1', name: 'Critical theory', definition: VIEW.search })
    await expect(
      browserSavedViewIntents(owner({ generation: 4, entityId: 'SV-1' }), 4, records).editById('SV-1'),
    ).resolves.toEqual({ status: 'quiet' })
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('reports a failed or missing index edit and stays quiet when that owner goes stale', async () => {
    const state = { generation: 2 }
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    const failing = fakeRecords({
      get: vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    })
    await expect(browserSavedViewIntents(indexOwner(state), 2, failing).editById('SV-1')).resolves.toEqual({
      status: 'error',
      message: 'Could not open Saved View.',
    })
    const missing = fakeRecords({ get: vi.fn(async () => null) })
    await expect(browserSavedViewIntents(indexOwner(state), 2, missing).editById('SV-1')).resolves.toEqual({
      status: 'error',
      message: 'Could not open Saved View.',
    })

    let rejectRead: (err: Error) => void = () => {}
    const late = fakeRecords({
      get: () =>
        new Promise((_resolve, reject) => {
          rejectRead = reject
        }),
    })
    const pending = browserSavedViewIntents(indexOwner(state), 2, late).editById('SV-1')
    state.generation = 3
    rejectRead(new Error('late failure'))
    await expect(pending).resolves.toEqual({ status: 'quiet' })
    expect(open).not.toHaveBeenCalled()
  })

  it('deletes an index row after confirm without navigating', async () => {
    confirmWith(true)
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const records = fakeRecords()
    await expect(
      browserSavedViewIntents(indexOwner({ generation: 1 }), 1, records).removeFromIndex('SV-1'),
    ).resolves.toEqual({ status: 'success' })
    expect(records.remove).toHaveBeenCalledWith('SV-1')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('reports a failed index delete and stays quiet for cancel, stale, and offline', async () => {
    confirmWith(true)
    const state = { generation: 1 }
    const failing = fakeRecords({
      remove: vi.fn(async () => {
        throw new PrksApiError('PRKS could not complete the request.', 500)
      }),
    })
    await expect(browserSavedViewIntents(indexOwner(state), 1, failing).removeFromIndex('SV-1')).resolves.toEqual({
      status: 'error',
      message: 'Could not delete Saved View.',
    })
    const offline = fakeRecords({
      remove: vi.fn(async () => {
        throw new SavedViewOfflineRefusal()
      }),
    })
    await expect(browserSavedViewIntents(indexOwner(state), 1, offline).removeFromIndex('SV-1')).resolves.toEqual({
      status: 'quiet',
    })

    confirmWith(false)
    const records = fakeRecords()
    await expect(browserSavedViewIntents(indexOwner(state), 1, records).removeFromIndex('SV-1')).resolves.toEqual({
      status: 'quiet',
    })
    expect(records.remove).not.toHaveBeenCalled()

    confirmWith(true)
    let rejectDelete: (err: Error) => void = () => {}
    const late = fakeRecords({
      remove: vi.fn(
        () =>
          new Promise<void>((_resolve, reject) => {
            rejectDelete = reject
          }),
      ),
    })
    const lateState = { generation: 2 }
    const pending = browserSavedViewIntents(indexOwner(lateState), 2, late).removeFromIndex('SV-1')
    await vi.waitFor(() => expect(late.remove).toHaveBeenCalled())
    lateState.generation = 3
    rejectDelete(new PrksApiError('late failure', 500))
    await expect(pending).resolves.toEqual({ status: 'quiet' })
  })

  it('opens the command palette from the empty index only while that index is current', () => {
    const open = vi.fn()
    window.prksOpenCommandPalette = open
    const state = { generation: 1 }
    browserSavedViewIntents(indexOwner(state), 1, fakeRecords()).openSearch()
    expect(open).toHaveBeenCalledTimes(1)
    state.generation = 2
    browserSavedViewIntents(indexOwner(state), 1, fakeRecords()).openSearch()
    expect(open).toHaveBeenCalledTimes(1)
    browserSavedViewIntents(owner({ generation: 2, entityId: null }), 2, fakeRecords()).openSearch()
    expect(open).toHaveBeenCalledTimes(1)
  })
})
