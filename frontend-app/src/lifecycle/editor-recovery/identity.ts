/**
 * Page and runtime identity for editor recovery.
 *
 * `pageInstanceId` is minted fresh on every load and is never read from or
 * written to sessionStorage. It owns lineage writes and keys the emergency
 * entry, so duplicated tabs can never collide on either.
 *
 * `runtimeId` names one browser tab across reloads. Its candidate comes from
 * sessionStorage, which `window.open` and Duplicate tab copy, so the page
 * claims it before use:
 * - with Web Locks, by holding `prks-editor-recovery-runtime:<id>` (ifAvailable);
 * - without them (LAN/HTTP), over BroadcastChannel: `claim` and a 250 ms
 *   window; a holder answers `taken`; two simultaneous claimants keep the
 *   lower `pageInstanceId`;
 * - with neither, the candidate is used `unverified`.
 * A loser mints a new candidate and claims again. Liveness questions about
 * other pages use held locks where available and channel pings otherwise.
 */

import {
  CLAIM_WAIT_MS,
  PAGE_LOCK_PREFIX,
  RECOVERY_CHANNEL,
  RUNTIME_LOCK_PREFIX,
  RUNTIME_SESSION_KEY,
  mintId,
  type RandomSource,
} from './schema'

export interface LockManagerLike {
  request(name: string, options: { ifAvailable?: boolean }, callback: (lock: unknown) => unknown): Promise<unknown>
  query?(): Promise<{ held?: Array<{ name?: string }> }>
}

export interface ChannelLike {
  postMessage(message: unknown): void
  close(): void
  onmessage: ((event: { data: unknown }) => void) | null
}

export interface IdentityEnv {
  sessionStorage?: Pick<Storage, 'getItem' | 'setItem'> | null
  locks?: LockManagerLike | null
  createChannel?: ((name: string) => ChannelLike) | null
  random?: RandomSource
  setTimeout?: (fn: () => void, ms: number) => unknown
  claimWaitMs?: number
}

export type ClaimVerification = 'lock' | 'channel' | 'unverified'

export interface RuntimeClaim {
  runtimeId: string
  verified: ClaimVerification
}

export interface PageIdentity {
  readonly pageInstanceId: string
  /** Claims the runtime id once; later calls return the same promise. */
  claim(): Promise<RuntimeClaim>
  /** The settled claim, or null while it is still being arbitrated. */
  current(): RuntimeClaim | null
  /** true/false when established; null when liveness cannot be established. */
  isPageAlive(pageInstanceId: string): Promise<boolean | null>
  /**
   * true only when held Web Locks prove the page is gone. A missed
   * BroadcastChannel answer is never proof: a frozen or busy page misses it.
   */
  isPageGone(pageInstanceId: string): Promise<boolean>
  isRuntimeAlive(runtimeId: string): Promise<boolean | null>
  /** Does another page report a live writer for this lineage? */
  isLineageLiveElsewhere(draftId: string): Promise<boolean | null>
  /** Answers other pages' `lineage?` queries; the writer registry installs it. */
  setLineageResponder(responder: ((draftId: string) => boolean) | null): void
  dispose(): void
}

type Message =
  | { t: 'claim'; rid: string; from: string }
  | { t: 'taken'; rid: string; to: string }
  | { t: 'runtime?'; rid: string; q: string }
  | { t: 'runtime!'; rid: string; q: string }
  | { t: 'page?'; page: string; q: string }
  | { t: 'page!'; page: string; q: string }
  | { t: 'lineage?'; draftId: string; q: string }
  | { t: 'lineage!'; draftId: string; q: string }

const MAX_CLAIM_ROUNDS = 8

function defaultChannel(): ((name: string) => ChannelLike) | null {
  if (typeof BroadcastChannel === 'undefined') return null
  return (name) => new BroadcastChannel(name) as unknown as ChannelLike
}

function defaultLocks(): LockManagerLike | null {
  const nav = typeof navigator !== 'undefined' ? (navigator as Navigator & { locks?: LockManagerLike }) : null
  return nav && nav.locks ? nav.locks : null
}

function defaultSession(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null
  } catch {
    return null
  }
}

export function createPageIdentity(env: IdentityEnv = {}): PageIdentity {
  const random = env.random || globalThis.crypto
  const session = env.sessionStorage === undefined ? defaultSession() : env.sessionStorage
  const locks = env.locks === undefined ? defaultLocks() : env.locks
  const makeChannel = env.createChannel === undefined ? defaultChannel() : env.createChannel
  const later = env.setTimeout || ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const waitMs = env.claimWaitMs ?? CLAIM_WAIT_MS
  const pageInstanceId = mintId('p', random)

  let channel: ChannelLike | null = null
  try {
    channel = makeChannel ? makeChannel(RECOVERY_CHANNEL) : null
  } catch {
    channel = null
  }

  const releases: Array<() => void> = []
  let disposed = false
  let settled: RuntimeClaim | null = null
  let claimPromise: Promise<RuntimeClaim> | null = null
  let pending: { rid: string; lost: boolean } | null = null
  let lineageResponder: ((draftId: string) => boolean) | null = null
  const answers = new Map<string, () => void>()

  function post(message: Message): void {
    if (!channel || disposed) return
    try {
      channel.postMessage(message)
    } catch {
      /* a closed channel answers nothing */
    }
  }

  function onClaim(m: { rid: string; from: string }): void {
    if (m.from === pageInstanceId) return
    if (settled && settled.runtimeId === m.rid) {
      post({ t: 'taken', rid: m.rid, to: m.from })
      return
    }
    if (!pending || pending.rid !== m.rid) return
    // Simultaneous claim: the lower pageInstanceId keeps the id.
    if (m.from < pageInstanceId) pending.lost = true
    else post({ t: 'taken', rid: m.rid, to: m.from })
  }

  function onTaken(m: { rid: string; to: string }): void {
    if (m.to === pageInstanceId && pending && pending.rid === m.rid) pending.lost = true
  }

  function onAnswer(m: { q: string }): void {
    const resolve = answers.get(m.q)
    if (resolve) resolve()
  }

  const handlers: { [K in Message['t']]: (m: Extract<Message, { t: K }>) => void } = {
    claim: onClaim,
    taken: onTaken,
    'runtime?': (m) => {
      if (settled && settled.runtimeId === m.rid) post({ t: 'runtime!', rid: m.rid, q: m.q })
    },
    'page?': (m) => {
      if (m.page === pageInstanceId) post({ t: 'page!', page: m.page, q: m.q })
    },
    'lineage?': (m) => {
      if (lineageResponder && lineageResponder(m.draftId)) post({ t: 'lineage!', draftId: m.draftId, q: m.q })
    },
    'runtime!': onAnswer,
    'page!': onAnswer,
    'lineage!': onAnswer,
  }

  if (channel) {
    channel.onmessage = (event) => {
      const m = event && (event.data as Message)
      if (!m || typeof m !== 'object' || disposed) return
      const handle = Object.prototype.hasOwnProperty.call(handlers, m.t) ? handlers[m.t] : null
      if (handle) (handle as (message: Message) => void)(m)
    }
  }

  function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      later(resolve, ms)
    })
  }

  /** Holds a lock until dispose. Resolves true if granted, false if another holder has it. */
  function hold(name: string): Promise<boolean> {
    if (!locks) return Promise.resolve(false)
    return new Promise<boolean>((resolve) => {
      let answered = false
      locks
        .request(name, { ifAvailable: true }, (lock) => {
          answered = true
          if (!lock) {
            resolve(false)
            return undefined
          }
          resolve(true)
          return new Promise<void>((release) => {
            if (disposed) release()
            else releases.push(release)
          })
        })
        .catch(() => {
          if (!answered) resolve(false)
        })
    })
  }

  function persist(runtimeId: string): void {
    try {
      if (session) session.setItem(RUNTIME_SESSION_KEY, runtimeId)
    } catch {
      /* sessionStorage may be blocked; the claim still holds for this page */
    }
  }

  function initialCandidate(): string {
    try {
      const stored = session ? session.getItem(RUNTIME_SESSION_KEY) : null
      if (stored && /^r-[0-9a-f]{32}$/.test(stored)) return stored
    } catch {
      /* fall through */
    }
    return mintId('r', random)
  }

  async function claimWithLocks(candidate: string): Promise<RuntimeClaim> {
    // Held before the runtime claim settles, so a settled page always reads as alive.
    await hold(PAGE_LOCK_PREFIX + pageInstanceId)
    for (let round = 0; round < MAX_CLAIM_ROUNDS; round++) {
      if (await hold(RUNTIME_LOCK_PREFIX + candidate)) return { runtimeId: candidate, verified: 'lock' }
      candidate = mintId('r', random)
    }
    return { runtimeId: candidate, verified: 'unverified' }
  }

  async function claimWithChannel(candidate: string): Promise<RuntimeClaim> {
    for (let round = 0; round < MAX_CLAIM_ROUNDS; round++) {
      pending = { rid: candidate, lost: false }
      post({ t: 'claim', rid: candidate, from: pageInstanceId })
      await sleep(waitMs)
      const lost = pending.lost
      pending = null
      if (!lost) return { runtimeId: candidate, verified: 'channel' }
      candidate = mintId('r', random)
    }
    return { runtimeId: candidate, verified: 'unverified' }
  }

  function claim(): Promise<RuntimeClaim> {
    if (claimPromise) return claimPromise
    const candidate = initialCandidate()
    const run = locks ? claimWithLocks(candidate) : channel ? claimWithChannel(candidate) : Promise.resolve({ runtimeId: candidate, verified: 'unverified' as const })
    claimPromise = run.then((result) => {
      settled = result
      persist(result.runtimeId)
      return result
    })
    return claimPromise
  }

  async function heldLockNames(): Promise<Set<string> | null> {
    if (!locks || !locks.query) return null
    try {
      const snapshot = await locks.query()
      return new Set((snapshot.held || []).map((lock) => String(lock.name || '')))
    } catch {
      return null
    }
  }

  function ask(message: Message & { q: string }): Promise<boolean | null> {
    if (!channel || disposed) return Promise.resolve(null)
    return new Promise((resolve) => {
      let done = false
      answers.set(message.q, () => {
        if (done) return
        done = true
        answers.delete(message.q)
        resolve(true)
      })
      post(message)
      later(() => {
        if (done) return
        done = true
        answers.delete(message.q)
        resolve(false)
      }, waitMs)
    })
  }

  function queryId(): string {
    return mintId('d', random).slice(2)
  }

  return {
    pageInstanceId,
    claim,
    current: () => settled,
    async isPageAlive(id) {
      if (id === pageInstanceId) return true
      const held = await heldLockNames()
      if (held) return held.has(PAGE_LOCK_PREFIX + id)
      return ask({ t: 'page?', page: id, q: queryId() })
    },
    async isPageGone(id) {
      if (id === pageInstanceId) return false
      const held = await heldLockNames()
      return held ? !held.has(PAGE_LOCK_PREFIX + id) : false
    },
    async isRuntimeAlive(id) {
      if (settled && settled.runtimeId === id) return true
      const held = await heldLockNames()
      if (held) return held.has(RUNTIME_LOCK_PREFIX + id)
      return ask({ t: 'runtime?', rid: id, q: queryId() })
    },
    isLineageLiveElsewhere(draftId) {
      return ask({ t: 'lineage?', draftId, q: queryId() })
    },
    setLineageResponder(responder) {
      lineageResponder = responder
    },
    dispose() {
      if (disposed) return
      disposed = true
      while (releases.length) {
        const release = releases.pop()
        if (release) release()
      }
      // Pending asks settle as unanswered through their own timeouts.
      answers.clear()
      if (channel) {
        channel.onmessage = null
        try {
          channel.close()
        } catch {
          /* ignore */
        }
      }
    },
  }
}
