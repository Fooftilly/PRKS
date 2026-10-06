/**
 * Bundle the owner-resource registry as a classic script.
 * Output: frontend/js/owner-resource.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/lifecycle/owner-resource-entry.ts',
  name: 'prksOwnerResource',
  fileName: 'owner-resource.js',
  stagingName: '.owner-resource-build',
})
