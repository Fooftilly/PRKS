import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'
import { inlineCssIntoJs } from './inline-css-plugin.ts'

// Maintainer build only. Production PRKS loads frontend/vue/prks-vue.js
// from the Python static root; this config never runs inside prks_app.py.
// Extracted CSS is inlined into that bundle. Do not emit a separate stylesheet.
export default defineConfig({
  plugins: [vue(), inlineCssIntoJs()],
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
    include: ['src/**/*.test.ts', 'inline-css-plugin.test.ts'],
  },
})
