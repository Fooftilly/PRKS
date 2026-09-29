/**
 * Bundle the typed Work route projection as a classic script.
 * Output: frontend/js/work-route-projection.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/features/work/browser-entry.ts',
  name: 'prksWorkRoute',
  fileName: 'work-route-projection.js',
  stagingName: '.work-route-build',
})
