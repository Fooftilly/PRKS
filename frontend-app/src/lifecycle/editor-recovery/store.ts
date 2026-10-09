/**
 * IndexedDB store for editor recovery drafts (`prks-editor-recovery-v1`).
 *
 * Two object stores written together: `drafts` (small metadata, enumerated
 * with getAll + filter) and `bodies` (`{draftId, generation, body}`). Every
 * write puts both in one readwrite transaction and resolves only from
 * `oncomplete`; an abort rejects, so metadata and body never diverge and a
 * write is never reported before it committed.
 *
 * Write transactions request `{durability: 'relaxed'}` where supported and fall
 * back to a plain readwrite transaction. That covers reload, tab close and a
 * browser-process crash. It is not a promise against OS crash or power loss;
 * the semantic operation queue stays the strict durability boundary.
 *
 * Every decision that depends on stored state (owner, generation, exact body)
 * is made inside the same transaction that writes or deletes, so concurrent
 * pages are serialized by IndexedDB itself. Nothing here deletes by age.
 */

import { sameBody, type Fingerprinter } from './fingerprint'
import {
  BODIES_STORE,
  DRAFTS_STORE,
  RECORD_VERSION,
  RECOVERY_DB_NAME,
  RECOVERY_DB_VERSION,
  UNKNOWN_BASE,
  entityKeyOf,
  isSupportedRecord,
  type DraftBase,
  type DraftBodyRow,
  type DraftEntityType,
  type DraftKind,
  type DraftOwner,
  type DraftPipeline,
  type DraftRecord,
  type EmergencyEntry,
  type EmergencyPayload,
} from './schema'

export type RecoveryStoreErrorCode = 'unavailable' | 'blocked' | 'quota' | 'aborted'

export class RecoveryStoreError extends Error {
  readonly code: RecoveryStoreErrorCode
  constructor(code: RecoveryStoreErrorCode, message: string) {
    super(message)
    this.name = 'RecoveryStoreError'
    this.code = code
  }
}

export function isQuotaError(error: unknown): boolean {
  return error instanceof RecoveryStoreError && error.code === 'quota'
}

export type DurabilityMode = 'relaxed' | 'default'

export interface RecoveryStoreOptions {
  indexedDB?: IDBFactory | null
  name?: string
  /**
   * 'auto' requests relaxed durability when `IDBTransaction.prototype` exposes
   * `durability`. Tests force 'relaxed' (try the option even without the
   * prototype hint) or 'none' (never pass it).
   */
  durability?: 'auto' | 'relaxed' | 'none'
  now?: () => number
  /** Optional precheck before exact body comparison (tests inject colliding ones). */
  fingerprint?: Fingerprinter
}

export interface CreateLineage {
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  owner: DraftOwner
  base: DraftBase
}

export interface WriteGenerationInput {
  draftId: string
  /** The writing page. Only the owner page may write an existing lineage. */
  pageInstanceId: string
  generation: number
  body: string
  paneId?: string
  base?: DraftBase
  /** Replaces the stored pipeline when given (null clears it). */
  pipeline?: DraftPipeline | null
  /** Create the lineage if it does not exist yet. */
  create?: CreateLineage
}

/**
 * Metadata-only update of a lineage this page owns: the base its body was
 * typed against and its relationship to the save pipeline. Never touches the
 * body or the generation.
 */
export interface UpdateLineageInput {
  draftId: string
  pageInstanceId: string
  base?: DraftBase
  pipeline?: DraftPipeline | null
}

/** `ok` committed; every other outcome wrote nothing. */
export type WriteOutcome = 'ok' | 'not-owner' | 'missing' | 'stale' | 'unsupported'
export type AdoptOutcome =
  | { outcome: 'ok'; record: DraftRecord }
  | { outcome: 'conflict' | 'missing' | 'unsupported' }
export type DeleteOutcome = 'deleted' | 'kept' | 'missing' | 'unsupported'
export type EmergencyOutcome = 'written' | 'tail-missing' | 'created' | 'forked' | 'noop' | 'dropped' | 'suppressed' | 'deferred'

/** What a discard tombstone records: enough to name the lineage, never its text. */
export interface DiscardTombstone {
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  generation: number
  /** The discarding page, whose stale emergency key the tombstone answers. */
  pageInstanceId: string
}

/**
 * A discard chosen from a review of one exact record: `kept` unless the
 * stored record still has this owner page, generation and status, so an
 * action decided on stale information never removes newer text.
 */
export interface DiscardExpectation {
  pageInstanceId: string
  generation: number
  status: DraftRecord['status']
}

export interface RecoveryStore {
  writeGeneration(input: WriteGenerationInput): Promise<WriteOutcome>
  /** Owner-checked metadata update in one transaction; `missing` for an absent or discarded record. */
  updateLineage(input: UpdateLineageInput): Promise<WriteOutcome>
  /**
   * Compare-and-set ownership: succeeds only if the stored owner page is still
   * `expectedPageInstanceId` and, when given, the stored generation is still
   * `expectedGeneration` (the body the caller read is the newest).
   */
  adopt(draftId: string, expectedPageInstanceId: string, owner: DraftOwner, expectedGeneration?: number): Promise<AdoptOutcome>
  get(draftId: string): Promise<DraftRecord | null>
  getBody(draftId: string): Promise<DraftBodyRow | null>
  listByEntity(kind: DraftKind, entityId: string): Promise<DraftRecord[]>
  listAll(): Promise<DraftRecord[]>
  /**
   * Deletes only if the stored generation is `generation` and the stored body
   * is exactly `body`; with `expectedPageInstanceId`, also only while that
   * page still owns the lineage (not adopted since it was read).
   */
  deleteIfAcknowledged(draftId: string, generation: number, body: string, expectedPageInstanceId?: string): Promise<DeleteOutcome>
  /** Deletes only if the stored body is exactly `body` (proven equal to acknowledged state). */
  deleteIfEqual(draftId: string, body: string): Promise<DeleteOutcome>
  /**
   * Explicit user discard. With `tombstone`, the body is deleted and a
   * bodyless `discarded` record stays in its place, so an emergency entry
   * that could not be cleared can never recreate the draft. `clearTombstone`
   * removes it later. Every delete (discard or acknowledgement) of a record
   * with an `emergencySource` leaves such a tombstone for that page.
   */
  discard(draftId: string, tombstone?: DiscardTombstone, expected?: DiscardExpectation): Promise<DeleteOutcome>
  /**
   * Called once that page's emergency key is gone: drops what answered that
   * key. A tombstone answering only that page is deleted; one also answering
   * another page's key (its `emergencySource`) stays for that one; a live
   * record loses its `emergencySource` mark. Anything else is kept.
   */
  clearTombstone(draftId: string, pageInstanceId: string): Promise<DeleteOutcome>
  /** Applies one emergency entry from a page that is no longer alive (§6 merge rules). */
  applyEmergencyEntry(payload: EmergencyPayload, entry: EmergencyEntry): Promise<EmergencyOutcome>
  /** Durability mode the most recent write transaction actually used. */
  lastDurability(): DurabilityMode | null
  close(): void
}

function supportsDurabilityHint(): boolean {
  try {
    return typeof IDBTransaction !== 'undefined' && 'durability' in IDBTransaction.prototype
  } catch {
    return false
  }
}

function errorFromTransaction(tx: IDBTransaction): RecoveryStoreError {
  const name = tx.error && tx.error.name
  if (name === 'QuotaExceededError') return new RecoveryStoreError('quota', 'Recovery storage is full.')
  return new RecoveryStoreError('aborted', 'The recovery write was rolled back.')
}

function clonePipeline(pipeline: DraftPipeline | null): DraftPipeline | null {
  if (!pipeline) return null
  return {
    ...pipeline,
    blockedBase: pipeline.blockedBase ? { ...pipeline.blockedBase } : null,
    ownQueued: pipeline.ownQueued ? { ...pipeline.ownQueued, base: { ...pipeline.ownQueued.base } } : null,
  }
}

/** Lineage id for an emergency tail forked away from a lineage adopted since. Deterministic, so merges are idempotent. */
export function forkedDraftId(draftId: string, generation: number): string {
  return draftId + '.e' + generation
}

export function createRecoveryStore(options: RecoveryStoreOptions = {}): RecoveryStore {
  const factory = options.indexedDB === undefined ? globalThis.indexedDB : options.indexedDB
  const name = options.name || RECOVERY_DB_NAME
  const now = options.now || Date.now
  const durability = options.durability || 'auto'
  const compare = { fingerprint: options.fingerprint }
  let dbPromise: Promise<IDBDatabase> | null = null
  let handle: IDBDatabase | null = null
  let lastMode: DurabilityMode | null = null

  function openDb(): Promise<IDBDatabase> {
    if (dbPromise) return dbPromise
    const opening = new Promise<IDBDatabase>((resolve, reject) => {
      if (!factory) {
        reject(new RecoveryStoreError('unavailable', 'IndexedDB is not available.'))
        return
      }
      let req: IDBOpenDBRequest
      try {
        req = factory.open(name, RECOVERY_DB_VERSION)
      } catch {
        reject(new RecoveryStoreError('unavailable', 'Could not open recovery storage.'))
        return
      }
      req.onupgradeneeded = () => {
        const db = req.result
        if (!db.objectStoreNames.contains(DRAFTS_STORE)) db.createObjectStore(DRAFTS_STORE, { keyPath: 'draftId' })
        if (!db.objectStoreNames.contains(BODIES_STORE)) db.createObjectStore(BODIES_STORE, { keyPath: 'draftId' })
      }
      req.onsuccess = () => {
        const db = req.result
        handle = db
        db.onversionchange = () => {
          try {
            db.close()
          } catch {
            /* ignore */
          }
          if (handle === db) handle = null
          dbPromise = null
        }
        resolve(db)
      }
      req.onerror = () => reject(new RecoveryStoreError('unavailable', 'Could not open recovery storage.'))
      req.onblocked = () => reject(new RecoveryStoreError('blocked', 'Recovery storage is blocked by another tab.'))
    })
    dbPromise = opening
    // A failed open is not cached: the next write retries.
    opening.catch(() => {
      if (dbPromise === opening) dbPromise = null
    })
    return opening
  }

  function begin(db: IDBDatabase, mode: IDBTransactionMode): IDBTransaction {
    const stores = [DRAFTS_STORE, BODIES_STORE]
    if (mode === 'readwrite' && (durability === 'relaxed' || (durability === 'auto' && supportsDurabilityHint()))) {
      try {
        const tx = db.transaction(stores, mode, { durability: 'relaxed' })
        lastMode = 'relaxed'
        return tx
      } catch {
        /* the options argument is not supported: fall through to a plain transaction */
      }
    }
    const tx = db.transaction(stores, mode)
    if (mode === 'readwrite') lastMode = 'default'
    return tx
  }

  /**
   * Runs `fn` in one transaction over both stores and resolves from
   * `oncomplete` with the value `fn` reported. Rejects on abort or error.
   */
  function run<T>(mode: IDBTransactionMode, fn: (tx: IDBTransaction, done: (value: T) => void) => void): Promise<T> {
    return openDb().then(
      (db) =>
        new Promise<T>((resolve, reject) => {
          let tx: IDBTransaction
          try {
            tx = begin(db, mode)
          } catch {
            reject(new RecoveryStoreError('unavailable', 'Could not start a recovery transaction.'))
            return
          }
          let value: T | undefined
          let reported = false
          let settled = false
          tx.oncomplete = () => {
            if (settled) return
            settled = true
            if (!reported) reject(new RecoveryStoreError('aborted', 'The recovery transaction ended without a result.'))
            else resolve(value as T)
          }
          tx.onabort = () => {
            if (settled) return
            settled = true
            reject(errorFromTransaction(tx))
          }
          try {
            fn(tx, (v) => {
              value = v
              reported = true
            })
          } catch {
            settled = true
            try {
              tx.abort()
            } catch {
              /* ignore */
            }
            reject(new RecoveryStoreError('aborted', 'The recovery transaction failed.'))
          }
        }),
    )
  }

  /** Reads the metadata and body rows of one draft, then calls `next` inside the same transaction. */
  function readBoth(
    tx: IDBTransaction,
    draftId: string,
    next: (record: DraftRecord | undefined, body: DraftBodyRow | undefined) => void,
  ): void {
    let record: DraftRecord | undefined
    let body: DraftBodyRow | undefined
    let pending = 2
    const step = () => {
      pending -= 1
      if (pending === 0) next(record, body)
    }
    const r1 = tx.objectStore(DRAFTS_STORE).get(draftId)
    r1.onsuccess = () => {
      record = r1.result as DraftRecord | undefined
      step()
    }
    const r2 = tx.objectStore(BODIES_STORE).get(draftId)
    r2.onsuccess = () => {
      body = r2.result as DraftBodyRow | undefined
      step()
    }
  }

  function readRecord(tx: IDBTransaction, draftId: string, next: (record: DraftRecord | undefined) => void): void {
    const r = tx.objectStore(DRAFTS_STORE).get(draftId)
    r.onsuccess = () => next(r.result as DraftRecord | undefined)
  }

  function putPair(tx: IDBTransaction, record: DraftRecord, body: string): void {
    tx.objectStore(DRAFTS_STORE).put(record)
    tx.objectStore(BODIES_STORE).put({ draftId: record.draftId, generation: record.generation, body })
  }

  function deletePair(tx: IDBTransaction, draftId: string): void {
    tx.objectStore(DRAFTS_STORE).delete(draftId)
    tx.objectStore(BODIES_STORE).delete(draftId)
  }

  /** Deletes a record, or leaves a tombstone for the emergency key that may still list it. */
  function retire(tx: IDBTransaction, record: DraftRecord): void {
    if (!record.emergencySource) return deletePair(tx, record.draftId)
    tx.objectStore(BODIES_STORE).delete(record.draftId)
    tx.objectStore(DRAFTS_STORE).put(
      tombstoneRecord(
        record.draftId,
        { kind: record.kind, entityType: record.entityType, entityId: record.entityId, generation: record.generation, pageInstanceId: record.emergencySource },
        record.generation,
      ),
    )
  }

  function newRecord(draftId: string, lineage: CreateLineage, generation: number, bodyLength: number): DraftRecord {
    const at = now()
    return {
      v: RECORD_VERSION,
      draftId,
      kind: lineage.kind,
      entityType: lineage.entityType,
      entityId: lineage.entityId,
      entityKey: entityKeyOf(lineage.kind, lineage.entityId),
      owner: { ...lineage.owner },
      generation,
      bodyLength,
      base: { ...lineage.base },
      pipeline: null,
      status: 'active',
      createdAt: at,
      updatedAt: at,
    }
  }

  function writeGeneration(input: WriteGenerationInput): Promise<WriteOutcome> {
    return run<WriteOutcome>('readwrite', (tx, done) => {
      readRecord(tx, input.draftId, (record) => {
        if (!record) {
          if (!input.create) {
            done('missing')
            return
          }
          const created = newRecord(input.draftId, input.create, input.generation, input.body.length)
          if (input.pipeline !== undefined) created.pipeline = clonePipeline(input.pipeline)
          putPair(tx, created, input.body)
          done('ok')
          return
        }
        if (!isSupportedRecord(record)) return done('unsupported')
        if (record.owner.pageInstanceId !== input.pageInstanceId) return done('not-owner')
        if (record.generation >= input.generation) return done('stale')
        const next: DraftRecord = {
          ...record,
          v: RECORD_VERSION,
          owner: { ...record.owner, paneId: input.paneId ?? record.owner.paneId },
          generation: input.generation,
          bodyLength: input.body.length,
          base: input.base ? { ...input.base } : record.base,
          pipeline: input.pipeline !== undefined ? clonePipeline(input.pipeline) : record.pipeline,
          status: 'active',
          updatedAt: now(),
        }
        putPair(tx, next, input.body)
        done('ok')
      })
    })
  }

  function updateLineage(input: UpdateLineageInput): Promise<WriteOutcome> {
    return run<WriteOutcome>('readwrite', (tx, done) => {
      readRecord(tx, input.draftId, (record) => {
        if (!record || record.status === 'discarded') return done('missing')
        if (!isSupportedRecord(record)) return done('unsupported')
        if (record.owner.pageInstanceId !== input.pageInstanceId) return done('not-owner')
        const next: DraftRecord = {
          ...record,
          base: input.base ? { ...input.base } : record.base,
          pipeline: input.pipeline !== undefined ? clonePipeline(input.pipeline) : record.pipeline,
          updatedAt: now(),
        }
        tx.objectStore(DRAFTS_STORE).put(next)
        done('ok')
      })
    })
  }

  function adopt(draftId: string, expectedPageInstanceId: string, owner: DraftOwner, expectedGeneration?: number): Promise<AdoptOutcome> {
    return run<AdoptOutcome>('readwrite', (tx, done) => {
      readRecord(tx, draftId, (record) => {
        if (!record) return done({ outcome: 'missing' })
        if (!isSupportedRecord(record)) return done({ outcome: 'unsupported' })
        if (record.status === 'discarded') return done({ outcome: 'missing' })
        if (record.owner.pageInstanceId !== expectedPageInstanceId) return done({ outcome: 'conflict' })
        if (expectedGeneration !== undefined && record.generation !== expectedGeneration) return done({ outcome: 'conflict' })
        const next: DraftRecord = { ...record, owner: { ...owner }, updatedAt: now() }
        tx.objectStore(DRAFTS_STORE).put(next)
        done({ outcome: 'ok', record: next })
      })
    })
  }

  function get(draftId: string): Promise<DraftRecord | null> {
    return run<DraftRecord | null>('readonly', (tx, done) => {
      readRecord(tx, draftId, (record) => done(record || null))
    })
  }

  function getBody(draftId: string): Promise<DraftBodyRow | null> {
    return run<DraftBodyRow | null>('readonly', (tx, done) => {
      const r = tx.objectStore(BODIES_STORE).get(draftId)
      r.onsuccess = () => done((r.result as DraftBodyRow | undefined) || null)
    })
  }

  function listAll(): Promise<DraftRecord[]> {
    return run<DraftRecord[]>('readonly', (tx, done) => {
      const r = tx.objectStore(DRAFTS_STORE).getAll()
      r.onsuccess = () => done((r.result as DraftRecord[]) || [])
    })
  }

  function listByEntity(kind: DraftKind, entityId: string): Promise<DraftRecord[]> {
    const key = entityKeyOf(kind, entityId)
    return listAll().then((rows) => rows.filter((row) => row && row.entityKey === key && row.status !== 'discarded'))
  }

  function deleteIfAcknowledged(draftId: string, generation: number, body: string, expectedPageInstanceId?: string): Promise<DeleteOutcome> {
    return run<DeleteOutcome>('readwrite', (tx, done) => {
      readBoth(tx, draftId, (record, row) => {
        if (!record) return done('missing')
        if (!isSupportedRecord(record)) return done('unsupported')
        if (expectedPageInstanceId !== undefined && record.owner.pageInstanceId !== expectedPageInstanceId) return done('kept')
        if (record.generation !== generation || !row || row.generation !== generation) return done('kept')
        if (!sameBody(row.body, body, compare)) return done('kept')
        retire(tx, record)
        done('deleted')
      })
    })
  }

  function deleteIfEqual(draftId: string, body: string): Promise<DeleteOutcome> {
    return run<DeleteOutcome>('readwrite', (tx, done) => {
      readBoth(tx, draftId, (record, row) => {
        if (!record) return done('missing')
        if (!isSupportedRecord(record)) return done('unsupported')
        if (!row || row.generation !== record.generation || !sameBody(row.body, body, compare)) return done('kept')
        retire(tx, record)
        done('deleted')
      })
    })
  }

  function tombstoneRecord(draftId: string, tombstone: DiscardTombstone, storedGeneration: number): DraftRecord {
    const lineage: CreateLineage = {
      kind: tombstone.kind,
      entityType: tombstone.entityType,
      entityId: tombstone.entityId,
      owner: { runtimeId: null, pageInstanceId: tombstone.pageInstanceId, paneId: '', claimedAt: now() },
      base: UNKNOWN_BASE,
    }
    return { ...newRecord(draftId, lineage, Math.max(tombstone.generation, storedGeneration), 0), status: 'discarded' }
  }

  function discard(draftId: string, tombstone?: DiscardTombstone, expected?: DiscardExpectation): Promise<DeleteOutcome> {
    return run<DeleteOutcome>('readwrite', (tx, done) => {
      readRecord(tx, draftId, (record) => {
        if (record && !isSupportedRecord(record)) return done('unsupported')
        if (expected) {
          if (!record || record.status === 'discarded') return done('missing')
          if (
            record.owner.pageInstanceId !== expected.pageInstanceId ||
            record.generation !== expected.generation ||
            record.status !== expected.status
          ) {
            return done('kept')
          }
        }
        if (!tombstone) {
          if (!record) return done('missing')
          retire(tx, record)
          return done('deleted')
        }
        tx.objectStore(BODIES_STORE).delete(draftId)
        const stone = tombstoneRecord(draftId, tombstone, record ? record.generation : 0)
        // It answers both keys: the discarding page's and the one the merge came
        // from, whether the record still carries that mark or already became
        // that key's tombstone (an acknowledgement retires before this runs).
        const source = record ? (record.emergencySource ?? (record.status === 'discarded' ? record.owner.pageInstanceId : undefined)) : undefined
        if (source && source !== tombstone.pageInstanceId) stone.emergencySource = source
        tx.objectStore(DRAFTS_STORE).put(stone)
        done(record ? 'deleted' : 'missing')
      })
    })
  }

  function clearTombstone(draftId: string, pageInstanceId: string): Promise<DeleteOutcome> {
    return run<DeleteOutcome>('readwrite', (tx, done) => {
      readRecord(tx, draftId, (record) => {
        if (!record) return done('missing')
        if (!isSupportedRecord(record)) return done('unsupported')
        const next: DraftRecord = { ...record, owner: { ...record.owner } }
        if (record.emergencySource === pageInstanceId) {
          delete next.emergencySource
        } else if (record.status === 'discarded' && record.owner.pageInstanceId === pageInstanceId) {
          // A tombstone answering a second key stays for that one.
          if (!record.emergencySource) {
            deletePair(tx, draftId)
            return done('deleted')
          }
          next.owner.pageInstanceId = record.emergencySource
          delete next.emergencySource
        } else {
          return done('kept')
        }
        tx.objectStore(DRAFTS_STORE).put(next)
        done('kept')
      })
    })
  }

  function applyEmergencyEntry(payload: EmergencyPayload, entry: EmergencyEntry): Promise<EmergencyOutcome> {
    return run<EmergencyOutcome>('readwrite', (tx, done) => {
      readRecord(tx, entry.draftId, (record) => {
        if (record && !isSupportedRecord(record)) return done('deferred')
        // Explicitly discarded while this entry could not be cleared: never recreated.
        if (record && record.status === 'discarded') return done('suppressed')
        if (!record) {
          if (entry.committedGeneration === 0 && entry.lineage && entry.body !== null) {
            const lineage: CreateLineage = {
              kind: entry.kind,
              entityType: entry.entityType,
              entityId: entry.entityId,
              owner: { ...entry.lineage.owner, claimedAt: entry.lineage.createdAt },
              base: entry.lineage.base,
            }
            const created = newRecord(entry.draftId, lineage, entry.generation, entry.body.length)
            created.createdAt = entry.lineage.createdAt
            created.emergencySource = payload.pageInstanceId
            putPair(tx, created, entry.body)
            return done('created')
          }
          // No record and nothing to create it from: either a guard covered
          // this generation (null body), or the lineage was discarded by the
          // user after it was committed. An explicit discard wins.
          return done('dropped')
        }
        if (record.owner.pageInstanceId === payload.pageInstanceId) {
          if (record.generation >= entry.generation) return done('noop')
          if (entry.body === null) {
            tx.objectStore(DRAFTS_STORE).put({ ...record, status: 'tail-missing', updatedAt: now() })
            return done('tail-missing')
          }
          // Same as an ordinary write of this generation: the entry's base and
          // pane are the writer's at the time, newer than the record's.
          putPair(
            tx,
            {
              ...record,
              generation: entry.generation,
              bodyLength: entry.body.length,
              status: 'active',
              updatedAt: now(),
              ...(entry.lineage
                ? { base: { ...entry.lineage.base }, owner: { ...record.owner, paneId: entry.lineage.owner.paneId } }
                : {}),
            },
            entry.body,
          )
          return done('written')
        }
        // Adopted by another page since. Never overwrite the adopter: the tail
        // becomes its own lineage, with the base the dead editor had.
        if (entry.body === null) return done('dropped')
        const forkId = forkedDraftId(entry.draftId, entry.generation)
        readRecord(tx, forkId, (existing) => {
          if (existing) return done('noop')
          const lineage: CreateLineage = {
            kind: entry.kind,
            entityType: entry.entityType,
            entityId: entry.entityId,
            owner: {
              runtimeId: payload.runtimeId,
              pageInstanceId: payload.pageInstanceId,
              paneId: entry.lineage ? entry.lineage.owner.paneId : record.owner.paneId,
              claimedAt: payload.at,
            },
            base: entry.lineage ? entry.lineage.base : record.base,
          }
          const forked = newRecord(forkId, lineage, entry.generation, (entry.body as string).length)
          forked.emergencySource = payload.pageInstanceId
          putPair(tx, forked, entry.body as string)
          done('forked')
        })
      })
    })
  }

  return {
    writeGeneration,
    updateLineage,
    adopt,
    get,
    getBody,
    listByEntity,
    listAll,
    deleteIfAcknowledged,
    deleteIfEqual,
    discard,
    clearTombstone,
    applyEmergencyEntry,
    lastDurability: () => lastMode,
    close() {
      const db = handle
      handle = null
      dbPromise = null
      if (db) {
        try {
          db.close()
        } catch {
          /* ignore */
        }
      }
    },
  }
}
