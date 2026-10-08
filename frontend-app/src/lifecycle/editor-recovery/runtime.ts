/**
 * One editor-recovery runtime per page: store, identity and writer registry.
 *
 * `start()` claims the runtime id and then merges emergency entries left by
 * pages that are no longer alive. `scanEmergency()` repeats that merge for a
 * page that is already running, so a tab that closes later is picked up
 * without a reload. Nothing starts at script load; slice 1 has
 * no consumer, so the page holds no lock, channel or listener until a
 * consumer asks for the runtime.
 */

import { mergeEmergencyEntries, readEmergencyKeys, releaseDeadReservations, type EmergencyStorage, type MergeReport } from './emergency'
import { createPageIdentity, type IdentityEnv, type PageIdentity, type RuntimeClaim } from './identity'
import { classifyLineage, type LineageClass } from './lineage'
import { RESERVATION_KEY_PREFIX, type DraftOwner, type DraftRecord } from './schema'
import { createRecoveryStore, forkedDraftId, type AdoptOutcome, type DeleteOutcome, type RecoveryStore, type RecoveryStoreOptions } from './store'
import { createWriterRegistry, type WriterEvent, type WriterRegistry, type WriterRegistryOptions } from './writer'

/** What a reviewer saw of one record; an action on it applies only while it still holds. */
export interface ReviewedRecord {
  draftId: string
  pageInstanceId: string
  generation: number
  status: DraftRecord['status']
}

export interface EditorRecoveryRuntimeOptions {
  store?: RecoveryStoreOptions
  identity?: IdentityEnv
  writers?: Omit<WriterRegistryOptions, 'store' | 'identity'>
  emergencyStorage?: EmergencyStorage | null
}

export interface EditorRecoveryRuntime {
  readonly store: RecoveryStore
  readonly identity: PageIdentity
  readonly writers: WriterRegistry
  /** Claims the runtime id, then merges dead pages' emergency entries. Idempotent. */
  start(): Promise<{ claim: RuntimeClaim; merged: MergeReport[] }>
  /**
   * Starts if needed, then merges emergency entries of pages that are dead now.
   * Live pages' keys are left alone, and so are pages whose liveness cannot be
   * established: only a later start() treats those as dead. Idempotent; scans
   * run one at a time.
   */
  scanEmergency(): Promise<MergeReport[]>
  classify(record: Pick<DraftRecord, 'draftId' | 'owner'>, askingSession?: string | null): Promise<LineageClass>
  /**
   * Takes ownership of a reviewed record for this page without a writer
   * (compare-and-set on its owner page and generation), so no other page can
   * adopt it while this page applies the user's choice. The record then
   * reads as this page's orphan until `discardReviewed` removes it.
   */
  claimReviewed(reviewed: ReviewedRecord, paneId: string): Promise<AdoptOutcome>
  /**
   * Explicit user discard of a reviewed record, only while it is unchanged.
   * When an emergency key that this page cannot clear still lists the
   * lineage, a tombstone stays in its place so a later merge never brings it back.
   */
  discardReviewed(reviewed: ReviewedRecord & Pick<DraftRecord, 'kind' | 'entityType' | 'entityId'>): Promise<DeleteOutcome>
  /** Writer protection events (`unprotected`, `protected`, `ownership-lost`) for this page. */
  onWriterEvent(listener: (event: WriterEvent) => void): () => void
  dispose(): void
}

export function createEditorRecoveryRuntime(options: EditorRecoveryRuntimeOptions = {}): EditorRecoveryRuntime {
  const store = createRecoveryStore(options.store)
  const identity = createPageIdentity(options.identity)
  const emergencyStorage =
    options.emergencyStorage !== undefined ? options.emergencyStorage : (options.writers?.emergencyStorage ?? defaultLocalStorage())
  const writerListeners = new Set<(event: WriterEvent) => void>()
  const ownEvent = options.writers?.onEvent
  const writers = createWriterRegistry({
    ...options.writers,
    store,
    identity,
    emergencyStorage,
    onEvent(event) {
      if (ownEvent) ownEvent(event)
      for (const listener of [...writerListeners]) {
        try {
          listener(event)
        } catch {
          /* one listener never stops the others */
        }
      }
    },
  })
  let started: Promise<{ claim: RuntimeClaim; merged: MergeReport[] }> | null = null
  let lastScan: Promise<unknown> = Promise.resolve()

  // Serialized so two scans never apply the same key's entries concurrently.
  function merge(definiteOnly: boolean): Promise<MergeReport[]> {
    const run = lastScan.then(() => runMerge(definiteOnly))
    lastScan = run.catch(() => undefined)
    return run
  }

  async function runMerge(definiteOnly: boolean): Promise<MergeReport[]> {
    if (!emergencyStorage) return []
    const env = {
      storage: emergencyStorage,
      store,
      pageInstanceId: identity.pageInstanceId,
      isPageAlive: (id: string) => identity.isPageAlive(id),
      definiteOnly,
    }
    const reports = await mergeEmergencyEntries(env)
    const claim = identity.current()
    const removed = await releaseDeadReservations({
      ...env,
      isPageGone: (id: string) => identity.isPageGone(id),
      // Only right after this load's claim: a channel claim is the absence of
      // an answer, and a later late answer demotes it anyway.
      verifiedRuntimeId: !definiteOnly && claim && claim.verified !== 'unverified' ? claim.runtimeId : null,
    })
    // A page taken for gone that is only busy or frozen writes it again.
    for (const key of removed) identity.announceReservationRemoved(key.slice(RESERVATION_KEY_PREFIX.length))
    return reports
  }

  function start(): Promise<{ claim: RuntimeClaim; merged: MergeReport[] }> {
    if (started) return started
    started = identity.claim().then(async (claim) => ({ claim, merged: await merge(false) }))
    return started
  }

  return {
    store,
    identity,
    writers,
    start,
    scanEmergency() {
      if (!started) return start().then((result) => result.merged)
      return started.then(() => merge(true))
    },
    classify(record, askingSession = null) {
      return classifyLineage(record, { identity, localOwner: (id) => writers.ownerOf(id) }, askingSession)
    },
    claimReviewed(reviewed, paneId) {
      if (reviewed.status !== 'active') return Promise.resolve({ outcome: 'conflict' as const })
      const claim = identity.current()
      const owner: DraftOwner = {
        runtimeId: claim ? claim.runtimeId : null,
        pageInstanceId: identity.pageInstanceId,
        paneId,
        claimedAt: Date.now(),
      }
      return store.adopt(reviewed.draftId, reviewed.pageInstanceId, owner, reviewed.generation)
    },
    discardReviewed(reviewed) {
      const expected = { pageInstanceId: reviewed.pageInstanceId, generation: reviewed.generation, status: reviewed.status }
      const listing = emergencyStorage ? listingPage(emergencyStorage, reviewed.draftId) : null
      const tombstone = listing
        ? { kind: reviewed.kind, entityType: reviewed.entityType, entityId: reviewed.entityId, generation: reviewed.generation, pageInstanceId: listing }
        : undefined
      return store.discard(reviewed.draftId, tombstone, expected)
    },
    onWriterEvent(listener) {
      writerListeners.add(listener)
      return () => {
        writerListeners.delete(listener)
      }
    },
    dispose() {
      writerListeners.clear()
      writers.dispose()
      identity.dispose()
      store.close()
    },
  }
}

/** The page whose emergency key still lists `draftId` (or its fork), if any. */
function listingPage(storage: EmergencyStorage, draftId: string): string | null {
  for (const stored of readEmergencyKeys(storage)) {
    const entries = stored.payload ? stored.payload.entries : []
    if (entries.some((entry) => entry.draftId === draftId || forkedDraftId(entry.draftId, entry.generation) === draftId)) {
      return stored.pageInstanceId
    }
  }
  return null
}

function defaultLocalStorage(): EmergencyStorage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}
