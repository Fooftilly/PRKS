import { describe, expect, it } from 'vitest'
import { createPlaylistSaveGate } from './playlist-save'
import type { PlaylistPending } from './submit'

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
  el.id = 'playlist-modal'
  if (hidden) el.classList.add('hidden')
  el.dataset.prksOpenGeneration = opening
  return el
}

describe('playlist save gate', () => {
  it('attaches the captured Work and ignores a later opener after overlapping create', async () => {
    const gate = createPlaylistSaveGate()
    const firstPending = deferred<string | null>()
    const secondPending = deferred<string | null>()
    const shell = modal('1')
    const firstAttach: PlaylistPending = { workId: 'W-1', tabId: 't1', generation: 1 }
    const secondAttach: PlaylistPending = { workId: 'W-2', tabId: 't2', generation: 2 }
    let livePending: PlaylistPending | null = firstAttach
    let creates = 0
    let busy = false
    const attached: Array<{ playlistId: string; workId: string }> = []
    let closed = 0
    let cleared: PlaylistPending | null | undefined
    let navigated = ''

    const depsFor = (attach: PlaylistPending) => ({
      modal: () => shell,
      isBusy: () => busy,
      setBusy: (next: boolean) => {
        busy = next
      },
      readDraft: () => ({ title: 'Lectures', description: '' }),
      createPlaylist: () => {
        creates += 1
        return creates === 1 ? firstPending.promise : secondPending.promise
      },
      pendingAttach: () => attach,
      clearPendingIfSame: (captured: PlaylistPending | null | undefined) => {
        if (captured && livePending === captured) {
          livePending = null
          cleared = captured
        }
      },
      capturedWorkStill: (pending: PlaylistPending | null | undefined) =>
        !!pending && livePending === pending,
      addWorkToPlaylist: async (playlistId: string, workId: string) => {
        attached.push({ playlistId, workId })
      },
      refreshSelects: async () => {},
      refreshAllSelects: async () => {},
      takeIndexCreateTabId: () => '',
      returnToWorkModal: () => false,
      closeModals: () => {
        closed += 1
        shell.classList.add('hidden')
      },
      navigate: (hash: string) => {
        navigated = hash
      },
      onError: async () => {
        throw new Error('must not error')
      },
    })

    const first = gate.save(depsFor(firstAttach))
    await Promise.resolve()
    expect(creates).toBe(1)
    // Dismiss → reopen for a different Work replaces the global pending attach
    // and clears busy for the new draft.
    busy = false
    shell.classList.remove('hidden')
    shell.dataset.prksOpenGeneration = '2'
    livePending = secondAttach
    const second = gate.save(depsFor(secondAttach))
    await Promise.resolve()
    expect(creates).toBe(2)

    firstPending.resolve('PL-OLD')
    await first
    expect(attached).toEqual([])
    expect(closed).toBe(0)
    expect(livePending).toEqual(secondAttach)
    expect(busy).toBe(true)

    secondPending.resolve('PL-NEW')
    await second
    expect(attached).toEqual([{ playlistId: 'PL-NEW', workId: 'W-2' }])
    expect(cleared).toBe(secondAttach)
    expect(livePending).toBeNull()
    expect(closed).toBe(1)
    expect(navigated).toBe('')
    expect(busy).toBe(false)
  })

  it('does not clear a newer pending attach when the older create finishes', async () => {
    const gate = createPlaylistSaveGate()
    const pendingCreate = deferred<string | null>()
    const shell = modal('1')
    const firstAttach: PlaylistPending = { workId: 'W-1' }
    const secondAttach: PlaylistPending = { workId: 'W-2' }
    let livePending: PlaylistPending | null = firstAttach
    let busy = false
    await Promise.resolve()
    const first = gate.save({
      modal: () => shell,
      isBusy: () => busy,
      setBusy: (next: boolean) => {
        busy = next
      },
      readDraft: () => ({ title: 'A', description: '' }),
      createPlaylist: () => pendingCreate.promise,
      pendingAttach: () => firstAttach,
      clearPendingIfSame: (captured) => {
        if (captured && livePending === captured) livePending = null
      },
      capturedWorkStill: () => false,
      addWorkToPlaylist: async () => {
        throw new Error('must not attach')
      },
      refreshSelects: async () => {},
      refreshAllSelects: async () => {},
      takeIndexCreateTabId: () => 'stolen',
      returnToWorkModal: () => false,
      closeModals: () => {
        throw new Error('must not close newer draft')
      },
      navigate: () => {
        throw new Error('must not navigate')
      },
      onError: async () => {},
    })
    busy = false
    shell.dataset.prksOpenGeneration = '9'
    livePending = secondAttach
    pendingCreate.resolve('PL-1')
    await first
    expect(livePending).toBe(secondAttach)
  })
})
