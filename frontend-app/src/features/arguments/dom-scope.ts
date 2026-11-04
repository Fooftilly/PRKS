import type { InjectionKey } from 'vue'

/** Per-owner prefix for Arguments tab and panel ids. */
export const argumentDomScopeKey: InjectionKey<string> = Symbol('prks-argument-dom-scope')

const anonymousScopes = new WeakMap<object, string>()
let anonymousSeq = 0

/**
 * Stable id prefix for one Arguments owner.
 * A tab id survives a retained refresh even when the owner object is replaced.
 * An owner with no tab id keeps one prefix for that object.
 */
export function argumentDomScope(owner: object | null | undefined): string {
  const tabId =
    owner && 'tabId' in owner && typeof (owner as { tabId?: unknown }).tabId === 'string'
      ? (owner as { tabId: string }).tabId
      : ''
  const safe = tabId.replace(/[^A-Za-z0-9_-]/g, '-')
  if (safe) return `prks-arg-${safe}`
  if (!owner) return 'prks-arg-pane'
  const existing = anonymousScopes.get(owner)
  if (existing) return existing
  anonymousSeq += 1
  const scope = `prks-arg-pane-${anonymousSeq}`
  anonymousScopes.set(owner, scope)
  return scope
}
