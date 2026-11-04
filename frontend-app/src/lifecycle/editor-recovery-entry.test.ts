import { afterEach, describe, expect, it, vi } from 'vitest'

type Scope = { current(): { key: string } | null; isRetired?(): boolean; subscribe?(fn: (event: { type: string }) => void): () => void }
type Global = { prksBrowserScope?: Scope }

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

  it('retires the runtime, its handles and diagnostics when the scope is signed out or replaced', async () => {
    const entry = await freshEntry()
    const listeners: Array<(event: { type: string }) => void> = []
    let retired = false
    ;(globalThis as Global).prksBrowserScope = {
      current: () => (retired ? null : { key: 'IN-1.LIB-1.U-a' }),
      isRetired: () => retired,
      subscribe: (fn) => {
        listeners.push(fn)
        return () => undefined
      },
    }
    const rt = entry.runtime()!
    const diag = entry.diagnostics()!
    expect(rt).not.toBeNull()
    expect(diag).not.toBeNull()
    expect(listeners).toHaveLength(1)
    retired = true
    listeners.forEach((fn) => fn({ type: 'retired' }))
    expect(rt.isRetired()).toBe(true)
    expect(rt.store.isRetired()).toBe(true)
    expect(entry.runtime()).toBeNull()
    expect(entry.diagnostics()).toBeNull()
    expect(entry.scopeNames()).toBeNull()
    await expect(rt.store.listAll()).rejects.toMatchObject({ code: 'retired' })
  })

  it('treats a scope found retired without its event as retired', async () => {
    const entry = await freshEntry()
    let retired = false
    ;(globalThis as Global).prksBrowserScope = {
      current: () => ({ key: 'IN-1.LIB-1.U-a' }),
      isRetired: () => retired,
    }
    const rt = entry.runtime()!
    retired = true
    expect(entry.runtime()).toBeNull()
    expect(rt.isRetired()).toBe(true)
  })
})
