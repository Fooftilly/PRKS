/**
 * Emit one classic IIFE from a TypeScript entry into frontend/js.
 * Does not write frontend/vue or touch the Vue application bundle.
 */
import { cpSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = join(appRoot, '..')

export async function buildClassicScript({ entry, name, fileName, stagingName }) {
  const staging = join(appRoot, stagingName)
  rmSync(staging, { recursive: true, force: true })
  mkdirSync(staging, { recursive: true })

  const built = await build({
    configFile: false,
    root: appRoot,
    publicDir: false,
    logLevel: 'warn',
    build: {
      lib: {
        entry: join(appRoot, entry),
        name,
        formats: ['iife'],
        fileName: () => fileName,
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
            `  module.exports = ${name};\n` +
            '}\n',
        },
      },
    },
  })

  if (Array.isArray(built) ? built.some((item) => item == null) : !built) {
    console.error(fileName, 'build produced no result')
    process.exit(1)
  }

  const emitted = readdirSync(staging).filter((item) => item !== fileName)
  if (emitted.length) {
    console.error('unexpected', fileName, 'output:', emitted.join(', '))
    process.exit(1)
  }

  cpSync(join(staging, fileName), join(repoRoot, 'frontend/js', fileName))
  rmSync(staging, { recursive: true, force: true })
}
