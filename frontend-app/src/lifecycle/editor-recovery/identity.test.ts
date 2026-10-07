import { describe, expect, it } from 'vitest'
import { createPageIdentity, type IdentityEnv } from './identity'
import { PAGE_LOCK_PREFIX, RUNTIME_LOCK_PREFIX, RUNTIME_SESSION_KEY } from './schema'
import { createFakeBrowser } from './test-support/fake-env'

const COPIED = 'r-' + 'a'.repeat(32)

function page(browser: ReturnType<typeof createFakeBrowser>, name: string, mode: 'locks' | 'channel' | 'none', session = browser.sessionStorageWith()) {
  const env: IdentityEnv = {
    sessionStorage: session,
    locks: mode === 'locks' ? browser.locksFor(name) : null,
    createChannel: mode === 'none' ? null : browser.channelFor(name),
    claimWaitMs: 30,
  }
  return { identity: createPageIdentity(env), session, locks: env.locks as ReturnType<typeof browser.locksFor> | null }
}

describe('page instance id', () => {
  it('is fresh on every load and never stored in sessionStorage', () => {
    const browser = createFakeBrowser()
    const session = browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED })
    const a = page(browser, 'a', 'none', session).identity
    const b = page(browser, 'b', 'none', session).identity
    expect(a.pageInstanceId).toMatch(/^p-[0-9a-f]{32}$/)
    expect(a.pageInstanceId).not.toBe(b.pageInstanceId)
    expect([...session.values.values()]).not.toContain(a.pageInstanceId)
  })
})

describe('runtime claim with Web Locks', () => {
  it('keeps the stored runtime id across a reload', async () => {
    const browser = createFakeBrowser()
    const session = browser.sessionStorageWith()
    const first = page(browser, 'first', 'locks', session)
    const claimed = await first.identity.claim()
    expect(claimed.verified).toBe('lock')
    expect(session.getItem(RUNTIME_SESSION_KEY)).toBe(claimed.runtimeId)
    first.identity.dispose()
    const reloaded = page(browser, 'reloaded', 'locks', session)
    expect((await reloaded.identity.claim()).runtimeId).toBe(claimed.runtimeId)
  })

  it('gives two simultaneous pages with a copied runtime id distinct ids', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'locks', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    const b = page(browser, 'b', 'locks', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    const [ca, cb] = await Promise.all([a.identity.claim(), b.identity.claim()])
    expect(ca.runtimeId).not.toBe(cb.runtimeId)
    expect([ca.runtimeId, cb.runtimeId]).toContain(COPIED)
    expect(a.session.getItem(RUNTIME_SESSION_KEY)).toBe(ca.runtimeId)
    expect(b.session.getItem(RUNTIME_SESSION_KEY)).toBe(cb.runtimeId)
  })

  it('holds a page lock and answers liveness from held locks', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'locks')
    const b = page(browser, 'b', 'locks')
    const ca = await a.identity.claim()
    await b.identity.claim()
    expect(browser.held.has(PAGE_LOCK_PREFIX + a.identity.pageInstanceId)).toBe(true)
    expect(await b.identity.isPageAlive(a.identity.pageInstanceId)).toBe(true)
    expect(await b.identity.isRuntimeAlive(ca.runtimeId)).toBe(true)
    a.identity.dispose()
    await new Promise((resolve) => setTimeout(resolve, 5))
    expect(browser.held.has(RUNTIME_LOCK_PREFIX + ca.runtimeId)).toBe(false)
    expect(await b.identity.isPageAlive(a.identity.pageInstanceId)).toBe(false)
    expect(await b.identity.isRuntimeAlive(ca.runtimeId)).toBe(false)
  })
})

describe('runtime claim over BroadcastChannel (no Web Locks)', () => {
  it('gives two simultaneous pages with a copied runtime id distinct ids; the lower page id keeps it', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'channel', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    const b = page(browser, 'b', 'channel', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    const [ca, cb] = await Promise.all([a.identity.claim(), b.identity.claim()])
    expect(ca.verified).toBe('channel')
    expect(ca.runtimeId).not.toBe(cb.runtimeId)
    const lower = a.identity.pageInstanceId < b.identity.pageInstanceId ? ca : cb
    expect(lower.runtimeId).toBe(COPIED)
  })

  it('makes a later page with a copied id mint a new one', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'channel', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    expect((await a.identity.claim()).runtimeId).toBe(COPIED)
    const opened = page(browser, 'opened', 'channel', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    const claim = await opened.identity.claim()
    expect(claim.runtimeId).not.toBe(COPIED)
    expect(opened.session.getItem(RUNTIME_SESSION_KEY)).toBe(claim.runtimeId)
  })

  it('answers page, runtime and lineage pings', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'channel')
    const b = page(browser, 'b', 'channel')
    const ca = await a.identity.claim()
    await b.identity.claim()
    a.identity.setLineageResponder((id) => id === 'd-live')
    expect(await b.identity.isPageAlive(a.identity.pageInstanceId)).toBe(true)
    expect(await b.identity.isRuntimeAlive(ca.runtimeId)).toBe(true)
    expect(await b.identity.isLineageLiveElsewhere('d-live')).toBe(true)
    expect(await b.identity.isLineageLiveElsewhere('d-other')).toBe(false)
    a.identity.dispose()
    expect(await b.identity.isPageAlive(a.identity.pageInstanceId)).toBe(false)
  })
})

describe('runtime claim with neither', () => {
  it('uses the candidate unverified and cannot establish liveness', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'none', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    expect(await a.identity.claim()).toEqual({ runtimeId: COPIED, verified: 'unverified' })
    expect(await a.identity.isPageAlive('p-x')).toBeNull()
    expect(await a.identity.isLineageLiveElsewhere('d-x')).toBeNull()
  })
})
