/**
 * Bundle the TabContext leave preflight as a classic script.
 * Output: frontend/js/tab-leave.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/lifecycle/browser-entry.ts',
  name: 'prksTabLeave',
  fileName: 'tab-leave.js',
  stagingName: '.tab-leave-build',
})
