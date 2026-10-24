/**
 * Classic-script entry. The maintainer build emits `frontend/js/tab-leave.js`
 * as the global `prksTabLeave`. Vue does not import this module.
 *
 * Feature probes register on that global. The workspace coordinator and
 * `prksRenderTabRoute` are the only callers of `run` / `runBatch`.
 */
import { createTabLeave } from './tab-leave'

const leave = createTabLeave()

export const run = leave.run
export const runBatch = leave.runBatch
export const registerProbe = leave.registerProbe
export const registerFlush = leave.registerFlush
export const assessOwner = leave.assessOwner
export const flushOwner = leave.flushOwner
