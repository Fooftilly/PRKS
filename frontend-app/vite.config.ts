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

export default defineConfig({
  plugins: [vue(), ...(storybook ? [] : [cssInjectedByJsPlugin()])],
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
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'css-inject.integration.test.mjs'],
  },
})
