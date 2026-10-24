/**
 * Shared harness for the Work note recovery integration tests (#466, #474):
 * a minimal durable queue with the local store's coalescing and scope_busy
 * rules, an in-memory localStorage, and one recovery page on fake browser
 * primitives. Each test file keeps its own TabContext and editor wiring.
 */
import * as recoveryApi from '../../../lifecycle/editor-recovery-entry'
import type { EmergencyStorage } from '../../../lifecycle/editor-recovery/emergency'
import type { EditorRecoveryRuntime } from '../../../lifecycle/editor-recovery/runtime'
import type { createFakeBrowser } from '../../../lifecycle/editor-recovery/test-support/fake-env'
import type { createFakeIdb } from '../../../lifecycle/editor-recovery/test-support/fake-idb'

export type NoteRow = {
  op_id: string
  operation: string
  entity_type: string
  entity_id: string
  payload: { text: string }
  base_revision: number | null
  status: string
  attempt_count: number
  depends_on?: string[]
}

export type NoteServer = { text: string; revision: number }

export { memoryStorage } from '../../../lifecycle/editor-recovery/test-support/fake-env'

/** The durable queue `window.prksSync` exposes, reduced to what Work notes use. An ack updates `server`. */
export function createNoteQueue(server: NoteServer) {
  let rows: NoteRow[] = []
  let seq = 0
  let failNext = 0
  const listeners = new Set<(event: unknown) => void>()
  const busy = (msg: string) => Object.assign(new Error(msg), { prksLocalStoreCode: 'scope_busy' })
  const store = {
    async saveWorkNote(workId: string, operation: string, text: string, observed: { value: string; revision: number }) {
      if (failNext > 0) {
        failNext -= 1
        throw Object.assign(new Error('refused'), { prksLocalStoreCode: 'failed' })
      }
      const active = rows.filter((r) => r.entity_id === workId && r.operation === operation)
      if (active.length > 1) throw busy('two rows')
      const existing = active[0]
      if (existing) {
        if (existing.status !== 'pending' || existing.attempt_count > 0) throw busy('attempted')
        if (existing.payload.text === text) return existing
        rows = rows.filter((r) => r !== existing)
      }
      if (text === observed.value) return null
      const row: NoteRow = {
        op_id: 'op-' + ++seq,
        operation,
        entity_type: 'work',
        entity_id: workId,
        payload: { text },
        base_revision: observed.revision,
        status: 'pending',
        attempt_count: 0,
      }
      rows.push(row)
      return row
    },
    async listOperations() {
      return rows.map((r) => ({ ...r, payload: { ...r.payload } }))
    },
    /** The local store's DELETE_WORK request: never-sent rows naming the Work are cancelled, sent ones waited for. */
    async deleteWork(workId: string) {
      const mine = rows.filter((r) => r.entity_id === workId)
      const already = mine.find((r) => r.operation === 'DELETE_WORK')
      if (already) return already
      const neverSent = (r: NoteRow) => r.status === 'pending' && !r.attempt_count
      rows = rows.filter((r) => !(r.entity_id === workId && neverSent(r)))
      const row: NoteRow = {
        op_id: 'op-' + ++seq,
        operation: 'DELETE_WORK',
        entity_type: 'work',
        entity_id: workId,
        payload: { text: '' },
        base_revision: null,
        status: 'pending',
        attempt_count: 0,
        depends_on: mine.filter((r) => !neverSent(r)).map((r) => r.op_id),
      }
      rows.push(row)
      return row
    },
  }
  const emit = (event: unknown) => listeners.forEach((fn) => fn(event))
  return {
    store,
    subscribe(fn: (event: unknown) => void) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    changed() {
      emit({})
    },
    rows: () => rows,
    reset() {
      rows = []
      seq = 0
      failNext = 0
    },
    /** The next `n` saves are refused by storage. */
    failNext(n: number) {
      failNext = n
    },
    attempt(opId: string) {
      rows.find((r) => r.op_id === opId)!.attempt_count = 1
    },
    ack(opId: string, revision: number) {
      const row = rows.find((r) => r.op_id === opId)!
      rows = rows.filter((r) => r !== row)
      server.text = row.payload.text
      server.revision = revision
      emit({ acknowledged: { code: 'ACKNOWLEDGED', server_revision: revision }, operation: row.operation, op: row })
    },
    /** The server acknowledged a DELETE_WORK row: the Work is gone. */
    ackDelete(opId: string) {
      const row = rows.find((r) => r.op_id === opId)!
      rows = rows.filter((r) => r !== row)
      emit({ acknowledged: { code: 'ACKNOWLEDGED', work_id: row.entity_id, changed: true }, operation: row.operation, op: row })
    },
    /** A row the server refused, or whose prerequisite it refused: kept for the user to resolve. */
    conflict(opId: string) {
      rows.find((r) => r.op_id === opId)!.status = 'conflict'
      emit({})
    },
    /** Another tab or device queued a row for this Work. */
    foreign(operation: string, workId: string, text: string) {
      rows.push({
        op_id: 'op-foreign-' + ++seq,
        operation,
        entity_type: 'work',
        entity_id: workId,
        payload: { text },
        base_revision: server.revision,
        status: 'pending',
        attempt_count: 0,
      })
    },
  }
}

export type RecoveryPage = { rt: EditorRecoveryRuntime; locks: { releaseAll(): void }; window: EventTarget }

/** One page load: a fresh runtime on the shared IndexedDB, sessionStorage and localStorage. */
export function startRecoveryPage(options: {
  browser: ReturnType<typeof createFakeBrowser>
  name: string
  idb: ReturnType<typeof createFakeIdb>
  session: Pick<Storage, 'getItem' | 'setItem'>
  local: EmergencyStorage
  /** The LAN/HTTP deployment: no Web Locks. */
  withoutLocks: boolean
  /** Another live page of the origin: this test page keeps its own runtime. */
  background?: boolean
}): RecoveryPage {
  const locks = options.browser.locksFor(options.name)
  const pageWindow = new EventTarget()
  const rt = recoveryApi.createEditorRecoveryRuntime({
    store: { indexedDB: options.idb.factory },
    identity: {
      sessionStorage: options.session,
      locks: options.withoutLocks ? null : locks,
      createChannel: options.browser.channelFor(options.name),
      claimWaitMs: 20,
      window: pageWindow,
      localStorage: options.local,
    },
    writers: { window: null, document: null },
    emergencyStorage: options.local,
  })
  if (!options.background) {
    const w = window as unknown as Record<string, unknown>
    w.prksEditorRecovery = { ...recoveryApi, runtime: () => rt }
  }
  return { rt, locks, window: pageWindow }
}
