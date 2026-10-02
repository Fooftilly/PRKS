import { afterEach, describe, expect, it, vi } from 'vitest'
import { browserTagsIntents, type TagsIntentOwner } from './intents'

afterEach(() => {
  delete window.prksNavigate
  delete window.prksTagsAddAlias
  delete window.prksTagsRemoveAlias
  delete window.prksTagsDelete
  delete window.prksTagsMerge
  delete window.prksReloadTagsVocabulary
  delete window.prksConfirmDestructive
  delete window.prksTagVocabularyMessage
})

function owner(state: { generation: number }): TagsIntentOwner {
  return {
    tabId: 'tab-tags',
    isCurrent: (generation) => generation === state.generation,
    lastResolvedRoute: { name: 'tags' },
  }
}

describe('Tags intents', () => {
  it('opens search on the owning tab only while that page is current', () => {
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const state = { generation: 2 }
    browserTagsIntents(owner(state), 2).openTag('Ada Lovelace')
    expect(navigate).toHaveBeenCalledWith('#/search?tag=Ada%20Lovelace', { tabId: 'tab-tags' })
    state.generation = 3
    browserTagsIntents(owner(state), 2).openTag('Ada Lovelace')
    expect(navigate).toHaveBeenCalledTimes(1)
  })

  it('reloads the alias dialog only after a current add', async () => {
    const state = { generation: 2 }
    const add = vi.fn(async () => ({ ok: true }))
    const reload = vi.fn(async () => true)
    window.prksTagsAddAlias = add
    window.prksReloadTagsVocabulary = reload
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const outcome = await browserTagsIntents(owner(state), 2).addAlias('t1', '  Latin  ')
    expect(outcome.status).toBe('success')
    expect(add).toHaveBeenCalledWith('t1', 'Latin')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 2, { aliasTagId: 't1' })
    expect(fetchMock).not.toHaveBeenCalled()
    await browserTagsIntents(owner(state), 2).addAlias('t1', '   ')
    expect(add).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('does not reload aliases onto an owner that left during the add', async () => {
    const state = { generation: 2 }
    window.prksTagsAddAlias = async () => {
      state.generation = 3
      return { ok: true }
    }
    const reload = vi.fn(async () => true)
    window.prksReloadTagsVocabulary = reload
    const outcome = await browserTagsIntents(owner(state), 2).addAlias('t1', 'Latin')
    expect(outcome.status).toBe('quiet')
    expect(reload).not.toHaveBeenCalled()
  })

  it('stays quiet when the offline alias guard refuses the write', async () => {
    const reload = vi.fn()
    window.prksReloadTagsVocabulary = reload
    window.prksTagsAddAlias = async () => ({ ok: false, reason: 'offline' })
    const outcome = await browserTagsIntents(owner({ generation: 1 }), 1).addAlias('t1', 'Latin')
    expect(outcome.status).toBe('quiet')
    expect(reload).not.toHaveBeenCalled()
  })

  it('does not delete after confirm once the owner has left', async () => {
    const state = { generation: 4 }
    const remove = vi.fn(async () => ({ ok: true }))
    window.prksTagsDelete = remove
    window.prksConfirmDestructive = async () => {
      state.generation = 5
      return true
    }
    const outcome = await browserTagsIntents(owner(state), 4).remove('t1', 'Alpha')
    expect(outcome.status).toBe('quiet')
    expect(remove).not.toHaveBeenCalled()
  })

  it('deletes through the durable wrapper and reloads without reopening the dialog', async () => {
    const remove = vi.fn(async () => ({ ok: true }))
    const reload = vi.fn(async () => true)
    window.prksTagsDelete = remove
    window.prksReloadTagsVocabulary = reload
    window.prksConfirmDestructive = async (opts) => {
      expect(opts.title).toBe('Delete tag “Alpha”?')
      expect(opts.confirmLabel).toBe('Delete tag')
      return true
    }
    const outcome = await browserTagsIntents(owner({ generation: 4 }), 4).remove('t1', 'Alpha')
    expect(outcome.status).toBe('success')
    expect(remove).toHaveBeenCalledWith('t1')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 4, null)
  })

  it('reports a delete failure with the vocabulary message and does not reload', async () => {
    window.prksConfirmDestructive = async () => true
    window.prksTagsDelete = async () => {
      throw Object.assign(new Error('busy'), { prksLocalStoreCode: 'scope_busy' })
    }
    window.prksTagVocabularyMessage = (_err, action) => `Could not ${action} locally.`
    const reload = vi.fn()
    window.prksReloadTagsVocabulary = reload
    const outcome = await browserTagsIntents(owner({ generation: 1 }), 1).remove('t1', 'Alpha')
    expect(outcome).toEqual({ status: 'error', message: 'Could not delete this tag locally.' })
    expect(reload).not.toHaveBeenCalled()
  })

  it('does not resume an alias dialog the user closed during the write', async () => {
    let open: string | null = 't1'
    const reload = vi.fn(async () => true)
    window.prksTagsAddAlias = async () => {
      open = null
      return { ok: true }
    }
    window.prksReloadTagsVocabulary = reload
    const outcome = await browserTagsIntents(owner({ generation: 2 }), 2, {
      openAliasTagId: () => open,
    }).addAlias('t1', 'Latin')
    expect(outcome.status).toBe('success')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 2, null)
  })

  it('resumes the tag the user switched to, not the write that was in flight', async () => {
    let open: string | null = 't1'
    const reload = vi.fn(async () => false)
    window.prksTagsRemoveAlias = async () => {
      open = 't2'
      return { ok: true }
    }
    window.prksReloadTagsVocabulary = reload
    const outcome = await browserTagsIntents(owner({ generation: 2 }), 2, {
      openAliasTagId: () => open,
    }).removeAlias('t1', 'Latin')
    expect(outcome.status).toBe('success')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 2, { aliasTagId: 't2' })
  })

  it('keeps a successful write quiet in the dialog when the refresh does not repaint', async () => {
    const reload = vi.fn(async () => false)
    window.prksTagsAddAlias = async () => ({ ok: true })
    window.prksReloadTagsVocabulary = reload
    const outcome = await browserTagsIntents(owner({ generation: 2 }), 2).addAlias('t1', 'Latin')
    expect(outcome.status).toBe('success')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 2, { aliasTagId: 't1' })
  })

  it('merges through the durable wrapper only while the owner is still current', async () => {
    const state = { generation: 6 }
    const merge = vi.fn(async () => ({ ok: true }))
    const reload = vi.fn(async () => true)
    window.prksTagsMerge = merge
    window.prksReloadTagsVocabulary = reload
    const outcome = await browserTagsIntents(owner(state), 6).merge('src', 'dst')
    expect(outcome.status).toBe('success')
    expect(merge).toHaveBeenCalledWith('src', 'dst')
    expect(reload).toHaveBeenCalledWith(expect.anything(), 6, null)
    state.generation = 7
    const stale = await browserTagsIntents(owner(state), 6).merge('src', 'dst')
    expect(stale.status).toBe('quiet')
    expect(merge).toHaveBeenCalledTimes(1)
  })
})
