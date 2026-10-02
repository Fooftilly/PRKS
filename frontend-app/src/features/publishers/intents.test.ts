import { afterEach, describe, expect, it, vi } from 'vitest'
import { browserPublishersIntents, type PublishersIntentOwner } from './intents'

afterEach(() => {
  delete window.prksNavigate
  delete window.prksPublishersCreate
  delete window.prksPublishersAddAlias
  delete window.prksPublishersRemoveAlias
  delete window.prksPublishersDelete
  delete window.prksReloadPublishersPage
  delete window.prksConfirmDestructive
})

function owner(state: { generation: number }): PublishersIntentOwner {
  return {
    tabId: 'tab-publishers',
    isCurrent: (generation) => generation === state.generation,
    lastResolvedRoute: { name: 'publishers' },
  }
}

describe('Publishers intents', () => {
  it('opens search on the owning tab only while that page is current', () => {
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const state = { generation: 2 }
    browserPublishersIntents(owner(state), 2).openPublisher('Oxford University Press')
    expect(navigate).toHaveBeenCalledWith('#/search?publisher=Oxford%20University%20Press', {
      tabId: 'tab-publishers',
    })
    state.generation = 3
    browserPublishersIntents(owner(state), 2).openPublisher('Oxford University Press')
    expect(navigate).toHaveBeenCalledTimes(1)
  })

  it('reloads the list after a current create and does not fetch from Vue', async () => {
    const create = vi.fn(async () => ({ ok: true }))
    const reload = vi.fn(async () => true)
    window.prksPublishersCreate = create
    window.prksReloadPublishersPage = reload
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const outcome = await browserPublishersIntents(owner({ generation: 2 }), 2).create('  OUP  ')
    expect(outcome.status).toBe('success')
    expect(create).toHaveBeenCalledWith('OUP')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 2, null)
    expect(fetchMock).not.toHaveBeenCalled()
    await browserPublishersIntents(owner({ generation: 2 }), 2).create('   ')
    expect(create).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('does not reload a publisher onto an owner that left during the create', async () => {
    const state = { generation: 2 }
    window.prksPublishersCreate = async () => {
      state.generation = 3
      return { ok: true }
    }
    const reload = vi.fn(async () => true)
    window.prksReloadPublishersPage = reload
    const outcome = await browserPublishersIntents(owner(state), 2).create('OUP')
    expect(outcome.status).toBe('quiet')
    expect(reload).not.toHaveBeenCalled()
  })

  it('reloads the alias dialog only after a current alias add', async () => {
    const add = vi.fn(async () => ({ ok: true }))
    const reload = vi.fn(async () => true)
    window.prksPublishersAddAlias = add
    window.prksReloadPublishersPage = reload
    const outcome = await browserPublishersIntents(owner({ generation: 4 }), 4).addAlias('p1', ' Oxford ')
    expect(outcome.status).toBe('success')
    expect(add).toHaveBeenCalledWith('p1', 'Oxford')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 4, { aliasPublisherId: 'p1' })
  })

  it('stays quiet when the offline guard refuses the write', async () => {
    const reload = vi.fn()
    window.prksReloadPublishersPage = reload
    window.prksPublishersCreate = async () => ({ ok: false, reason: 'offline' })
    const outcome = await browserPublishersIntents(owner({ generation: 1 }), 1).create('OUP')
    expect(outcome.status).toBe('quiet')
    expect(reload).not.toHaveBeenCalled()
  })

  it('does not delete after confirm once the owner has left', async () => {
    const state = { generation: 4 }
    const remove = vi.fn(async () => ({ ok: true }))
    window.prksPublishersDelete = remove
    window.prksConfirmDestructive = async () => {
      state.generation = 5
      return true
    }
    const outcome = await browserPublishersIntents(owner(state), 4).remove('p1', 'OUP')
    expect(outcome.status).toBe('quiet')
    expect(remove).not.toHaveBeenCalled()
  })

  it('deletes through the online wrapper and reloads without reopening the dialog', async () => {
    const remove = vi.fn(async () => ({ ok: true }))
    const reload = vi.fn(async () => true)
    window.prksPublishersDelete = remove
    window.prksReloadPublishersPage = reload
    window.prksConfirmDestructive = async (opts) => {
      expect(opts.title).toBe('Delete publisher “OUP”?')
      expect(opts.confirmLabel).toBe('Delete publisher')
      return true
    }
    const outcome = await browserPublishersIntents(owner({ generation: 4 }), 4).remove('p1', 'OUP')
    expect(outcome.status).toBe('success')
    expect(remove).toHaveBeenCalledWith('p1')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 4, null)
  })
})
