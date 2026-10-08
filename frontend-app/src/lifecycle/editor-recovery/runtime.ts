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

import { mergeEmergencyEntries, type EmergencyStorage, type MergeReport } from './emergency'
import { createPageIdentity, type IdentityEnv, type PageIdentity, type RuntimeClaim } from './identity'
import { classifyLineage, type LineageClass } from './lineage'
import type { DraftRecord } from './schema'
import { createRecoveryStore, type RecoveryStore, type RecoveryStoreOptions } from './store'
import { createWriterRegistry, type WriterRegistry, type WriterRegistryOptions } from './writer'

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
  dispose(): void
}

export function createEditorRecoveryRuntime(options: EditorRecoveryRuntimeOptions = {}): EditorRecoveryRuntime {
  const store = createRecoveryStore(options.store)
  const identity = createPageIdentity(options.identity)
  const emergencyStorage =
    options.emergencyStorage !== undefined ? options.emergencyStorage : (options.writers?.emergencyStorage ?? defaultLocalStorage())
  const writers = createWriterRegistry({ ...options.writers, store, identity, emergencyStorage })
  let started: Promise<{ claim: RuntimeClaim; merged: MergeReport[] }> | null = null
  let lastScan: Promise<unknown> = Promise.resolve()

  // Serialized so two scans never apply the same key's entries concurrently.
  function merge(definiteOnly: boolean): Promise<MergeReport[]> {
    const run = lastScan.then(() => runMerge(definiteOnly))
    lastScan = run.catch(() => undefined)
    return run
  }

  function runMerge(definiteOnly: boolean): Promise<MergeReport[]> {
    if (!emergencyStorage) return Promise.resolve([])
    return mergeEmergencyEntries({
      storage: emergencyStorage,
      store,
      pageInstanceId: identity.pageInstanceId,
      isPageAlive: (id) => identity.isPageAlive(id),
      definiteOnly,
    })
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
    dispose() {
      writers.dispose()
      identity.dispose()
      store.close()
    },
  }
}

function defaultLocalStorage(): EmergencyStorage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}
