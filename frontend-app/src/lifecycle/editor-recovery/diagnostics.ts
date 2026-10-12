/**
 * Settings → Diagnostics view of browser-local editor recovery (#466 slice 5).
 *
 * Lists every draft recovery storage holds, for every kind, without opening
 * its Work or Folder: the records in `prks-editor-recovery-v1`, records this
 * build cannot read, and emergency entries still outside IndexedDB (a live or
 * unproven page's localStorage key). It reads; it never restores, enqueues or
 * applies anything, and never asks the server whether an entity exists (a
 * 404 from the library this origin serves now proves nothing, #550).
 *
 * The only write is an explicit discard of one reviewed record, through the
 * runtime's compare-and-set `discardReviewed` after the record and its owner
 * class are read again. Nothing here deletes by age, orphan state or
 * readability: unreadable records and emergency keys are listed, never
 * removed, so a newer bundle or a later read can still recover them.
 */

import { mayHoldUnreadable, readEmergencyKeys, type EmergencyStorage, type StoredEmergency } from './emergency'
import type { LineageClass } from './lineage'
import type { EditorRecoveryRuntime, ReviewedRecord } from './runtime'
import {
  SUPERSEDED_KEY_PREFIX,
  entityKeyOf,
  isDraftKind,
  isSupportedRecord,
  type BaseSource,
  type DraftEntityType,
  type DraftKind,
  type DraftRecord,
} from './schema'
import { forkedDraftId } from './store'

/** Per-Work cleanup marks of `work-lifecycle-state.js` (#533): this device confirmed that Work deleted. */
export const DELETED_WORK_MARK_PREFIX = 'prks.workRecoveryCleanup.v1.'
/** A draft not touched for this long is flagged as old. Flagged only: nothing is removed for its age. */
export const OLD_DRAFT_MS = 30 * 24 * 60 * 60 * 1000
/** Diagnostics warns when recovery copies together exceed this (design §9). */
export const RECOVERY_WARN_BYTES = 256 * 1024 * 1024

export type StorageState = 'ok' | 'unavailable' | 'blocked' | 'failed'

/** One readable record. `expect` is what an action on it must still find. */
export interface DiagnosticsDraft {
  source: 'record'
  draftId: string
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  status: 'active' | 'tail-missing'
  lineage: LineageClass
  generation: number
  bodyLength: number
  createdAt: number
  updatedAt: number
  baseRevision: number | null
  baseSource: BaseSource
  pipelineState: string | null
  /** Not touched for `OLD_DRAFT_MS` and no editor holds it. */
  old: boolean
  /** The server already holds newer text; this copy waits for its removal. */
  superseded: boolean
  /** This device confirmed its Work deleted; the draft stays until its tab is proven closed. */
  deletedHere: boolean
  /** Created from an emergency entry of a page that did not finish writing it. */
  fromEmergency: boolean
  expect: ReviewedRecord
}

/** A record this build cannot read: a newer schema or a malformed row. Listed, never changed. */
export interface DiagnosticsUnreadableRecord {
  source: 'unreadable-record'
  draftId: string | null
  reason: 'newer-version' | 'malformed'
  kind: string | null
  entityType: string | null
  entityId: string | null
  updatedAt: number | null
  /** As the record states it; null when it does not. */
  bodyLength: number | null
  /** Opaque identity of the row as listed: its text is read only while the row still matches. Null: not readable safely. */
  snapshot: string | null
}

/** Text only an emergency key holds: its page is still open, or not proven gone. */
export interface DiagnosticsEmergency {
  source: 'emergency'
  key: string
  pageInstanceId: string
  draftId: string
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  generation: number
  /** Null when the newest text could not be captured. */
  bodyLength: number | null
  at: number
  lineage: LineageClass
}

/** An emergency key this build cannot parse. Its raw value stays copyable. */
export interface DiagnosticsUnreadableEmergency {
  source: 'unreadable-emergency'
  key: string
  pageInstanceId: string
  reason: 'read-failed' | 'newer-version' | 'malformed'
  rawLength: number | null
  /** The key's value as listed: its raw data is copied only while the key still holds exactly this. */
  raw: string | null
}

export interface RecoveryInventory {
  storage: StorageState
  /** localStorage could not be enumerated: emergency copies, if any, are unknown. */
  emergencyStorage: 'ok' | 'unavailable'
  drafts: DiagnosticsDraft[]
  unreadable: DiagnosticsUnreadableRecord[]
  emergency: DiagnosticsEmergency[]
  unreadableEmergency: DiagnosticsUnreadableEmergency[]
  /** Characters of every listed recovery copy. */
  totalChars: number
  /** Approximate storage those characters take (UTF-16). */
  approxBytes: number
  /** Some kept copy states no size (an unreadable record or key): the totals are a lower bound. */
  sizeIncomplete: boolean
  overBudget: boolean
}

export type TextTarget =
  | { source: 'record'; expect: ReviewedRecord }
  | { source: 'unreadable-record'; draftId: string; snapshot: string }
  | { source: 'emergency'; key: string; draftId: string; generation: number }
  | { source: 'unreadable-emergency'; key: string; raw: string | null }

export type TextResult =
  | { ok: true; text: string }
  | { ok: false; code: 'changed' | 'live' | 'missing' | 'failed' }

export interface DiscardTarget extends ReviewedRecord {
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  /** The user confirmed that a tab which may still be open could lose this text. */
  allowUnknownOwner: boolean
}

export type DiscardResult = 'deleted' | 'changed' | 'live' | 'unknown-owner' | 'failed'

export interface RecoveryDiagnostics {
  inventory(): Promise<RecoveryInventory>
  readText(target: TextTarget): Promise<TextResult>
  discard(target: DiscardTarget): Promise<DiscardResult>
}

export interface RecoveryDiagnosticsOptions {
  storage?: EmergencyStorage | null
  now?: () => number
}

function isLive(lineage: LineageClass): boolean {
  return lineage === 'self-live' || lineage === 'other-live'
}

function storageState(error: unknown): StorageState {
  const code = error && typeof error === 'object' ? (error as { code?: unknown }).code : undefined
  if (code === 'unavailable' || code === 'blocked') return code
  return 'failed'
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function readKey(storage: EmergencyStorage | null, key: string): string | null {
  try {
    return storage ? storage.getItem(key) : null
  } catch {
    return null
  }
}

function supersededGeneration(storage: EmergencyStorage | null, draftId: string): number {
  const raw = readKey(storage, SUPERSEDED_KEY_PREFIX + draftId)
  if (!raw) return 0
  try {
    const value = JSON.parse(raw) as { generation?: unknown } | null
    return value && typeof value.generation === 'number' ? value.generation : 0
  } catch {
    return 0
  }
}

function unreadableReason(stored: StoredEmergency): DiagnosticsUnreadableEmergency['reason'] {
  if (stored.readFailed) return 'read-failed'
  return mayHoldUnreadable(stored) ? 'newer-version' : 'malformed'
}

function finiteAtLeastZero(value: unknown): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function numberOrNull(value: unknown): boolean {
  return value === null || (typeof value === 'number' && Number.isFinite(value))
}

function isObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object'
}

const BASE_SOURCES: readonly string[] = ['server', 'cache', 'pending-create', 'unknown']
const PIPELINE_STATES: readonly string[] = ['drafting', 'saving', 'queued', 'blocked', 'error', 'conflict']

function isIntactOwner(owner: unknown): boolean {
  return (
    isObject(owner) &&
    (owner.runtimeId === null || typeof owner.runtimeId === 'string') &&
    typeof owner.pageInstanceId === 'string' &&
    typeof owner.paneId === 'string' &&
    finiteAtLeastZero(owner.claimedAt)
  )
}

function isIntactBase(base: unknown): boolean {
  return (
    isObject(base) &&
    typeof base.source === 'string' &&
    BASE_SOURCES.includes(base.source) &&
    numberOrNull(base.revision) &&
    numberOrNull(base.length) &&
    (base.fingerprint === null || typeof base.fingerprint === 'string')
  )
}

function isIntactPipeline(pipeline: unknown): boolean {
  return pipeline === null || (isObject(pipeline) && typeof pipeline.state === 'string' && PIPELINE_STATES.includes(pipeline.state))
}

/**
 * A row with the store's tombstone shape: the whole v1 shape, `discarded`, no
 * body length. Whether its body is really gone is checked when it is listed.
 */
function isTombstoneShape(row: unknown): row is DraftRecord {
  return hasIntactShape(row) && row.status === 'discarded' && row.bodyLength === 0
}

/**
 * A supported draft record with the whole v1 shape intact: identity, entity,
 * owner, base, pipeline, sizes and times. Anything less is listed copy-only
 * as damaged and is never offered for Discard.
 */
function isIntactRecord(row: unknown): row is DraftRecord {
  return hasIntactShape(row) && (row.status === 'active' || row.status === 'tail-missing')
}

/** Every v1 field present and well formed, whatever the status. */
function hasIntactShape(row: unknown): row is DraftRecord {
  if (!isSupportedRecord(row)) return false
  const r = row as unknown as Record<string, unknown>
  const entityType = row.kind === 'folder-private-note' ? 'folder' : 'work'
  return (
    (r.status === 'active' || r.status === 'tail-missing' || r.status === 'discarded') &&
    r.entityType === entityType &&
    typeof r.entityId === 'string' &&
    r.entityId !== '' &&
    r.entityKey === entityKeyOf(row.kind, r.entityId) &&
    Number.isInteger(r.generation) &&
    finiteAtLeastZero(r.generation) &&
    isIntactOwner(r.owner) &&
    isIntactBase(r.base) &&
    isIntactPipeline(r.pipeline) &&
    finiteAtLeastZero(r.bodyLength) &&
    finiteAtLeastZero(r.createdAt) &&
    finiteAtLeastZero(r.updatedAt) &&
    (r.emergencySource === undefined || typeof r.emergencySource === 'string')
  )
}

function hexOf(bytes: Uint8Array): string {
  let hex = ''
  for (const byte of bytes) hex += byte.toString(16).padStart(2, '0')
  return hex
}

/**
 * An exact, tagged serialization of a structured-clone value: primitives
 * (BigInt included), arrays, plain objects, Dates, Maps, Sets and ArrayBuffers,
 * with shared and cyclic references by position. Null for anything it cannot
 * represent exactly, so two different rows never share a snapshot.
 */
function serialized(value: unknown, seen: Map<object, number>): string | null {
  switch (typeof value) {
    case 'undefined':
      return 'u'
    case 'boolean':
      return 'b:' + String(value)
    case 'number':
      return 'n:' + (Object.is(value, -0) ? '-0' : String(value))
    case 'bigint':
      return 'i:' + value.toString()
    case 'string':
      return 's:' + JSON.stringify(value)
    case 'object':
      break
    default:
      return null
  }
  if (value === null) return 'null'
  const ref = seen.get(value)
  if (ref !== undefined) return 'r:' + ref
  seen.set(value, seen.size)
  const all = (parts: Array<string | null>): string | null => (parts.every((part) => part !== null) ? parts.join(',') : null)
  const wrap = (open: string, inner: string | null, close: string): string | null => (inner === null ? null : open + inner + close)
  const ownKeys = (record: Record<string, unknown>): string | null =>
    all(Object.keys(record).map((k) => wrap(JSON.stringify(k) + ':', serialized(record[k], seen), '')))
  // Own keys, not a walk by index: holes and extra properties are part of the identity.
  if (Array.isArray(value)) return wrap('[' + value.length + ':', ownKeys(value as unknown as Record<string, unknown>), ']')
  if (value instanceof Date) return 'd:' + value.getTime()
  if (value instanceof Map) return wrap('m{', all([...value].map(([k, v]) => wrap('', all([serialized(k, seen), serialized(v, seen)]), ''))), '}')
  if (value instanceof Set) return wrap('S{', all([...value].map((item) => serialized(item, seen))), '}')
  if (value instanceof ArrayBuffer) return 'a:' + hexOf(new Uint8Array(value))
  // A view's identity includes its buffer, offset and aliasing: not represented, so such a row offers no text.
  if (ArrayBuffer.isView(value)) return null
  const proto = Object.getPrototypeOf(value)
  if (proto !== Object.prototype && proto !== null) return null
  return wrap('{', ownKeys(value as Record<string, unknown>), '}')
}

/** Identity of a stored row as listed; null when it cannot be told apart exactly from a rewrite. */
function snapshotOf(row: unknown): string | null {
  return serialized(row, new Map())
}

function enumerable(storage: EmergencyStorage | null): boolean {
  if (!storage) return false
  try {
    return typeof storage.length === 'number'
  } catch {
    return false
  }
}

export function createRecoveryDiagnostics(rt: EditorRecoveryRuntime, options: RecoveryDiagnosticsOptions = {}): RecoveryDiagnostics {
  const storage = options.storage === undefined ? defaultLocalStorage() : options.storage
  const now = options.now || Date.now

  async function classifySafely(record: Pick<DraftRecord, 'draftId' | 'owner'>): Promise<LineageClass> {
    try {
      return await rt.classify(record)
    } catch {
      return 'unknown'
    }
  }

  function draftOf(record: DraftRecord, lineage: LineageClass, at: number): DiagnosticsDraft {
    const status = record.status === 'tail-missing' ? 'tail-missing' : 'active'
    return {
      source: 'record',
      draftId: record.draftId,
      kind: record.kind,
      entityType: record.entityType,
      entityId: record.entityId,
      status,
      lineage,
      generation: record.generation,
      bodyLength: record.bodyLength,
      createdAt: record.createdAt,
      updatedAt: record.updatedAt,
      baseRevision: record.base ? record.base.revision : null,
      baseSource: record.base ? record.base.source : 'unknown',
      pipelineState: record.pipeline ? record.pipeline.state : null,
      old: !isLive(lineage) && at - record.updatedAt > OLD_DRAFT_MS,
      superseded: supersededGeneration(storage, record.draftId) >= record.generation,
      deletedHere: record.entityType === 'work' && readKey(storage, DELETED_WORK_MARK_PREFIX + record.entityId) !== null,
      fromEmergency: typeof record.emergencySource === 'string',
      expect: { draftId: record.draftId, pageInstanceId: record.owner.pageInstanceId, generation: record.generation, status: record.status },
    }
  }

  function unreadableOf(row: unknown): DiagnosticsUnreadableRecord {
    const r = (row && typeof row === 'object' ? row : {}) as Record<string, unknown>
    const newer = typeof r.v === 'number' && r.v > 1
    return {
      source: 'unreadable-record',
      draftId: stringOrNull(r.draftId),
      reason: newer ? 'newer-version' : 'malformed',
      kind: stringOrNull(r.kind),
      entityType: stringOrNull(r.entityType),
      entityId: stringOrNull(r.entityId),
      updatedAt: typeof r.updatedAt === 'number' ? r.updatedAt : null,
      bodyLength: typeof r.bodyLength === 'number' && Number.isFinite(r.bodyLength) && r.bodyLength >= 0 ? r.bodyLength : null,
      snapshot: snapshotOf(row),
    }
  }

  /** Stored records: readable ones classified, unreadable ones listed as they are. */
  async function listRecords(at: number): Promise<{
    state: StorageState
    records: DraftRecord[]
    tombstones: DraftRecord[]
    drafts: DiagnosticsDraft[]
    unreadable: DiagnosticsUnreadableRecord[]
  }> {
    let state: StorageState = 'ok'
    let rows: unknown[] = []
    try {
      rows = await rt.store.listAll()
    } catch (error) {
      state = storageState(error)
    }
    const records: DraftRecord[] = []
    const tombstones: DraftRecord[] = []
    const unreadable: DiagnosticsUnreadableRecord[] = []
    for (const row of rows) {
      // A tombstone hides nothing while a body is still stored under it: then it is a damaged row.
      if (isTombstoneShape(row) && !(await bodyStored(row.draftId))) tombstones.push(row)
      // A draft is its record and that generation's body together: anything less is copy-only.
      else if (isIntactRecord(row) && (await bodyMatches(row))) records.push(row)
      else unreadable.push(unreadableOf(row))
    }
    // The pings wait on silence, so every record is classified together.
    const lineages = await Promise.all(records.map((record) => classifySafely(record)))
    const drafts = records.map((record, i) => draftOf(record, lineages[i]!, at))
    drafts.sort((a, b) => b.updatedAt - a.updatedAt || a.draftId.localeCompare(b.draftId))
    return { state, records, tombstones, drafts, unreadable }
  }

  /** Whether the stored body is text of the record's own generation; when that cannot be told, it is not. */
  async function bodyMatches(record: DraftRecord): Promise<boolean> {
    try {
      const body = (await rt.store.getBody(record.draftId)) as { generation?: unknown; body?: unknown } | null
      return !!body && body.generation === record.generation && typeof body.body === 'string'
    } catch {
      return false
    }
  }

  /** Whether a body row exists; when that cannot be told, assume it does (list, never hide). */
  async function bodyStored(draftId: string): Promise<boolean> {
    try {
      return (await rt.store.getBody(draftId)) !== null
    } catch {
      return true
    }
  }

  function unreadableKeyOf(stored: StoredEmergency): DiagnosticsUnreadableEmergency {
    return {
      source: 'unreadable-emergency',
      key: stored.key,
      pageInstanceId: stored.pageInstanceId,
      reason: unreadableReason(stored),
      rawLength: stored.raw === null ? null : stored.raw.length,
      raw: stored.raw,
    }
  }

  /** Emergency entries of other pages that no record holds yet, and keys this build cannot parse. */
  async function listEmergency(
    records: DraftRecord[],
    tombstones: DraftRecord[],
  ): Promise<{ emergency: DiagnosticsEmergency[]; unreadableEmergency: DiagnosticsUnreadableEmergency[] }> {
    const known = new Map<string, number>()
    // Only a draft with its body, or a verified tombstone, stands for an entry: a damaged row may have lost its text.
    for (const row of [...records, ...tombstones]) {
      known.set(row.draftId, Math.max(row.generation, known.get(row.draftId) || 0))
    }
    const unreadableEmergency: DiagnosticsUnreadableEmergency[] = []
    const pending: Array<Promise<DiagnosticsEmergency>> = []
    for (const stored of storage ? readEmergencyKeys(storage) : []) {
      const payload = stored.payload
      if (!payload) {
        if (stored.raw !== null || stored.readFailed) unreadableEmergency.push(unreadableKeyOf(stored))
        continue
      }
      const owner = { runtimeId: payload.runtimeId, pageInstanceId: stored.pageInstanceId, paneId: '', claimedAt: payload.at }
      for (const entry of payload.entries) {
        // Already in a record at this generation or later (merged, or written by its editor).
        const inRecord = Math.max(known.get(entry.draftId) || 0, known.get(forkedDraftId(entry.draftId, entry.generation)) || 0)
        if (inRecord >= entry.generation) continue
        pending.push(
          classifySafely({ draftId: entry.draftId, owner }).then((lineage) => ({
            source: 'emergency' as const,
            key: stored.key,
            pageInstanceId: stored.pageInstanceId,
            draftId: entry.draftId,
            kind: entry.kind,
            entityType: entry.entityType,
            entityId: entry.entityId,
            generation: entry.generation,
            bodyLength: entry.body === null ? null : entry.body.length,
            at: payload.at,
            lineage,
          })),
        )
      }
    }
    const emergency = await Promise.all(pending)
    emergency.sort((a, b) => b.at - a.at || a.draftId.localeCompare(b.draftId))
    return { emergency, unreadableEmergency }
  }

  async function inventory(): Promise<RecoveryInventory> {
    const at = now()
    // Dead pages' emergency entries become records first, by the runtime's own merge rules.
    try {
      await rt.scanEmergency()
    } catch {
      /* storage refused; whatever the keys hold is listed below */
    }
    const { state, records, tombstones, drafts, unreadable } = await listRecords(at)
    const { emergency, unreadableEmergency } = await listEmergency(records, tombstones)
    // Everything kept counts, readable or not: an unreadable copy takes storage too.
    const sizes = [
      ...drafts.map((d) => (Number.isFinite(d.bodyLength) ? d.bodyLength : null)),
      ...emergency.map((e) => e.bodyLength),
      ...unreadable.map((u) => u.bodyLength),
      ...unreadableEmergency.map((u) => u.rawLength),
    ]
    const totalChars = sizes.reduce<number>((sum, n) => sum + (n ?? 0), 0)
    const approxBytes = totalChars * 2
    return {
      storage: state,
      emergencyStorage: enumerable(storage) ? 'ok' : 'unavailable',
      drafts,
      unreadable,
      emergency,
      unreadableEmergency,
      totalChars,
      approxBytes,
      sizeIncomplete: sizes.some((n) => n === null),
      overBudget: approxBytes > RECOVERY_WARN_BYTES,
    }
  }

  function sameReviewed(record: DraftRecord | null, expect: ReviewedRecord): record is DraftRecord {
    return (
      isIntactRecord(record) &&
      record.owner.pageInstanceId === expect.pageInstanceId &&
      record.generation === expect.generation &&
      record.status === expect.status
    )
  }

  async function readRecordText(expect: ReviewedRecord): Promise<TextResult> {
    const record = await rt.store.get(expect.draftId)
    if (!sameReviewed(record, expect)) return { ok: false, code: 'changed' }
    // An editor holding it is the authority on its text: continue there.
    if (isLive(await classifySafely(record))) return { ok: false, code: 'live' }
    const row = await rt.store.getBody(expect.draftId)
    if (!row || typeof row.body !== 'string') return { ok: false, code: 'missing' }
    // Listed with this generation's body: any other body means it changed since.
    if (row.generation !== expect.generation) return { ok: false, code: 'changed' }
    // Read twice around the body, so a record adopted or rewritten meanwhile never shows stale text.
    if (!sameReviewed(await rt.store.get(expect.draftId), expect)) return { ok: false, code: 'changed' }
    return { ok: true, text: row.body }
  }

  async function readUnreadableText(draftId: string, snapshot: string | null): Promise<TextResult> {
    if (snapshot === null) return { ok: false, code: 'failed' }
    // Its row cannot be classified, so it is read only while it is exactly the row that was listed.
    if (snapshotOf(await rt.store.get(draftId)) !== snapshot) return { ok: false, code: 'changed' }
    const row = (await rt.store.getBody(draftId)) as { body?: unknown } | null
    if (!row || typeof row.body !== 'string') return { ok: false, code: 'missing' }
    if (snapshotOf(await rt.store.get(draftId)) !== snapshot) return { ok: false, code: 'changed' }
    return { ok: true, text: row.body }
  }

  async function readEmergencyText(key: string, draftId: string, generation: number): Promise<TextResult> {
    const stored = storage ? readEmergencyKeys(storage).find((s) => s.key === key) : undefined
    const payload = stored ? stored.payload : null
    if (!stored || !payload) return { ok: false, code: 'changed' }
    const entry = payload.entries.find((e) => e.draftId === draftId && e.generation === generation)
    if (!entry) return { ok: false, code: 'changed' }
    if (entry.body === null) return { ok: false, code: 'missing' }
    // Its page may have resumed since the list was read: an open editor is the authority on its text.
    const owner = { runtimeId: payload.runtimeId, pageInstanceId: stored.pageInstanceId, paneId: '', claimedAt: payload.at }
    if (isLive(await classifySafely({ draftId, owner }))) return { ok: false, code: 'live' }
    // The page may also have rewritten its key while it was asked: then this text is stale.
    if (readKey(storage, key) !== stored.raw) return { ok: false, code: 'changed' }
    return { ok: true, text: entry.body }
  }

  function readRawEmergency(key: string, listed: string | null): TextResult {
    if (!storage || listed === null) return { ok: false, code: 'failed' }
    try {
      const raw = storage.getItem(key)
      // Rewritten since it was listed (it may now be a live page's payload): list it again first.
      return raw === null || raw !== listed ? { ok: false, code: 'changed' } : { ok: true, text: raw }
    } catch {
      return { ok: false, code: 'failed' }
    }
  }

  async function readText(target: TextTarget): Promise<TextResult> {
    try {
      switch (target.source) {
        case 'record':
          return await readRecordText(target.expect)
        case 'unreadable-record':
          return await readUnreadableText(target.draftId, target.snapshot)
        case 'emergency':
          return await readEmergencyText(target.key, target.draftId, target.generation)
        case 'unreadable-emergency':
          return readRawEmergency(target.key, target.raw)
      }
    } catch {
      return { ok: false, code: 'failed' }
    }
  }

  async function discard(target: DiscardTarget): Promise<DiscardResult> {
    if (!isDraftKind(target.kind)) return 'changed'
    try {
      const record = await rt.store.get(target.draftId)
      if (!sameReviewed(record, target) || record.kind !== target.kind || record.entityId !== target.entityId) return 'changed'
      // Ownership is checked again: what the list showed may have changed since.
      const lineage = await classifySafely(record)
      if (isLive(lineage)) return 'live'
      if (lineage === 'unknown' && !target.allowUnknownOwner) return 'unknown-owner'
      const outcome = await rt.discardReviewed({
        draftId: target.draftId,
        pageInstanceId: target.pageInstanceId,
        generation: target.generation,
        status: target.status,
        kind: record.kind,
        entityType: record.entityType,
        entityId: record.entityId,
      })
      return outcome === 'deleted' ? 'deleted' : 'changed'
    } catch {
      return 'failed'
    }
  }

  return { inventory, readText, discard }
}

function defaultLocalStorage(): EmergencyStorage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}
