/**
 * Bundle the typed workspace model as a classic script.
 * Output: frontend/js/workspace-model.js
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { cpSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = join(appRoot, '..')
const staging = join(appRoot, '.workspace-model-build')

rmSync(staging, { recursive: true, force: true })
mkdirSync(staging, { recursive: true })

const built = await build({
  configFile: false,
  root: appRoot,
  publicDir: false,
  logLevel: 'warn',
  build: {
    lib: {
      entry: join(appRoot, 'src/workspace/browser-entry.ts'),
      name: 'prksWorkspaceModel',
      formats: ['iife'],
      fileName: () => 'workspace-model.js',
    },
    outDir: staging,
    emptyOutDir: true,
    sourcemap: false,
    minify: false,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        extend: false,
        inlineDynamicImports: true,
        footer:
          'if (typeof module === "object" && module != null && module.exports) {\n' +
          '  module.exports = prksWorkspaceModel;\n' +
          '}\n',
      },
    },
  },
})

if (Array.isArray(built) ? built.some((item) => item == null) : !built) {
  console.error('workspace model build produced no result')
  process.exit(1)
}

const emitted = readdirSync(staging).filter((name) => name !== 'workspace-model.js')
if (emitted.length) {
  console.error('unexpected workspace model output:', emitted.join(', '))
  process.exit(1)
}

cpSync(join(staging, 'workspace-model.js'), join(repoRoot, 'frontend/js/workspace-model.js'))
rmSync(staging, { recursive: true, force: true })
