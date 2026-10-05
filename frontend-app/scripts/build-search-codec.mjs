/**
 * Bundle the Search / Saved View query codec as a classic script.
 * Output: frontend/js/search-query-codec.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { buildClassicScript } from './build-classic-script.mjs'

await buildClassicScript({
  entry: 'src/features/search/codec-browser-entry.ts',
  name: 'prksSearchQueryCodec',
  fileName: 'search-query-codec.js',
  stagingName: '.search-codec-build',
})
