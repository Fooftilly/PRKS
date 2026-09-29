/**
 * Bundle the typed workspace model as a classic script.
 * Output: frontend/js/workspace-model.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/workspace/browser-entry.ts',
  name: 'prksWorkspaceModel',
  fileName: 'workspace-model.js',
  stagingName: '.workspace-model-build',
})
