import vue from '@vitejs/plugin-vue'
import cssInjectedByJsPlugin from 'vite-plugin-css-injected-by-js'
import { defineConfig } from 'vitest/config'

// Maintainer build only. Production PRKS loads frontend/vue/prks-vue.js
// from the Python static root; this config never runs inside prks_app.py.
// vite-plugin-css-injected-by-js folds extracted CSS into that bundle.
// Storybook sets STORYBOOK=true before loading this file. The catalog must
// not inject CSS into the production bundle path. Avoid @types/node: this
// file is in the Vue tsconfig, and Node types are not a frontend dependency.
const storybook =
  (globalThis as { process?: { env?: { STORYBOOK?: string } } }).process?.env?.STORYBOOK === 'true'

// PRKS's Content-Security-Policy refuses inline <style> elements (style-src
// has no 'unsafe-inline'), so the bundled CSS is applied as a constructable
// stylesheet instead: CSSOM is not inline style. The plugin inlines this
// function's source into prks-vue.js, so it must stay self-contained.
// Without constructable stylesheets the components render unstyled rather
// than failing before Vue mounts; there is no inline fallback under the CSP.
export function injectPrksVueCss(cssCode: string): void {
  if (typeof CSSStyleSheet !== 'function' || !Array.isArray(document.adoptedStyleSheets)) return
  const sheet = new CSSStyleSheet()
  sheet.replaceSync(cssCode)
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet]
}

export default defineConfig({
  plugins: [vue(), ...(storybook ? [] : [cssInjectedByJsPlugin({ injectCodeFunction: injectPrksVueCss })])],
  publicDir: false,
  base: '/vue/',
  build: {
    outDir: '../frontend/vue',
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      output: {
        entryFileNames: 'prks-vue.js',
        chunkFileNames: 'prks-vue-[name].js',
        assetFileNames: 'prks-vue[extname]',
        codeSplitting: false,
      },
    },
  },
  server: {
    fs: {
      allow: ['..'],
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'css-inject.integration.test.mjs', 'openapi-types.integration.test.mjs'],
  },
})
