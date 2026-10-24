/**
 * Bundle the typed route model as a classic script.
 * Output: frontend/js/route-model.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/routing/route-model-entry.ts',
  name: 'prksRouteModel',
  fileName: 'route-model.js',
  stagingName: '.route-model-build',
})
