/**
 * Bundle the editor draft recovery store (#466) as a classic script.
 * Output: frontend/js/editor-recovery.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/lifecycle/editor-recovery-entry.ts',
  name: 'prksEditorRecovery',
  fileName: 'editor-recovery.js',
  stagingName: '.editor-recovery-build',
})
