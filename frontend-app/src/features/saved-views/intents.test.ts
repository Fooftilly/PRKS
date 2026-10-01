import { afterEach, describe, expect, it, vi } from 'vitest'
import { browserSavedViewIntents, type SavedViewIntentOwner } from './intents'

afterEach(() => {
  delete window.prksOpenSavedViewModal
  delete window.prksDeleteSavedViewFromDetail
  delete window.prksDeleteSavedViewFromIndex
  delete window.prksTabContextOwnsEntityRoute
  delete window.fetchSavedView
  delete window.prksOpenCommandPalette
  delete window.prksNavigate
})

const VIEW = { id: 'SV-1', name: 'Critical theory', search: { mode: 'all', q: 'x', tag: '', author: '', publisher: '' } }

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

describe('saved view intents', () => {
  it('opens the shared modal for the owned view only', () => {
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    const state = { generation: 2, entityId: 'SV-1' }
    browserSavedViewIntents(owner(state), 2).edit(VIEW)
    expect(open).toHaveBeenCalledWith({ viewId: 'SV-1', name: 'Critical theory', definition: VIEW.search })
    state.generation = 3
    browserSavedViewIntents(owner(state), 2).edit(VIEW)
    state.entityId = 'SV-2'
    browserSavedViewIntents(owner(state), 3).edit(VIEW)
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('deletes through the canonical wrapper with a live still fence and the owner tab', async () => {
    const state = { generation: 5, entityId: 'SV-1' }
    let fence: (() => boolean) | undefined
    const del = vi.fn(async (_id: string, still?: () => boolean) => {
      fence = still
    })
    window.prksDeleteSavedViewFromDetail = del
    await browserSavedViewIntents(owner(state), 5).remove('SV-1')
    expect(del).toHaveBeenCalledWith('SV-1', expect.any(Function), 'tab-main')
    expect(fence?.()).toBe(true)
    state.generation = 6
    expect(fence?.()).toBe(false)
    await browserSavedViewIntents(owner(state), 5).remove('SV-1')
    expect(del).toHaveBeenCalledTimes(1)
  })

  it('uses the shared TabContext entity-route check when present', () => {
    const check = vi.fn(() => false)
    window.prksTabContextOwnsEntityRoute = check
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    const o = owner({ generation: 1, entityId: 'SV-1' })
    browserSavedViewIntents(o, 1).edit(VIEW)
    expect(check).toHaveBeenCalledWith(o, 1, 'savedView', 'SV-1', 'saved-view-detail')
    expect(open).not.toHaveBeenCalled()
  })

  it('fetches an index row and opens the modal only while that index is current', async () => {
    const state = { generation: 2 }
    let release: (view: typeof VIEW) => void = () => {}
    window.fetchSavedView = () => new Promise((resolve) => {
      release = resolve
    })
    const open = vi.fn()
    window.prksOpenSavedViewModal = open
    const pending = browserSavedViewIntents(indexOwner(state), 2).editById('SV-1')
    state.generation = 3
    release(VIEW)
    await pending
    expect(open).not.toHaveBeenCalled()

    state.generation = 4
    window.fetchSavedView = async () => VIEW
    await browserSavedViewIntents(indexOwner(state), 4).editById('SV-1')
    expect(open).toHaveBeenCalledWith({ viewId: 'SV-1', name: 'Critical theory', definition: VIEW.search })
    await browserSavedViewIntents(owner({ generation: 4, entityId: 'SV-1' }), 4).editById('SV-1')
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('deletes an index row through the in-place wrapper and does not navigate itself', async () => {
    const state = { generation: 1 }
    let fence: (() => boolean) | undefined
    const del = vi.fn(async (_id: string, still?: () => boolean) => {
      fence = still
    })
    window.prksDeleteSavedViewFromIndex = del
    window.prksNavigate = vi.fn()
    await browserSavedViewIntents(indexOwner(state), 1).removeFromIndex('SV-1')
    expect(del).toHaveBeenCalledWith('SV-1', expect.any(Function), 'tab-index')
    expect(fence?.()).toBe(true)
    expect(window.prksNavigate).not.toHaveBeenCalled()
    state.generation = 2
    expect(fence?.()).toBe(false)
    await browserSavedViewIntents(indexOwner(state), 1).removeFromIndex('SV-1')
    expect(del).toHaveBeenCalledTimes(1)
  })

  it('opens the command palette from the empty index only while that index is current', () => {
    const open = vi.fn()
    window.prksOpenCommandPalette = open
    const state = { generation: 1 }
    browserSavedViewIntents(indexOwner(state), 1).openSearch()
    expect(open).toHaveBeenCalledTimes(1)
    state.generation = 2
    browserSavedViewIntents(indexOwner(state), 1).openSearch()
    expect(open).toHaveBeenCalledTimes(1)
    browserSavedViewIntents(owner({ generation: 2, entityId: null }), 2).openSearch()
    expect(open).toHaveBeenCalledTimes(1)
  })
})
