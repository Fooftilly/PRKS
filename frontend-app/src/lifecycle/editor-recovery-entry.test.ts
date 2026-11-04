import { afterEach, describe, expect, it, vi } from 'vitest'

type Global = { prksBrowserScope?: { current(): { key: string } | null } }

async function freshEntry() {
  vi.resetModules()
  return import('./editor-recovery-entry')
}

describe('the page runtime is bound to the signed-in browser scope (#629)', () => {
  afterEach(() => {
    delete (globalThis as Global).prksBrowserScope
  })

  it('builds nothing without a confirmed scope: recovery keeps nothing', async () => {
    const entry = await freshEntry()
    expect(entry.runtime()).toBeNull()
    expect(entry.diagnostics()).toBeNull()
    expect(entry.scopeNames()).toBeNull()
    ;(globalThis as Global).prksBrowserScope = { current: () => null }
    expect(entry.runtime()).toBeNull()
    ;(globalThis as Global).prksBrowserScope = { current: () => ({ key: 'not a scope key' }) }
    expect(entry.runtime()).toBeNull()
  })

  it("uses the scope's names once one is confirmed, and keeps them for the life of the page", async () => {
    const entry = await freshEntry()
    let key = 'IN-1.LIB-1.U-a'
    ;(globalThis as Global).prksBrowserScope = { current: () => ({ key }) }
    const rt = entry.runtime()
    expect(rt).not.toBeNull()
    expect(rt!.names.dbName).toBe('prks-editor-recovery-v2:IN-1.LIB-1.U-a')
    expect(rt!.names.channel).toBe('prks-editor-recovery-v2:IN-1.LIB-1.U-a')
    expect(entry.scopeNames()).toBe(rt!.names)
    // Another account signs in: this page's drafts stay the first account's.
    key = 'IN-1.LIB-1.U-b'
    expect(entry.runtime()).toBe(rt)
    expect(entry.diagnostics()).not.toBeNull()
    rt!.dispose()
  })
})
