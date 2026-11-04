/**
 * Classic-script entry. The maintainer build emits `frontend/js/editor-recovery.js`
 * as the global `prksEditorRecovery`. Loading the script starts nothing;
 * `runtime()` creates the page's single runtime on first use, once the page
 * has a confirmed browser scope. Research Notes
 * (`works.js`) is the consumer since #466 slice 2; Settings → Diagnostics
 * reads every kind through `diagnostics()` (slice 5).
 */
import { createRecoveryDiagnostics, type RecoveryDiagnostics } from './editor-recovery/diagnostics'
import { createEditorRecoveryRuntime, type EditorRecoveryRuntime } from './editor-recovery/runtime'
import { isScopeKey, recoveryNames, type RecoveryNames } from './editor-recovery/schema'

export * from './editor-recovery/schema'
export { fingerprintText, sameBody, sameBaseIdentity } from './editor-recovery/fingerprint'
export { createRecoveryStore, RecoveryStoreError, forkedDraftId } from './editor-recovery/store'
export { createPageIdentity } from './editor-recovery/identity'
export { classifyLineage, isAdoptable } from './editor-recovery/lineage'
export { createWriterRegistry } from './editor-recovery/writer'
export { planEmergency, readEmergencyKeys, mergeEmergencyEntries, mayHoldUnreadable } from './editor-recovery/emergency'
export { planResearchNotesRestore } from './editor-recovery/research-notes'
export { createEditorRecoveryRuntime }

let pageRuntime: EditorRecoveryRuntime | null = null
let pageDiagnostics: RecoveryDiagnostics | null = null

interface BrowserScopeLike {
  current(): { key?: unknown } | null
}

/** The page's confirmed browser scope key (`frontend/js/browser-scope.js`), or null. */
function confirmedScopeKey(): string | null {
  const scope = (globalThis as { prksBrowserScope?: BrowserScopeLike }).prksBrowserScope
  try {
    const current = scope && typeof scope.current === 'function' ? scope.current() : null
    return current && isScopeKey(current.key) ? current.key : null
  } catch {
    return null
  }
}

/**
 * The names of the page's recovery storage for its confirmed browser scope,
 * or null without one. For readers that must not build the runtime (a
 * cheap "may anything be stored here?" check): a runtime made later uses
 * the same names, unless it already exists, in which case these are its.
 */
export function scopeNames(): RecoveryNames | null {
  if (pageRuntime) return pageRuntime.names
  const scopeKey = confirmedScopeKey()
  return scopeKey ? recoveryNames(scopeKey) : null
}

/**
 * The page's runtime, bound to the signed-in browser scope (#629), or null
 * while the page has none: recovery then keeps nothing (fail closed) and
 * a later call, once the scope is confirmed, creates it. The runtime keeps
 * that scope for the life of the page: drafts typed under an account stay
 * that account's, even after it signs out or another account signs in.
 */
export function runtime(): EditorRecoveryRuntime | null {
  if (pageRuntime) return pageRuntime
  const scopeKey = confirmedScopeKey()
  if (!scopeKey) return null
  pageRuntime = createEditorRecoveryRuntime({ names: recoveryNames(scopeKey) })
  return pageRuntime
}

/** Settings → Diagnostics (#466 slice 5) over this page's runtime, or null without a scope. Starts nothing until it is asked to read. */
export function diagnostics(): RecoveryDiagnostics | null {
  if (pageDiagnostics) return pageDiagnostics
  const rt = runtime()
  if (!rt) return null
  pageDiagnostics = createRecoveryDiagnostics(rt)
  return pageDiagnostics
}
