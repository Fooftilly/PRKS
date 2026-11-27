/**
 * Bundle the shared sync ACK reconciliation core as a classic script.
 * Output: frontend/js/sync-reconcile.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/sync/reconcile-entry.ts',
  name: 'prksSyncReconcile',
  fileName: 'sync-reconcile.js',
  stagingName: '.sync-reconcile-build',
})
