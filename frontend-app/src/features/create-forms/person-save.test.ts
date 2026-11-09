import { describe, expect, it } from 'vitest'
import { createPersonSaveGate } from './person-save'
import type { PersonPayload } from './submit'

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  let reject: (error: unknown) => void = () => {}
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function modal(opening: string, hidden = false): HTMLElement {
  const el = document.createElement('div')
  el.id = 'person-modal'
  if (hidden) el.classList.add('hidden')
  el.dataset.prksOpenGeneration = opening
  return el
}

const payload: PersonPayload = {
  first_name: 'Ada',
  last_name: 'Lovelace',
  aliases: '',
  about: '',
  image_url: '',
  link_wikipedia: '',
  link_stanford_encyclopedia: '',
  link_iep: '',
  links_other: '',
  birth_date: '',
  death_date: '',
}

describe('person save gate', () => {
  it('lets a newer opening save and ignores the older close/navigation', async () => {
    const gate = createPersonSaveGate()
    const firstPending = deferred<{ entity_id: string } | null>()
    const secondPending = deferred<{ entity_id: string } | null>()
    const busy: boolean[] = []
    let creates = 0
    let finishes = 0
    let closed = 0
    const shell = modal('1')
    const deps = {
      modal: () => shell,
      isBusy: () => busy.at(-1) === true,
      setBusy: (busyState: boolean) => {
        busy.push(busyState)
      },
      readPayload: () => payload,
      createPerson: () => {
        creates += 1
        return creates === 1 ? firstPending.promise : secondPending.promise
      },
      alert: async () => {
        throw new Error('older completion must not alert')
      },
      afterCreate: async (_created: { entity_id?: string } | null, stillOpening: () => boolean) => {
        if (!stillOpening()) return
        finishes += 1
        closed += 1
      },
    }
    const first = gate.save(deps)
    await Promise.resolve()
    expect(creates).toBe(1)
    // Dismiss → reopen clears busy for the new draft (afterReset).
    busy.push(false)
    shell.dataset.prksOpenGeneration = '2'
    const second = gate.save(deps)
    await Promise.resolve()
    expect(creates).toBe(2)
    firstPending.resolve({ entity_id: 'P-OLD' })
    await first
    expect(finishes).toBe(0)
    expect(closed).toBe(0)
    expect(busy.at(-1)).toBe(true)
    secondPending.resolve({ entity_id: 'P-NEW' })
    await second
    expect(finishes).toBe(1)
    expect(busy.at(-1)).toBe(false)
  })

  it('does not closeModals when the Person shell was dismissed before create settles', async () => {
    const gate = createPersonSaveGate()
    const pending = deferred<{ entity_id: string } | null>()
    let busy = false
    let finishes = 0
    const shell = modal('1')
    const save = gate.save({
      modal: () => shell,
      isBusy: () => busy,
      setBusy: (next: boolean) => {
        busy = next
      },
      readPayload: () => payload,
      createPerson: () => pending.promise,
      alert: async () => {
        throw new Error('dismissed create must not alert')
      },
      afterCreate: async () => {
        finishes += 1
      },
    })
    await Promise.resolve()
    // Dismiss Person, then open another modal (generation unchanged).
    shell.classList.add('hidden')
    busy = false
    pending.resolve({ entity_id: 'P-1' })
    await save
    expect(finishes).toBe(0)
    expect(busy).toBe(false)
  })

  it('creates once when save is clicked twice in the same opening', async () => {
    const gate = createPersonSaveGate()
    const pending = deferred<{ entity_id: string } | null>()
    let creates = 0
    let busy = false
    const shell = modal('1')
    const deps = {
      modal: () => shell,
      isBusy: () => busy,
      setBusy: (next: boolean) => {
        busy = next
      },
      readPayload: () => payload,
      createPerson: () => {
        creates += 1
        return pending.promise
      },
      alert: async () => {},
      afterCreate: async () => {},
    }
    const first = gate.save(deps)
    const second = gate.save(deps)
    expect(creates).toBe(1)
    pending.resolve({ entity_id: 'P-1' })
    await first
    await second
    expect(creates).toBe(1)
  })
})
