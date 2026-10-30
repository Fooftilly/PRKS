import { ESLint } from 'eslint'
import { describe, expect, it } from 'vitest'

// Vitest rewrites import.meta.url to /@fs/ URLs. The real test process
// working directory is frontend-app, so use it for ESLint fixture paths
// without adding Node type declarations to the browser-typed Vue project.
const vueRoot = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
const repoRoot = `${vueRoot}/..`

const forbidden = [
  "window.dispatchEvent(new Event('resize'))",
  'window.dispatchEvent(new Event("resize", { bubbles: true }))',
  "globalThis.dispatchEvent(new UIEvent('resize'))",
  "window['dispatchEvent'](new CustomEvent('resize'))",
  "dispatchEvent(new Event('resize'))",
  "self.dispatchEvent(new Event('resize'))",
  "globalThis.window.dispatchEvent(new Event('resize'))",
  "window.dispatchEvent(new Event(`resize`))",
]

const allowed = [
  "window.addEventListener('resize', () => {})",
  'new ResizeObserver(() => {}).observe(document.body)',
  "document.body.dispatchEvent(new Event('resize'))",
  "window.dispatchEvent(new Event('scroll'))",
  "window.dispatchEvent(new CustomEvent('layout-resized'))",
  "function dispatchEvent(event) { document.body.dispatchEvent(event) } dispatchEvent(new Event('resize'))",
  "function mount(window) { window.dispatchEvent(new Event('resize')) }",
  "function mount(globalThis) { globalThis.dispatchEvent(new Event('resize')) }",
]

async function check(eslint: ESLint, filePath: string, code: string, expected: number) {
  const [result] = await eslint.lintText(code, { filePath })
  const violations = result.messages.filter((message) =>
    message.ruleId === 'prks/no-synthetic-window-resize')
  expect(violations, code).toHaveLength(expected)
  if (expected) {
    expect(violations[0]?.message).toContain('ResizeObserver')
  }
}

describe('synthetic window resize ESLint guard (#193)', () => {
  it('rejects synthetic global resizes in legacy JavaScript, not real observation', async () => {
    const eslint = new ESLint({
      cwd: repoRoot,
      overrideConfigFile: `${repoRoot}/eslint.config.mjs`,
    })
    const file = `${repoRoot}/frontend/js/fake-resize-guard-fixture.js`
    for (const code of forbidden) await check(eslint, file, code, 1)
    for (const code of allowed) await check(eslint, file, code, 0)
  })

  it('rejects the same synthetic resizes in Vue TypeScript', async () => {
    const eslint = new ESLint({
      cwd: vueRoot,
      overrideConfigFile: `${vueRoot}/eslint.config.js`,
    })
    const file = `${vueRoot}/src/fake-resize-guard-fixture.ts`
    for (const code of forbidden) await check(eslint, file, code, 1)
    for (const code of allowed) await check(eslint, file, code, 0)
  })

  it('applies to Vue single-file component scripts', async () => {
    const eslint = new ESLint({
      cwd: vueRoot,
      overrideConfigFile: `${vueRoot}/eslint.config.js`,
    })
    const file = `${vueRoot}/src/fake-resize-guard-fixture.vue`
    await check(eslint, file, '<script setup lang="ts">\nwindow.dispatchEvent(new Event("resize"))\n</script>', 1)
    await check(eslint, file, '<script setup lang="ts">\nwindow.addEventListener("resize", () => {})\n</script>', 0)
  })
})
