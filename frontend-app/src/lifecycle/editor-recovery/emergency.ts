/**
 * Unload emergency entry: one synchronous localStorage key per page load
 * (`prks.editorRecovery.emergency.v1.<pageInstanceId>`), written at
 * `visibilitychange:hidden` / `pagehide` only while a writer is pending.
 *
 * The plan decides which pending bodies the entry may hold: each at most
 * 256 Ki chars and all of a page together at most 1 Mi chars, smallest first.
 * A pending writer outside the plan has the leave guard armed instead.
 *
 * Startup merges entries of pages that are no longer alive into IndexedDB and
 * removes a key only after every entry in it was handled.
 */

import type { RecoveryStore, EmergencyOutcome } from './store'
import {
  EMERGENCY_BODY_CHARS,
  EMERGENCY_KEY_PREFIX,
  EMERGENCY_PAGE_CHARS,
  EMERGENCY_VERSION,
  isDraftKind,
  type EmergencyEntry,
  type EmergencyPayload,
} from './schema'

export type EmergencyStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>

/** Indexes of the bodies the emergency entry holds, chosen smallest first within the budget. */
export function planEmergency(lengths: readonly number[]): Set<number> {
  const order = lengths.map((length, index) => ({ length, index })).sort((a, b) => a.length - b.length || a.index - b.index)
  const held = new Set<number>()
  let total = 0
  for (const item of order) {
    if (item.length > EMERGENCY_BODY_CHARS) continue
    if (total + item.length > EMERGENCY_PAGE_CHARS) continue
    total += item.length
    held.add(item.index)
  }
  return held
}

export type EmergencyWriteResult = 'written' | 'written-without-bodies' | 'failed'

/** Synchronous write. On failure retries once with every body null, then gives up. */
const SHORT_ESCAPE = /["\\\b\f\n\r\t]/g
const UNICODE_ESCAPE = /[\u0000-\u0007\u000b\u000e-\u001f]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g

/**
 * Chars JSON.stringify adds to `text` beyond its length: one for each `"`,
 * `\\` and short escape (`\n`, `\t`, ...), five for each other control char or
 * lone surrogate (`\u00XX`). Exact, so capacity checks never undercount.
 */
export function jsonEscapeExtra(text: string): number {
  const short = text.match(SHORT_ESCAPE)
  const long = text.match(UNICODE_ESCAPE)
  return (short ? short.length : 0) + (long ? long.length * 5 : 0)
}

export function writeEmergency(storage: EmergencyStorage, key: string, payload: EmergencyPayload): EmergencyWriteResult {
  try {
    storage.setItem(key, JSON.stringify(payload))
    return 'written'
  } catch {
    /* quota or blocked storage */
  }
  try {
    const stripped: EmergencyPayload = { ...payload, entries: payload.entries.map((entry) => ({ ...entry, body: null })) }
    storage.setItem(key, JSON.stringify(stripped))
    return 'written-without-bodies'
  } catch {
    return 'failed'
  }
}

export interface StoredEmergency {
  key: string
  pageInstanceId: string
  /** null when unreadable or written by a newer schema; such keys are never removed here. */
  payload: EmergencyPayload | null
}

function isEntry(value: unknown): value is EmergencyEntry {
  if (!value || typeof value !== 'object') return false
  const e = value as Partial<EmergencyEntry>
  return (
    typeof e.draftId === 'string' &&
    isDraftKind(e.kind) &&
    typeof e.entityId === 'string' &&
    typeof e.generation === 'number' &&
    typeof e.committedGeneration === 'number' &&
    (e.body === null || typeof e.body === 'string') &&
    (e.committedGeneration !== 0 || !!e.lineage)
  )
}

function parsePayload(raw: string | null, pageInstanceId: string): EmergencyPayload | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<EmergencyPayload>
    if (!value || value.v !== EMERGENCY_VERSION || value.pageInstanceId !== pageInstanceId) return null
    if (!Array.isArray(value.entries) || !value.entries.every(isEntry)) return null
    return value as EmergencyPayload
  } catch {
    return null
  }
}

/** Blocked storage can throw on enumeration or reads: that leaves the emergency layer empty, never failing startup. */
export function readEmergencyKeys(storage: EmergencyStorage): StoredEmergency[] {
  const keys: string[] = []
  try {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key && key.startsWith(EMERGENCY_KEY_PREFIX)) keys.push(key)
    }
  } catch {
    return []
  }
  return keys.map((key) => {
    const pageInstanceId = key.slice(EMERGENCY_KEY_PREFIX.length)
    let raw: string | null = null
    try {
      raw = storage.getItem(key)
    } catch {
      /* unreadable now: kept for a later page */
    }
    return { key, pageInstanceId, payload: parsePayload(raw, pageInstanceId) }
  })
}

export interface MergeEnv {
  storage: EmergencyStorage
  store: Pick<RecoveryStore, 'applyEmergencyEntry'>
  /** This page; its own key is never merged. */
  pageInstanceId: string
  /** null (cannot establish) is treated as not alive: keys are only ever read by a later page. */
  isPageAlive(pageInstanceId: string): Promise<boolean | null>
}

export interface MergeReport {
  key: string
  outcomes: EmergencyOutcome[]
  removed: boolean
}

export async function mergeEmergencyEntries(env: MergeEnv): Promise<MergeReport[]> {
  const reports: MergeReport[] = []
  for (const stored of readEmergencyKeys(env.storage)) {
    if (stored.pageInstanceId === env.pageInstanceId || !stored.payload) continue
    if ((await env.isPageAlive(stored.pageInstanceId)) === true) continue
    const outcomes: EmergencyOutcome[] = []
    let complete = true
    for (const entry of stored.payload.entries) {
      try {
        const outcome = await env.store.applyEmergencyEntry(stored.payload, entry)
        outcomes.push(outcome)
        if (outcome === 'deferred') complete = false
      } catch {
        complete = false
      }
    }
    if (complete) {
      try {
        env.storage.removeItem(stored.key)
      } catch {
        complete = false
      }
    }
    reports.push({ key: stored.key, outcomes, removed: complete })
  }
  return reports
}
