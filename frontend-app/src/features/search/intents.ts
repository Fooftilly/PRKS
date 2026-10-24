import { hashFromDefinition } from './codec'
import type { SearchFormDraft } from './types'

/** Owning TabContext fields Search intents need. Not a second route model. */
export interface SearchIntentOwner {
  tabId?: string
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
}

export interface SearchIntents {
  /** Navigate this owner to the submitted search. False when stale or empty. */
  run(draft: SearchFormDraft): boolean
  /** Open the shared Saved View modal for this owner's search. */
  saveView(): void
}

function ownsSearch(owner: SearchIntentOwner | null | undefined, generation: number): owner is SearchIntentOwner {
  if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!route && route.name === 'search'
}

function definitionOf(draft: SearchFormDraft): Record<string, string> | null {
  const q = draft.q.trim()
  if (draft.any) {
    if (!q) return null
    return { mode: 'all', q, tag: '', author: '', publisher: '' }
  }
  const author = draft.author.trim()
  const publisher = draft.publisher.trim()
  if (!q && !author && !publisher) return null
  return { mode: 'advanced', q, tag: '', author, publisher }
}

/**
 * Search hashes come from the canonical query codec (`hashFromDefinition`).
 * Navigation targets the owning TabContext. Save View passes this owner's
 * canonical hash to the shared modal instead of reading `location.hash`.
 * A stale owner does neither.
 */
export function browserSearchIntents(
  owner: SearchIntentOwner | null,
  generation: number,
  canonicalHash: string,
): SearchIntents {
  return {
    run(draft) {
      if (!ownsSearch(owner, generation)) return false
      const definition = definitionOf(draft)
      if (!definition) return false
      const navigate = window.prksNavigate
      if (typeof navigate !== 'function') return false
      navigate(hashFromDefinition(definition), { tabId: owner.tabId })
      return true
    },
    saveView() {
      if (!ownsSearch(owner, generation)) return
      window.prksOpenSavedViewModalFromCurrentSearch?.(canonicalHash)
    },
  }
}
