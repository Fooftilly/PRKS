import { describe, expect, it } from 'vitest'
import { jsonEscapeExtra, mergeEmergencyEntries, planEmergency, readEmergencyKeys, releaseDeadReservations, writeEmergency, type EmergencyStorage } from './emergency'
import { EMERGENCY_BODY_CHARS, EMERGENCY_PAGE_CHARS, UNKNOWN_BASE, emergencyKeyOf, reservationKeyOf, reservationValue, type EmergencyPayload } from './schema'
import { createRecoveryStore, type EmergencyOutcome, type RecoveryStore } from './store'
import { createFakeIdb } from './test-support/fake-idb'

function memoryStorage(limitChars = Infinity): EmergencyStorage & { map: Map<string, string> } {
  const map = new Map<string, string>()
  return {
    map,
    get length() {
      return map.size
    },
    key: (i: number) => [...map.keys()][i] ?? null,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => {
      if (v.length > limitChars) throw new DOMException('full', 'QuotaExceededError')
      map.set(k, v)
    },
    removeItem: (k: string) => {
      map.delete(k)
    },
  }
}

const payload = (page: string, body: string | null = 'tail'): EmergencyPayload => ({
  v: 1,
  pageInstanceId: page,
  runtimeId: 'r-shared',
  at: 1,
  entries: [
    {
      draftId: 'd-' + page,
      kind: 'work-research-note',
      entityType: 'work',
      entityId: 'w1',
      generation: 2,
      committedGeneration: 1,
      body,
    },
  ],
})

describe('jsonEscapeExtra', () => {
  it('matches what JSON.stringify adds, for every escape class', () => {
    const samples = [
      'plain prose, čćšžđ — “quoted”',
      'line\nbreaks\r\n\ttabs "quotes" back\\slash',
      String.fromCharCode(...Array.from({ length: 32 }, (_, i) => i)),
      'lone \ud800 high, lone \udc00 low, pair \ud83d\ude00',
      '\n'.repeat(1000),
    ]
    for (const text of samples) expect(jsonEscapeExtra(text)).toBe(JSON.stringify(text).length - 2 - text.length)
  })
})

describe('planEmergency', () => {
  it('holds bodies smallest first within the per-body and per-page budgets', () => {
    const big = EMERGENCY_BODY_CHARS + 1
    const quarter = EMERGENCY_PAGE_CHARS / 4
    const plan = planEmergency([big, quarter, quarter, quarter, quarter, 10])
    expect(plan.has(0)).toBe(false)
    expect(plan.has(5)).toBe(true)
    // 10 + three quarters fit; the fourth quarter would exceed the page budget.
    expect([1, 2, 3, 4].filter((i) => plan.has(i))).toHaveLength(3)
  })
})

describe('emergency keys', () => {
  it('isolates duplicated tabs that share a runtime id by page instance', () => {
    const storage = memoryStorage()
    writeEmergency(storage, emergencyKeyOf('p-one'), payload('p-one', 'one'))
    writeEmergency(storage, emergencyKeyOf('p-two'), payload('p-two', 'two'))
    const read = readEmergencyKeys(storage)
    expect(read.map((r) => r.pageInstanceId).sort()).toEqual(['p-one', 'p-two'])
    expect(read.map((r) => r.payload?.entries[0]?.body).sort()).toEqual(['one', 'two'])
  })

  it('retries without bodies when the full entry does not fit', () => {
    const storage = memoryStorage(400)
    expect(writeEmergency(storage, emergencyKeyOf('p'), payload('p', 'x'.repeat(1000)))).toBe('written-without-bodies')
    expect(readEmergencyKeys(storage)[0]?.payload?.entries[0]?.body).toBeNull()
  })

  it('rejects a payload whose page id does not match its key, or a first generation without lineage', () => {
    const storage = memoryStorage()
    storage.setItem(emergencyKeyOf('p-a'), JSON.stringify(payload('p-b')))
    const noLineage = payload('p-c')
    noLineage.entries[0]!.committedGeneration = 0
    storage.setItem(emergencyKeyOf('p-c'), JSON.stringify(noLineage))
    expect(readEmergencyKeys(storage).map((r) => r.payload)).toEqual([null, null])
  })
})

describe('mergeEmergencyEntries', () => {
  function fakeStore(result: EmergencyOutcome | Error = 'written') {
    const applied: string[] = []
    const store: Pick<RecoveryStore, 'applyEmergencyEntry'> = {
      applyEmergencyEntry: async (p) => {
        applied.push(p.pageInstanceId)
        if (result instanceof Error) throw result
        return result
      },
    }
    return { store, applied }
  }

  it('merges only dead pages, never its own or a live page, and removes merged keys', async () => {
    const storage = memoryStorage()
    for (const page of ['p-me', 'p-live', 'p-dead']) writeEmergency(storage, emergencyKeyOf(page), payload(page))
    const { store, applied } = fakeStore()
    await mergeEmergencyEntries({ storage, store, pageInstanceId: 'p-me', isPageAlive: async (id) => id === 'p-live' })
    expect(applied).toEqual(['p-dead'])
    expect([...storage.map.keys()].sort()).toEqual([emergencyKeyOf('p-live'), emergencyKeyOf('p-me')])
  })

  it('on a re-scan, merges only a definite dead page that had settled its claim', async () => {
    const storage = memoryStorage()
    for (const page of ['p-unknown', 'p-dead', 'p-unsettled']) writeEmergency(storage, emergencyKeyOf(page), payload(page))
    const unsettled = payload('p-unsettled')
    unsettled.runtimeId = null
    writeEmergency(storage, emergencyKeyOf('p-unsettled'), unsettled)
    const liveness: Record<string, boolean | null> = { 'p-unknown': null, 'p-dead': false, 'p-unsettled': false }
    const { store, applied } = fakeStore()
    const env = { storage, store, pageInstanceId: 'p-me', isPageAlive: async (id: string) => liveness[id] ?? null }
    await mergeEmergencyEntries({ ...env, definiteOnly: true })
    expect(applied).toEqual(['p-dead'])
    // A later start still treats an unknown or unsettled page as a previous load.
    await mergeEmergencyEntries(env)
    expect(applied.sort()).toEqual(['p-dead', 'p-unknown', 'p-unsettled'])
    expect(storage.map.size).toBe(0)
  })

  it('stops and keeps the key when its page rewrote it after the snapshot', async () => {
    const storage = memoryStorage()
    const two = payload('p-dead')
    two.entries.push({ ...two.entries[0]!, draftId: 'd-second' })
    writeEmergency(storage, emergencyKeyOf('p-dead'), two)
    const newer = JSON.stringify(payload('p-dead', 'newer tail'))
    const applied: string[] = []
    const store: Pick<RecoveryStore, 'applyEmergencyEntry'> = {
      applyEmergencyEntry: async (_p, entry) => {
        applied.push(entry.draftId)
        storage.setItem(emergencyKeyOf('p-dead'), newer)
        return 'written'
      },
    }
    const [report] = await mergeEmergencyEntries({ storage, store, pageInstanceId: 'p-me', isPageAlive: async () => false })
    expect(applied).toHaveLength(1)
    expect(report!.removed).toBe(false)
    expect(storage.map.get(emergencyKeyOf('p-dead'))).toBe(newer)
  })

  it('keeps a key when an entry could not be applied or is unreadable', async () => {
    const storage = memoryStorage()
    writeEmergency(storage, emergencyKeyOf('p-dead'), payload('p-dead'))
    storage.setItem(emergencyKeyOf('p-garbled'), '{not json')
    const { store } = fakeStore(new Error('quota'))
    await mergeEmergencyEntries({ storage, store, pageInstanceId: 'p-me', isPageAlive: async () => false })
    expect(storage.map.has(emergencyKeyOf('p-dead'))).toBe(true)
    expect(storage.map.has(emergencyKeyOf('p-garbled'))).toBe(true)
  })

  it('treats storage that throws on enumeration or reads as empty instead of failing', async () => {
    const blocked: EmergencyStorage = {
      get length(): number {
        throw new DOMException('blocked', 'SecurityError')
      },
      key: () => null,
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    }
    const { store, applied } = fakeStore()
    await expect(mergeEmergencyEntries({ storage: blocked, store, pageInstanceId: 'p-me', isPageAlive: async () => false })).resolves.toEqual([])
    const unreadable = memoryStorage()
    writeEmergency(unreadable, emergencyKeyOf('p-dead'), payload('p-dead'))
    unreadable.getItem = () => {
      throw new DOMException('blocked', 'SecurityError')
    }
    await mergeEmergencyEntries({ storage: unreadable, store, pageInstanceId: 'p-me', isPageAlive: async () => false })
    expect(applied).toEqual([])
    expect(unreadable.map.has(emergencyKeyOf('p-dead'))).toBe(true)
  })
})

describe('a merged draft whose emergency key could not be removed', () => {
  /** A dead page's key with one uncommitted lineage: a merge creates the draft. */
  function uncommitted(page: string): EmergencyPayload {
    const p = payload(page, 'unsaved words')
    p.entries[0] = {
      ...p.entries[0]!,
      generation: 1,
      committedGeneration: 0,
      lineage: { createdAt: 1, owner: { runtimeId: 'r-shared', pageInstanceId: page, paneId: 'tab-1' }, base: UNKNOWN_BASE },
    }
    return p
  }
  function stuckStorage() {
    const storage = memoryStorage()
    let stuck = true
    const remove = storage.removeItem
    storage.removeItem = (k) => {
      if (stuck) throw new DOMException('busy', 'UnknownError')
      remove(k)
    }
    return { storage, unstick: () => void (stuck = false) }
  }
  const env = (storage: EmergencyStorage, store: RecoveryStore) => ({ storage, store, pageInstanceId: 'p-me', isPageAlive: async () => false })

  it('is never recreated by the kept key after a discard or an acknowledgement', async () => {
    for (const retire of ['discard', 'acknowledge'] as const) {
      const store = createRecoveryStore({ indexedDB: createFakeIdb().factory })
      const { storage, unstick } = stuckStorage()
      writeEmergency(storage, emergencyKeyOf('p-dead'), uncommitted('p-dead'))
      const [first] = await mergeEmergencyEntries(env(storage, store))
      expect(first!.outcomes).toEqual(['created'])
      expect(first!.removed).toBe(false)
      if (retire === 'discard') expect(await store.discard('d-p-dead')).toBe('deleted')
      else expect(await store.deleteIfAcknowledged('d-p-dead', 1, 'unsaved words')).toBe('deleted')
      expect(await store.listByEntity('work-research-note', 'w1')).toEqual([])
      unstick()
      const [second] = await mergeEmergencyEntries(env(storage, store))
      expect(second!.outcomes).toEqual(['suppressed'])
      expect(second!.removed).toBe(true)
      // The key is gone, and the tombstone with it.
      expect(await store.get('d-p-dead')).toBeNull()
    }
  })

  it('drops the mark once the key is removed, so a later discard leaves nothing behind', async () => {
    const store = createRecoveryStore({ indexedDB: createFakeIdb().factory })
    const { storage, unstick } = stuckStorage()
    writeEmergency(storage, emergencyKeyOf('p-dead'), uncommitted('p-dead'))
    await mergeEmergencyEntries(env(storage, store))
    expect((await store.get('d-p-dead'))!.emergencySource).toBe('p-dead')
    unstick()
    const [again] = await mergeEmergencyEntries(env(storage, store))
    expect(again!.removed).toBe(true)
    expect((await store.get('d-p-dead'))!.emergencySource).toBeUndefined()
    await store.discard('d-p-dead')
    expect(await store.get('d-p-dead')).toBeNull()
  })

  it('keeps a tombstone that answers two keys until both are gone', async () => {
    const store = createRecoveryStore({ indexedDB: createFakeIdb().factory })
    const { storage } = stuckStorage()
    writeEmergency(storage, emergencyKeyOf('p-dead'), uncommitted('p-dead'))
    await mergeEmergencyEntries(env(storage, store))
    // An adopter discards it while its own key still lists it, too.
    await store.discard('d-p-dead', { kind: 'work-research-note', entityType: 'work', entityId: 'w1', generation: 1, pageInstanceId: 'p-adopter' })
    expect(await store.clearTombstone('d-p-dead', 'p-adopter')).toBe('kept')
    expect((await store.get('d-p-dead'))!.status).toBe('discarded')
    expect(await store.clearTombstone('d-p-dead', 'p-dead')).toBe('deleted')
    expect(await store.get('d-p-dead')).toBeNull()
  })
})

describe('releaseDeadReservations', () => {
  it('removes only proven-gone pages\' reservations, never its own or one not proven gone', async () => {
    const storage = memoryStorage()
    for (const page of ['p-me', 'p-live', 'p-dead', 'p-unknown']) storage.setItem(reservationKeyOf(page), 'ā'.repeat(10))
    writeEmergency(storage, emergencyKeyOf('p-dead'), payload('p-dead'))
    const removed = await releaseDeadReservations({ storage, pageInstanceId: 'p-me', isPageGone: async (id) => id === 'p-dead' || id === 'p-me' })
    expect(removed).toEqual([reservationKeyOf('p-dead')])
    expect([...storage.map.keys()].sort()).toEqual(
      [emergencyKeyOf('p-dead'), reservationKeyOf('p-live'), reservationKeyOf('p-me'), reservationKeyOf('p-unknown')].sort(),
    )
    // A reservation is never read as an emergency entry.
    expect(readEmergencyKeys(storage).map((r) => r.key)).toEqual([emergencyKeyOf('p-dead')])
  })

  it('removes, without Web Locks, the reservation of an earlier page of the runtime this page verifiably claimed', async () => {
    const storage = memoryStorage()
    storage.setItem(reservationKeyOf('p-crashed'), reservationValue('r-tab', 'ā'.repeat(10)))
    storage.setItem(reservationKeyOf('p-other-tab'), reservationValue('r-other', 'ā'.repeat(10)))
    storage.setItem(reservationKeyOf('p-untagged'), 'ā'.repeat(10))
    const env = { storage, pageInstanceId: 'p-me', isPageGone: async () => false }
    // An unverified claim proves nothing.
    expect(await releaseDeadReservations({ ...env, verifiedRuntimeId: null })).toEqual([])
    expect(await releaseDeadReservations({ ...env, verifiedRuntimeId: 'r-tab' })).toEqual([reservationKeyOf('p-crashed')])
    expect([...storage.map.keys()].sort()).toEqual([reservationKeyOf('p-other-tab'), reservationKeyOf('p-untagged')].sort())
  })
})
