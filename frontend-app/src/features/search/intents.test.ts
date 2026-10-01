import { afterEach, describe, expect, it, vi } from 'vitest'
import { browserSearchIntents, type SearchIntentOwner } from './intents'

afterEach(() => {
  delete window.prksNavigate
  delete window.prksSearchHashFromDefinition
  delete window.prksOpenSavedViewModalFromCurrentSearch
})

function owner(current: { generation: number }, routeName = 'search'): SearchIntentOwner {
  return {
    tabId: 'tab-main',
    isCurrent: (generation) => generation === current.generation,
    lastResolvedRoute: { name: routeName },
  }
}

describe('search intents', () => {
  it('navigates the owning tab through the canonical query codec', () => {
    const navigate = vi.fn()
    const toHash = vi.fn((d: Record<string, string>) => `#/hash/${d.mode}/${d.q}/${d.author}/${d.publisher}`)
    window.prksNavigate = navigate
    window.prksSearchHashFromDefinition = toHash
    const state = { generation: 2 }
    const intents = browserSearchIntents(owner(state), 2, '#/search?q=a')
    expect(intents.run({ any: false, q: ' a ', author: ' b ', publisher: '' })).toBe(true)
    expect(toHash).toHaveBeenLastCalledWith({ mode: 'advanced', q: 'a', tag: '', author: 'b', publisher: '' })
    expect(navigate).toHaveBeenLastCalledWith('#/hash/advanced/a/b/', { tabId: 'tab-main' })
    expect(intents.run({ any: true, q: 'all', author: 'ignored', publisher: 'ignored' })).toBe(true)
    expect(toHash).toHaveBeenLastCalledWith({ mode: 'all', q: 'all', tag: '', author: '', publisher: '' })
  })

  it('does not navigate an empty form', () => {
    const navigate = vi.fn()
    window.prksNavigate = navigate
    window.prksSearchHashFromDefinition = () => '#/search'
    const intents = browserSearchIntents(owner({ generation: 1 }), 1, '#/search')
    expect(intents.run({ any: true, q: '  ', author: 'a', publisher: '' })).toBe(false)
    expect(intents.run({ any: false, q: '', author: ' ', publisher: '' })).toBe(false)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('is a no-op once the owner moved to another generation or route', () => {
    const navigate = vi.fn()
    const save = vi.fn()
    window.prksNavigate = navigate
    window.prksSearchHashFromDefinition = () => '#/search?q=x'
    window.prksOpenSavedViewModalFromCurrentSearch = save
    const state = { generation: 4 }
    const stale = browserSearchIntents(owner(state), 3, '#/search?q=old')
    expect(stale.run({ any: false, q: 'x', author: '', publisher: '' })).toBe(false)
    stale.saveView()
    const elsewhere = browserSearchIntents(owner(state, 'recent'), 4, '#/search?q=old')
    expect(elsewhere.run({ any: false, q: 'x', author: '', publisher: '' })).toBe(false)
    elsewhere.saveView()
    expect(navigate).not.toHaveBeenCalled()
    expect(save).not.toHaveBeenCalled()
  })

  it('opens Save View with the owner canonical hash, not location.hash', () => {
    const save = vi.fn()
    window.prksOpenSavedViewModalFromCurrentSearch = save
    window.location.hash = '#/folders'
    browserSearchIntents(owner({ generation: 1 }), 1, '#/search?q=mine').saveView()
    expect(save).toHaveBeenCalledWith('#/search?q=mine')
  })
})
