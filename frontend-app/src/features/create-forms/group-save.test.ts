import { describe, expect, it } from 'vitest'
import { createGroupSaveGate } from './group-save'

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  let reject: (error: unknown) => void = () => {}
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function modal(opening: string): HTMLElement {
  const el = document.createElement('div')
  el.id = 'group-modal'
  el.dataset.prksOpenGeneration = opening
  return el
}

describe('group save gate', () => {
  it('lets a newer opening save and ignores the older close/navigation', async () => {
    const gate = createGroupSaveGate()
    const firstPending = deferred<{ entity_id: string } | null>()
    const secondPending = deferred<{ entity_id: string } | null>()
    const busy: boolean[] = []
    let creates = 0
    let finishes = 0
    const shell = modal('1')
    const deps = {
      modal: () => shell,
      isBusy: () => busy.at(-1) === true,
      setBusy: (busyState: boolean) => {
        busy.push(busyState)
      },
      readDraft: () => ({
        name: 'Frankfurt',
        parentId: '',
        parentQuery: '',
        description: '',
      }),
      resolveParent: async () => '',
      createGroup: () => {
        creates += 1
        return creates === 1 ? firstPending.promise : secondPending.promise
      },
      alert: async () => {
        throw new Error('older completion must not alert')
      },
      failureMessage: () => 'fail',
      afterCreate: async (_created: { entity_id?: string } | null, stillOpening: () => boolean) => {
        if (!stillOpening()) return
        finishes += 1
      },
    }
    const first = gate.save(deps)
    await Promise.resolve()
    expect(creates).toBe(1)
    busy.push(false)
    shell.dataset.prksOpenGeneration = '2'
    const second = gate.save(deps)
    await Promise.resolve()
    expect(creates).toBe(2)
    firstPending.resolve({ entity_id: 'G-OLD' })
    await first
    expect(finishes).toBe(0)
    expect(busy.at(-1)).toBe(true)
    secondPending.resolve({ entity_id: 'G-NEW' })
    await second
    expect(finishes).toBe(1)
    expect(busy.at(-1)).toBe(false)
  })
})
