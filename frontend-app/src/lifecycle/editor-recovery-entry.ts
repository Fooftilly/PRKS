/**
 * Classic-script entry. The maintainer build emits `frontend/js/editor-recovery.js`
 * as the global `prksEditorRecovery`. Slice 1 of #466 has no consumer: loading
 * the script starts nothing. `runtime()` creates the page's single runtime on
 * first use.
 */
import { createEditorRecoveryRuntime, type EditorRecoveryRuntime } from './editor-recovery/runtime'

export * from './editor-recovery/schema'
export { fingerprintText, sameBody, sameBaseIdentity } from './editor-recovery/fingerprint'
export { createRecoveryStore, RecoveryStoreError, forkedDraftId } from './editor-recovery/store'
export { createPageIdentity } from './editor-recovery/identity'
export { classifyLineage, isAdoptable } from './editor-recovery/lineage'
export { createWriterRegistry } from './editor-recovery/writer'
export { planEmergency, readEmergencyKeys, mergeEmergencyEntries } from './editor-recovery/emergency'
export { createEditorRecoveryRuntime }

let pageRuntime: EditorRecoveryRuntime | null = null

export function runtime(): EditorRecoveryRuntime {
  if (!pageRuntime) pageRuntime = createEditorRecoveryRuntime()
  return pageRuntime
}
