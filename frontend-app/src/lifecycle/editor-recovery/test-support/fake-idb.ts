/**
 * Minimal IndexedDB fake for editor-recovery Vitest suites.
 *
 * Unlike tests/browser/lib/fake_indexeddb.js it serializes transactions on a
 * database in creation order, which is what real IndexedDB guarantees for
 * overlapping readwrite scopes and what compare-and-set depends on. Writes are
 * staged and applied only at commit; `failCommits` aborts with a
 * QuotaExceededError after the requests succeeded. `rejectOptions` makes
 * `transaction()` throw when given an options argument (no durability support).
 */

type Row = Record<string, unknown>

interface StoreDef {
  keyPath: string
  rows: Map<string, Row>
}

export interface FakeIdbControls {
  factory: IDBFactory
  /** Every transaction() call: mode and the options argument, if any. */
  log: Array<{ mode: string; options: unknown }>
  failCommits: number
  rejectOptions: boolean
  openFails: boolean
  rows(dbName: string, store: string): Row[]
}

const tick = (fn: () => void) => setTimeout(fn, 0)

class FakeRequest {
  result: unknown = undefined
  error: unknown = null
  onsuccess: ((e: unknown) => void) | null = null
  onerror: ((e: unknown) => void) | null = null
  onupgradeneeded: ((e: unknown) => void) | null = null
  onblocked: ((e: unknown) => void) | null = null
}

class FakeDb {
  readonly stores = new Map<string, StoreDef>()
  onversionchange: (() => void) | null = null
  private queue: FakeTx[] = []
  constructor(readonly controls: FakeIdbControls) {}
  get objectStoreNames() {
    return { contains: (name: string) => this.stores.has(name) }
  }
  createObjectStore(name: string, opts: { keyPath: string }) {
    this.stores.set(name, { keyPath: opts.keyPath, rows: new Map() })
  }
  transaction(names: string | string[], mode: IDBTransactionMode = 'readonly', options?: unknown) {
    if (options !== undefined && this.controls.rejectOptions) throw new TypeError('options not supported')
    this.controls.log.push({ mode, options })
    const tx = new FakeTx(this, Array.isArray(names) ? names : [names], mode)
    this.queue.push(tx)
    if (this.queue.length === 1) tick(() => tx.start())
    return tx
  }
  finished(tx: FakeTx) {
    this.queue = this.queue.filter((t) => t !== tx)
    const next = this.queue[0]
    if (next) tick(() => next.start())
  }
  close() {}
}

class FakeTx {
  oncomplete: (() => void) | null = null
  onabort: (() => void) | null = null
  onerror: (() => void) | null = null
  error: { name: string } | null = null
  private started = false
  private done = false
  private pending = 0
  private waiting: Array<() => void> = []
  private staged = new Map<string, Map<string, Row>>()
  constructor(
    private readonly db: FakeDb,
    private readonly names: string[],
    readonly mode: IDBTransactionMode,
  ) {}

  start() {
    this.started = true
    for (const name of this.names) {
      const def = this.db.stores.get(name)
      if (def) this.staged.set(name, new Map([...def.rows].map(([k, v]) => [k, structuredClone(v)])))
    }
    const waiting = this.waiting
    this.waiting = []
    waiting.forEach((run) => run())
    this.maybeFinish()
  }

  private maybeFinish() {
    tick(() => {
      if (this.done || this.pending > 0 || this.waiting.length) return
      this.done = true
      if (this.mode === 'readwrite' && this.db.controls.failCommits > 0) {
        this.db.controls.failCommits -= 1
        this.error = { name: 'QuotaExceededError' }
        this.onabort?.()
      } else {
        if (this.mode === 'readwrite') {
          for (const [name, rows] of this.staged) (this.db.stores.get(name) as StoreDef).rows = rows
        }
        this.oncomplete?.()
      }
      this.db.finished(this)
    })
  }

  abort() {
    if (this.done) return
    this.done = true
    this.error = this.error || { name: 'AbortError' }
    tick(() => {
      this.onabort?.()
      this.db.finished(this)
    })
  }

  objectStore(name: string) {
    const def = this.db.stores.get(name)
    if (!def || !this.names.includes(name)) throw new Error('No such store ' + name)
    const request = (fn: (rows: Map<string, Row>) => unknown) => {
      const req = new FakeRequest()
      this.pending += 1
      const run = () =>
        tick(() => {
          if (this.done) return
          req.result = fn(this.staged.get(name) as Map<string, Row>)
          this.pending -= 1
          req.onsuccess?.({ target: req })
          this.maybeFinish()
        })
      if (this.started) run()
      else this.waiting.push(run)
      return req
    }
    const writable = () => {
      if (this.mode !== 'readwrite') throw new Error('ReadOnlyError')
    }
    return {
      get: (key: string) => request((rows) => (rows.has(key) ? structuredClone(rows.get(key)) : undefined)),
      getAll: () => request((rows) => [...rows.keys()].sort().map((k) => structuredClone(rows.get(k)))),
      put: (value: Row) => {
        writable()
        const copy = structuredClone(value)
        return request((rows) => {
          rows.set(String(copy[def.keyPath]), copy)
          return copy[def.keyPath]
        })
      },
      delete: (key: string) => {
        writable()
        return request((rows) => {
          rows.delete(key)
          return undefined
        })
      },
    }
  }
}

export function createFakeIdb(): FakeIdbControls {
  const databases = new Map<string, FakeDb>()
  const controls: FakeIdbControls = {
    factory: null as unknown as IDBFactory,
    log: [],
    failCommits: 0,
    rejectOptions: false,
    openFails: false,
    rows(dbName, store) {
      const db = databases.get(dbName)
      const def = db && db.stores.get(store)
      return def ? [...def.rows.values()].map((row) => structuredClone(row)) : []
    },
  }
  controls.factory = {
    open(name: string, version: number) {
      const req = new FakeRequest()
      tick(() => {
        if (controls.openFails) {
          req.error = { name: 'UnknownError' }
          req.onerror?.({ target: req })
          return
        }
        let db = databases.get(name)
        const fresh = !db
        if (!db) {
          db = new FakeDb(controls)
          databases.set(name, db)
        }
        req.result = db
        if (fresh && version >= 1) req.onupgradeneeded?.({ target: req })
        req.onsuccess?.({ target: req })
      })
      return req
    },
  } as unknown as IDBFactory
  return controls
}

/** Manual scheduler for writer timers. `advance(ms)` runs due callbacks in time order. */
export function createManualScheduler() {
  let clock = 0
  let seq = 0
  const timers = new Map<number, { at: number; fn: () => void; seq: number }>()
  return {
    now: () => clock,
    set(fn: () => void, ms: number) {
      seq += 1
      timers.set(seq, { at: clock + ms, fn, seq })
      return seq
    },
    clear(handle: unknown) {
      timers.delete(handle as number)
    },
    pending: () => timers.size,
    advance(ms: number) {
      const end = clock + ms
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at || a[1].seq - b[1].seq)[0]
        if (!due) break
        timers.delete(due[0])
        clock = due[1].at
        due[1].fn()
      }
      clock = end
    },
  }
}

/** Lets real-timer IndexedDB work (fake transactions) settle. */
export async function settle(rounds = 40): Promise<void> {
  for (let i = 0; i < rounds; i++) await new Promise((resolve) => setTimeout(resolve, 0))
}
