import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import vue from '@vitejs/plugin-vue'
import { build } from 'vite'
import cssInjectedByJsPlugin from 'vite-plugin-css-injected-by-js'
import { describe, expect, it } from 'vitest'

const require = createRequire(join(process.cwd(), 'package.json'))
const vueEntry = require.resolve('vue')
const STYLE_MARKER = 'color:#123456'

function filesUnder(dir) {
  const found = []
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) found.push(...filesUnder(path))
    else found.push(path)
  }
  return found
}

async function buildStyledSfc(root, outDir) {
  await build({
    root,
    configFile: false,
    publicDir: false,
    logLevel: 'error',
    resolve: { alias: { vue: vueEntry } },
    plugins: [vue(), cssInjectedByJsPlugin()],
    build: {
      outDir,
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
  })
}

describe('Vite CSS injection', () => {
  it('inlines an SFC style into prks-vue.js without a stylesheet, twice', async () => {
    const root = mkdtempSync(join(tmpdir(), 'prks-vue-css-'))
    const outRoot = mkdtempSync(join(tmpdir(), 'prks-vue-out-'))
    try {
      mkdirSync(join(root, 'src'))
      writeFileSync(
        join(root, 'index.html'),
        '<div id="app"></div>\n<script type="module" src="./src/main.ts"></script>\n',
      )
      writeFileSync(
        join(root, 'src/App.vue'),
        `<template><span data-marker="styled">x</span></template>\n<style>span[data-marker="styled"]{${STYLE_MARKER}}</style>\n`,
      )
      writeFileSync(
        join(root, 'src/main.ts'),
        "import { createApp } from 'vue'\nimport App from './App.vue'\ncreateApp(App).mount('#app')\n",
      )
      const firstDir = join(outRoot, 'a')
      const secondDir = join(outRoot, 'b')
      await buildStyledSfc(root, firstDir)
      await buildStyledSfc(root, secondDir)
      for (const dir of [firstDir, secondDir]) {
        const files = filesUnder(dir)
        expect(files.some((path) => path.endsWith('.css'))).toBe(false)
        const js = readFileSync(join(dir, 'prks-vue.js'), 'utf8')
        expect(js).toContain(STYLE_MARKER)
        expect(js).toContain('createElement')
      }
      const first = readFileSync(join(firstDir, 'prks-vue.js'))
      const second = readFileSync(join(secondDir, 'prks-vue.js'))
      const hash = (bytes) => createHash('sha256').update(bytes).digest('hex')
      expect(hash(first)).toBe(hash(second))
    } finally {
      rmSync(root, { recursive: true, force: true })
      rmSync(outRoot, { recursive: true, force: true })
    }
  })
})
