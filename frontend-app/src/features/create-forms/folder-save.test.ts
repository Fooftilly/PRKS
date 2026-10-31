import { describe, expect, it } from 'vitest'
import { createFolderSaveGate } from './folder-save'
import type { FolderDraft } from './folder-effects'

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
  el.id = 'folder-modal'
  if (hidden) el.classList.add('hidden')
  el.dataset.prksOpenGeneration = opening
  return el
}

describe('folder save gate', () => {
  it('creates once when save is clicked twice in the same opening', async () => {
    const gate = createFolderSaveGate()
    const pending = deferred<string>()
    let calls = 0
    const shell = modal('1')
    const deps = {
      modal: () => shell,
      buttonDisabled: () => false,
      setBusy: () => {},
      readDraft: (): FolderDraft => ({ title: 'Twice', description: '', parentId: '' }),
      createFolder: () => {
        calls += 1
        return pending.promise
      },
      alert: async () => {},
      guardRefusal: () => false,
      revalidate: () => {},
      owner: () => null,
      afterCreate: async () => {},
    }
    const first = gate.save(deps)
    const second = gate.save(deps)
    expect(calls).toBe(1)
    pending.resolve('F-1')
    await first
    await second
    expect(calls).toBe(1)
  })

  it('lets a newer opening save and ignores the older completion', async () => {
    const gate = createFolderSaveGate()
    const firstPending = deferred<string>()
    const secondPending = deferred<string>()
    const busy: boolean[] = []
    let calls = 0
    const shell = modal('1')
    let after = 0
    const deps = {
      modal: () => shell,
      buttonDisabled: () => false,
      setBusy: (busyState: boolean) => {
        busy.push(busyState)
      },
      readDraft: (): FolderDraft => ({ title: 'A', description: '', parentId: '  ' }),
      createFolder: (_title: string, _description: string, parentId: string | null) => {
        calls += 1
        expect(parentId).toBeNull()
        return calls === 1 ? firstPending.promise : secondPending.promise
      },
      alert: async () => {
        throw new Error('older completion must not alert')
      },
      guardRefusal: () => false,
      revalidate: () => {},
      owner: () => ({ tabId: 'tab' }),
      afterCreate: async () => {
        after += 1
      },
    }
    const first = gate.save(deps)
    shell.dataset.prksOpenGeneration = '2'
    const second = gate.save(deps)
    expect(calls).toBe(2)
    firstPending.resolve('F-OLD')
    await first
    expect(after).toBe(0)
    expect(busy.at(-1)).toBe(true)
    secondPending.resolve('F-NEW')
    await second
    expect(after).toBe(1)
    expect(busy.at(-1)).toBe(false)
  })

  it('does not alert when the opening is gone or the offline guard refused', async () => {
    const gate = createFolderSaveGate()
    const shell = modal('1', true)
    let alerts = 0
    await gate.save({
      modal: () => shell,
      buttonDisabled: () => false,
      setBusy: () => {},
      readDraft: () => ({ title: '', description: '', parentId: '' }),
      createFolder: () => Promise.reject(new Error('nope')),
      alert: async () => {
        alerts += 1
      },
      guardRefusal: () => false,
      revalidate: () => {},
      owner: () => null,
      afterCreate: async () => {},
    })
    expect(alerts).toBe(0)

    shell.classList.remove('hidden')
    await gate.save({
      modal: () => shell,
      buttonDisabled: () => false,
      setBusy: () => {},
      readDraft: () => ({ title: '', description: '', parentId: '' }),
      createFolder: () => Promise.reject(Object.assign(new Error('offline'), { prksOfflineRefused: true })),
      alert: async () => {
        alerts += 1
      },
      guardRefusal: () => true,
      revalidate: () => {},
      owner: () => null,
      afterCreate: async () => {},
    })
    expect(alerts).toBe(0)
  })

  it('does not start when the button is already disabled', async () => {
    const gate = createFolderSaveGate()
    let calls = 0
    await gate.save({
      modal: () => modal('1'),
      buttonDisabled: () => true,
      setBusy: () => {},
      readDraft: () => ({ title: 'X', description: '', parentId: '' }),
      createFolder: () => {
        calls += 1
        return Promise.resolve('F')
      },
      alert: async () => {},
      guardRefusal: () => false,
      revalidate: () => {},
      owner: () => null,
      afterCreate: async () => {},
    })
    expect(calls).toBe(0)
  })
})
