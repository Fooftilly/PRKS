/**
 * Browser-local editor draft recovery (#466): names, budgets and record shapes.
 *
 * Recovery drafts live in their own IndexedDB database, one per browser
 * scope (`RecoveryNames`). They are not server state, not the semantic
 * operation queue (`prks-local-v2:<scope>`) and not a cache;
 * Clear offline cache never touches them. Nothing here claims that a body was
 * saved: the queue and the server stay the only authorities for that.
 */

export const RECOVERY_DB_VERSION = 1
export const DRAFTS_STORE = 'drafts'
export const BODIES_STORE = 'bodies'

/** Record schema version written by this build. Readers accept `v <= RECORD_VERSION`. */
export const RECORD_VERSION = 1
export const EMERGENCY_VERSION = 1
/** Allowance for one entry's JSON metadata (ids, lineage, base) when sizing the emergency payload. */
export const EMERGENCY_ENTRY_OVERHEAD_CHARS = 1024
export const CLOSED_PAGES_KEPT = 8
export const CLOSED_PAGES_LOCAL_KEPT = 64

/**
 * Every name recovery storage uses, for one browser scope (#629): the
 * signed-in `(instance_id, library_id, user_id)`, as `frontend/js/browser-scope.js`
 * resolves it. Each name carries the scope key, so another account in the
 * same browser never opens, reads, lists or signals this scope's drafts,
 * emergency copies, reservations, page records or locks. There is no
 * unscoped name: without a scope recovery stores nothing.
 */
export interface RecoveryNames {
  readonly scopeKey: string
  /** IndexedDB database of drafts and bodies. */
  readonly dbName: string
  /** One localStorage key per page load, so duplicated tabs never share an entry. */
  readonly emergencyPrefix: string
  /**
   * One key per page load holding filler that reserves quota for that page's
   * emergency payload. Not under the emergency prefix, so it is never read as a draft.
   * The value starts with the page's runtime id and a newline (see reservationValue).
   */
  readonly reservationPrefix: string
  /** sessionStorage: candidate runtime id for this browser tab; copied by window.open and Duplicate tab. */
  readonly runtimeSessionKey: string
  /**
   * sessionStorage: pages of this tab that ran `pagehide`, newest last. A
   * duplicated tab copies it before the original closes, so it never lists a
   * page still open there.
   */
  readonly closedPagesSessionKey: string
  /**
   * Pages of any tab that ran a final `pagehide` (not into the back/forward
   * cache), newest last: positive evidence that the page is gone where Web
   * Locks cannot prove it (LAN/HTTP). A page that crashed or was discarded
   * never records itself, so its drafts stay `unknown`. One key per page,
   * valued with its close time: tabs closing together never overwrite each
   * other's record. The oldest beyond the bound are pruned.
   */
  readonly closedPagePrefix: string
  /**
   * One key per draft whose stored generation the server already superseded
   * and whose removal recovery storage refused: `{ pageInstanceId, generation }`.
   * Kept outside recovery storage, so every page still never restores it and
   * finishes the removal. One key per draft: no tab rewrites another's mark.
   */
  readonly supersededPrefix: string
  /** One key per Work whose deletion this browser confirmed (#533). */
  readonly deletedWorkPrefix: string
  readonly channel: string
  readonly runtimeLockPrefix: string
  readonly pageLockPrefix: string
}

const SCOPE_KEY_PATTERN = /^[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9_-]{1,128}$/

/** A scope key as browser-scope.js forms it: `<instanceId>.<libraryId>.<userId>`. */
export function isScopeKey(value: unknown): value is string {
  return typeof value === 'string' && SCOPE_KEY_PATTERN.test(value)
}

/** The names of one scope's recovery storage. Throws on anything that is not a scope key. */
export function recoveryNames(scopeKey: string): RecoveryNames {
  if (!isScopeKey(scopeKey)) throw new Error('recovery storage needs a signed-in scope')
  return Object.freeze({
    scopeKey,
    dbName: 'prks-editor-recovery-v2:' + scopeKey,
    emergencyPrefix: 'prks.editorRecovery.emergency.v2.' + scopeKey + '.',
    reservationPrefix: 'prks.editorRecovery.reserve.v2.' + scopeKey + '.',
    runtimeSessionKey: 'prks.editorRecovery.runtime.v2.' + scopeKey,
    closedPagesSessionKey: 'prks.editorRecovery.closed.v2.' + scopeKey,
    closedPagePrefix: 'prks.editorRecovery.closedPage.v2.' + scopeKey + '.',
    supersededPrefix: 'prks.editorRecovery.superseded.v2.' + scopeKey + '.',
    deletedWorkPrefix: 'prks.workRecoveryCleanup.v2.' + scopeKey + '.',
    channel: 'prks-editor-recovery-v2:' + scopeKey,
    runtimeLockPrefix: 'prks-editor-recovery-runtime:' + scopeKey + ':',
    pageLockPrefix: 'prks-editor-recovery-page:' + scopeKey + ':',
  })
}

/** Bodies above this are "large": written immediately and leave-guarded until committed. */
export const LARGE_BODY_CHARS = 256 * 1024
/** Emergency entry budget: per body, and all held bodies of one page together. */
export const EMERGENCY_BODY_CHARS = LARGE_BODY_CHARS
export const EMERGENCY_PAGE_CHARS = 1024 * 1024

export const IDLE_WRITE_MS = 300
export const MAX_WRITE_WAIT_MS = 1000
export const CLAIM_WAIT_MS = 250
export const RETRY_FIRST_MS = 2000
export const RETRY_MAX_MS = 30000

export type DraftKind = 'work-research-note' | 'work-private-note' | 'folder-private-note'
export type DraftEntityType = 'work' | 'folder'
/**
 * `discarded`: a tombstone with no body, kept only while a stale emergency key
 * that this page could not clear still lists the lineage. Never a candidate.
 */
export type DraftStatus = 'active' | 'tail-missing' | 'discarded'
export type BaseSource = 'server' | 'cache' | 'pending-create' | 'unknown'

export interface DraftOwner {
  /** Browser tab across reloads; null while the claim has not settled. */
  runtimeId: string | null
  /** This page load. Only the owner page writes a lineage. */
  pageInstanceId: string
  /** Workspace tab id at last write. A hint only: not unique and remapped by shared workspace persistence. */
  paneId: string
  claimedAt: number
}

export interface DraftBase {
  /** Acknowledged revision the body was typed against; null only with source 'unknown'. */
  revision: number | null
  length: number | null
  /** 128-bit fingerprint of the acknowledged base text (32 hex). */
  fingerprint: string | null
  source: BaseSource
}

export const UNKNOWN_BASE: DraftBase = Object.freeze({ revision: null, length: null, fingerprint: null, source: 'unknown' })

/** Relationship to the ordinary save pipeline. Informational, never authoritative. Filled from slice 2. */
export interface DraftPipeline {
  state: 'drafting' | 'saving' | 'queued' | 'blocked' | 'error' | 'conflict'
  queuedOpId: string | null
  queuedGeneration: number
  blockedBase: { revision: number | null; length: number | null; fingerprint: string | null } | null
  ownQueued: {
    opId: string
    textLength: number
    textFingerprint: string
    /** Of what the server stores for that text, when it is not the text itself (a Folder field, #534). */
    storedLength?: number
    storedFingerprint?: string
    base: { revision: number | null; length: number | null; fingerprint: string | null }
  } | null
}

export interface DraftRecord {
  v: number
  draftId: string
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  entityKey: string
  owner: DraftOwner
  /** Monotonic per lineage; the body row carries the same generation. */
  generation: number
  bodyLength: number
  base: DraftBase
  pipeline: DraftPipeline | null
  status: DraftStatus
  createdAt: number
  updatedAt: number
  /**
   * Set on a record an emergency merge created: the page whose emergency key
   * may still list it. Until that key is gone, deleting the record leaves a
   * `discarded` tombstone for that page instead, so the key never recreates it.
   */
  emergencySource?: string
}

export interface DraftBodyRow {
  draftId: string
  generation: number
  body: string
}

export interface EmergencyLineage {
  createdAt: number
  owner: { runtimeId: string | null; pageInstanceId: string; paneId: string }
  base: DraftBase
}

export interface EmergencyEntry {
  draftId: string
  kind: DraftKind
  entityType: DraftEntityType
  entityId: string
  /** The uncommitted generation. */
  generation: number
  /** Newest generation known committed to IndexedDB; 0 means the lineage has no record yet. */
  committedGeneration: number
  /** Null only when the body could not be held (budget or write failure). */
  body: string | null
  /**
   * Required when committedGeneration === 0 (it creates the record). Writers
   * always include it, so a forked tail keeps the base its text was written
   * against rather than an adopter's newer one.
   */
  lineage?: EmergencyLineage
}

export interface EmergencyPayload {
  v: number
  pageInstanceId: string
  runtimeId: string | null
  at: number
  entries: EmergencyEntry[]
}

export function entityKeyOf(kind: DraftKind, entityId: string): string {
  return kind + ':' + entityId
}

export function emergencyKeyOf(names: RecoveryNames, pageInstanceId: string): string {
  return names.emergencyPrefix + pageInstanceId
}

export function reservationKeyOf(names: RecoveryNames, pageInstanceId: string): string {
  return names.reservationPrefix + pageInstanceId
}

/**
 * A reservation value: the reserving page's runtime id, a newline, then
 * filler. A later page that claims the same runtime (a reload or crash
 * restore in that tab) knows the reserving page is gone without Web Locks.
 */
export function reservationValue(runtimeId: string, filler: string): string {
  return runtimeId + '\n' + filler
}

/** The runtime id a reservation value was tagged with, or null for an untagged value. */
export function reservationRuntimeOf(value: string): string | null {
  const end = value.indexOf('\n')
  return end > 0 ? value.slice(0, end) : null
}

export interface RandomSource {
  getRandomValues(array: Uint8Array): unknown
}

/**
 * Random id with 128 bits from `crypto.getRandomValues`, which exists in
 * insecure (LAN/HTTP) contexts. `crypto.randomUUID` does not, so it is not used.
 */
export function mintId(prefix: 'r' | 'p' | 'd', random: RandomSource = globalThis.crypto): string {
  const bytes = new Uint8Array(16)
  random.getRandomValues(bytes)
  let hex = ''
  for (let i = 0; i < bytes.length; i++) hex += bytes[i]!.toString(16).padStart(2, '0')
  return prefix + '-' + hex
}

const KINDS: readonly DraftKind[] = ['work-research-note', 'work-private-note', 'folder-private-note']

export function isDraftKind(value: unknown): value is DraftKind {
  return typeof value === 'string' && (KINDS as readonly string[]).includes(value)
}

/** Readable by this build. A newer `v` is ignored and never rewritten or deleted. */
export function isSupportedRecord(record: unknown): record is DraftRecord {
  if (!record || typeof record !== 'object') return false
  const r = record as Partial<DraftRecord>
  return (
    typeof r.v === 'number' &&
    r.v >= 1 &&
    r.v <= RECORD_VERSION &&
    typeof r.draftId === 'string' &&
    isDraftKind(r.kind) &&
    typeof r.generation === 'number' &&
    !!r.owner &&
    typeof r.owner.pageInstanceId === 'string'
  )
}
