import { describe, expect, it } from 'vitest'
import { createPageIdentity, type IdentityEnv } from './identity'
import { CLOSED_PAGES_LOCAL_KEPT, CLOSED_PAGE_KEY_PREFIX, PAGE_LOCK_PREFIX, RUNTIME_LOCK_PREFIX, RUNTIME_SESSION_KEY } from './schema'
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
    expect(await b.identity.isPageGone(a.identity.pageInstanceId)).toBe(false)
    a.identity.dispose()
    await new Promise((resolve) => setTimeout(resolve, 5))
    expect(await b.identity.isPageGone(a.identity.pageInstanceId)).toBe(true)
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
    // An unanswered ping is not proof: a frozen or busy page misses it too.
    expect(await b.identity.isPageGone(a.identity.pageInstanceId)).toBe(false)
  })
})

describe('a late answer to a channel claim', () => {
  it('demotes the claim to unverified and tells the holder its runtime was contested', async () => {
    const browser = createFakeBrowser()
    // The original tab's channel, held back while its main thread is busy.
    let busy = false
    const held: Array<() => void> = []
    const createChannel = (name: string) => {
      const real = browser.channelFor('a')(name)
      const proxy = {
        onmessage: null as ((event: { data: unknown }) => void) | null,
        postMessage: (message: unknown) => real.postMessage(message),
        close: () => real.close(),
      }
      real.onmessage = (event) => {
        const deliver = () => proxy.onmessage?.(event)
        if (busy) held.push(deliver)
        else deliver()
      }
      return proxy
    }
    const a = createPageIdentity({ sessionStorage: browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }), locks: null, createChannel, claimWaitMs: 30 })
    expect((await a.claim()).runtimeId).toBe(COPIED)
    let contested = 0
    a.setContestListener(() => contested++)
    busy = true
    const b = page(browser, 'b', 'channel', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    // Silence through the window: the duplicate keeps the copied id.
    expect(await b.identity.claim()).toEqual({ runtimeId: COPIED, verified: 'channel' })
    busy = false
    held.splice(0).forEach((deliver) => deliver())
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(contested).toBe(1)
    expect(b.identity.current()).toEqual({ runtimeId: COPIED, verified: 'unverified' })
  })
})

describe('reservation removal notice', () => {
  it('reaches only the page whose reservation was removed', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'channel')
    const b = page(browser, 'b', 'channel')
    const c = page(browser, 'c', 'channel')
    const heard: string[] = []
    a.identity.setContestListener(() => heard.push('a'))
    c.identity.setContestListener(() => heard.push('c'))
    b.identity.announceReservationRemoved(a.identity.pageInstanceId)
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(heard).toEqual(['a'])
  })
})

describe('closed-page record', () => {
  function events() {
    const listeners = new Map<string, Set<(e: Event) => void>>()
    return {
      addEventListener: (type: string, fn: (e: Event) => void) => void (listeners.get(type) ?? listeners.set(type, new Set()).get(type)!).add(fn),
      removeEventListener: (type: string, fn: (e: Event) => void) => void listeners.get(type)?.delete(fn),
      fire: (type: string, event: object = {}) => [...(listeners.get(type) ?? [])].forEach((fn) => fn({ type, ...event } as Event)),
    }
  }

  it('lists a page of this tab only after it ran pagehide, and not after a back/forward restore', async () => {
    const browser = createFakeBrowser()
    const session = browser.sessionStorageWith()
    const win = events()
    const before = createPageIdentity({ sessionStorage: session, window: win, locks: null, createChannel: null })
    await before.claim()
    // A tab duplicated now copies sessionStorage without the page in it.
    const copied = browser.sessionStorageWith(Object.fromEntries(session.values))
    win.fire('pagehide')
    const after = createPageIdentity({ sessionStorage: session, window: events(), locks: null, createChannel: null })
    expect(after.wasClosedInThisTab(before.pageInstanceId)).toBe(true)
    const duplicate = createPageIdentity({ sessionStorage: copied, window: events(), locks: null, createChannel: null })
    expect(duplicate.wasClosedInThisTab(before.pageInstanceId)).toBe(false)
    win.fire('pageshow', { persisted: true })
    expect(after.wasClosedInThisTab(before.pageInstanceId)).toBe(false)
  })

  it('never lists a page of this tab that went into the back/forward cache', async () => {
    const browser = createFakeBrowser()
    const session = browser.sessionStorageWith()
    const win = events()
    const cached = createPageIdentity({ sessionStorage: session, window: win, locks: null, createChannel: null })
    await cached.claim()
    win.fire('pagehide', { persisted: true })
    // The tab opens PRKS again while the old page is frozen in the cache.
    const next = createPageIdentity({ sessionStorage: session, window: events(), locks: null, createChannel: null })
    expect(next.wasClosedInThisTab(cached.pageInstanceId)).toBe(false)
    // It comes back, and later closes for real.
    win.fire('pageshow', { persisted: true })
    win.fire('pagehide', { persisted: false })
    expect(next.wasClosedInThisTab(cached.pageInstanceId)).toBe(true)
  })

  it('records a final pagehide in localStorage for every tab, but not one into the back/forward cache', async () => {
    const browser = createFakeBrowser()
    const local = browser.sessionStorageWith()
    const cachedWin = events()
    const cached = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: cachedWin, locks: null, createChannel: null })
    const closingWin = events()
    const closing = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: closingWin, locks: null, createChannel: null })
    await Promise.all([cached.claim(), closing.claim()])
    // Another tab of the origin: its own sessionStorage, the shared localStorage.
    const reader = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: events(), locks: null, createChannel: null })
    expect(reader.wasPageClosed(closing.pageInstanceId)).toBe(false)
    cachedWin.fire('pagehide', { persisted: true })
    closingWin.fire('pagehide', { persisted: false })
    expect(reader.wasPageClosed(cached.pageInstanceId)).toBe(false)
    expect(reader.wasPageClosed(closing.pageInstanceId)).toBe(true)
    expect(reader.wasClosedInThisTab(closing.pageInstanceId)).toBe(false)
    // A page never counts itself as closed.
    expect(closing.wasPageClosed(closing.pageInstanceId)).toBe(false)
  })

  it('keeps each closing page\'s record under its own key, so tabs closing together never drop one', async () => {
    const browser = createFakeBrowser()
    const local = browser.sessionStorageWith()
    const firstWin = events()
    const first = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: firstWin, locks: null, createChannel: null })
    const secondWin = events()
    const second = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: secondWin, locks: null, createChannel: null })
    await Promise.all([first.claim(), second.claim()])
    const reader = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: events(), locks: null, createChannel: null })
    firstWin.fire('pagehide', { persisted: false })
    secondWin.fire('pagehide', { persisted: false })
    expect(local.values.has(CLOSED_PAGE_KEY_PREFIX + first.pageInstanceId)).toBe(true)
    expect(local.values.has(CLOSED_PAGE_KEY_PREFIX + second.pageInstanceId)).toBe(true)
    expect(reader.wasPageClosed(first.pageInstanceId)).toBe(true)
    expect(reader.wasPageClosed(second.pageInstanceId)).toBe(true)
  })

  it('keeps a bounded record and survives a refused write', async () => {
    const browser = createFakeBrowser()
    const local = browser.sessionStorageWith()
    const ids: string[] = []
    for (let i = 0; i < CLOSED_PAGES_LOCAL_KEPT + 3; i++) {
      const win = events()
      const page = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: win, locks: null, createChannel: null })
      await page.claim()
      win.fire('pagehide', { persisted: false })
      ids.push(page.pageInstanceId)
    }
    expect([...local.values.keys()].filter((k) => k.startsWith(CLOSED_PAGE_KEY_PREFIX)).length).toBe(CLOSED_PAGES_LOCAL_KEPT)
    // The oldest records go; the newest stay.
    const reader = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: local, window: events(), locks: null, createChannel: null })
    expect(reader.wasPageClosed(ids[0]!)).toBe(false)
    expect(reader.wasPageClosed(ids[ids.length - 1]!)).toBe(true)
    const refusing = { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError') } }
    const win = events()
    const page = createPageIdentity({ sessionStorage: browser.sessionStorageWith(), localStorage: refusing, window: win, locks: null, createChannel: null })
    await page.claim()
    expect(() => win.fire('pagehide', { persisted: false })).not.toThrow()
    expect(page.wasPageClosed(ids[ids.length - 1]!)).toBe(false)
  })
})

describe('runtime claim with neither', () => {
  it('uses the candidate unverified and cannot establish liveness', async () => {
    const browser = createFakeBrowser()
    const a = page(browser, 'a', 'none', browser.sessionStorageWith({ [RUNTIME_SESSION_KEY]: COPIED }))
    expect(await a.identity.claim()).toEqual({ runtimeId: COPIED, verified: 'unverified' })
    expect(await a.identity.isPageAlive('p-x')).toBeNull()
    expect(await a.identity.isPageGone('p-x')).toBe(false)
    expect(await a.identity.isLineageLiveElsewhere('d-x')).toBeNull()
  })
})
