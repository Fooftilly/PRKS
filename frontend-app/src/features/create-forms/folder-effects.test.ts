import { afterEach, describe, expect, it } from 'vitest'
import { finishFolderCreate } from './folder-effects'

interface EffectsWindow {
  __prksPendingWorkFolderAttach?: { workId?: string; tabId?: string } | null
  closeModals?: () => void
  prksGetTabContext?: (tabId: string) => { ui?: { workFolderEditing?: boolean } } | null
  prksCapturedWorkPanelStill?: (pending: { workId?: string } | null | undefined) => boolean
  patchWorkFolder?: (workId: string, folderId: string, still: () => boolean) => Promise<unknown>
  fetchWorkDetails?: (workId: string) => Promise<{ id: string }>
  prksOfflineCacheEntityIfCurrent?: (kind: string, id: string, entity: unknown, token: unknown) => void
  prksApplyOwnedWorkEntity?: (owner: unknown, workId: string, work: unknown) => boolean
  prksTabContextIsFocused?: (owner: unknown) => boolean
  updatePanelContent?: (tab: string) => void
  prksNavigate?: (hash: string, options?: { replace?: boolean; tabId?: string }) => void
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
}

function effects(): EffectsWindow {
  return window as unknown as EffectsWindow
}

const STUBS: Array<keyof EffectsWindow> = [
  '__prksPendingWorkFolderAttach',
  'closeModals',
  'prksGetTabContext',
  'prksCapturedWorkPanelStill',
  'patchWorkFolder',
  'fetchWorkDetails',
  'prksOfflineCacheEntityIfCurrent',
  'prksApplyOwnedWorkEntity',
  'prksTabContextIsFocused',
  'updatePanelContent',
  'prksNavigate',
  'prksAlertMessage',
]

describe('folder create follow-up', () => {
  afterEach(() => {
    const w = effects()
    for (const name of STUBS) delete w[name]
  })

  it('files the new folder onto the captured Work and does not navigate', async () => {
    const w = effects()
    const opener = { ui: { workFolderEditing: true } }
    const pending = { workId: 'W-1', tabId: 'tab-1' }
    w.__prksPendingWorkFolderAttach = pending
    const calls: string[] = []
    w.prksGetTabContext = () => opener
    w.prksCapturedWorkPanelStill = () => true
    w.patchWorkFolder = async (workId, folderId) => {
      calls.push(`patch:${workId}:${folderId}`)
      return 'token'
    }
    w.fetchWorkDetails = async () => ({ id: 'W-1' })
    w.prksOfflineCacheEntityIfCurrent = () => {
      calls.push('cache')
    }
    w.prksApplyOwnedWorkEntity = () => true
    w.prksTabContextIsFocused = () => true
    w.updatePanelContent = (tab) => {
      calls.push(`panel:${tab}`)
    }
    w.closeModals = () => {
      calls.push('close')
    }
    w.prksNavigate = () => {
      calls.push('nav')
    }
    await finishFolderCreate('F-9', { tabId: 'library' }, () => true, pending)
    expect(w.__prksPendingWorkFolderAttach).toBeNull()
    expect(opener.ui.workFolderEditing).toBe(false)
    expect(calls).toEqual(['close', 'patch:W-1:F-9', 'cache', 'panel:details'])
  })

  it('replaces the Folder library route for the owner that opened the modal', async () => {
    const w = effects()
    w.__prksPendingWorkFolderAttach = null
    let hash = ''
    let replace = false
    let tabId = ''
    w.closeModals = () => {}
    w.prksNavigate = (next, options) => {
      hash = next
      replace = options?.replace === true
      tabId = options?.tabId || ''
    }
    await finishFolderCreate('F-2', { tabId: 'tab-library', route: { name: 'folders' } }, () => true)
    expect(hash).toBe('#/folders')
    expect(replace).toBe(true)
    expect(tabId).toBe('tab-library')
  })

  it('opens the new folder in the owner tab when saved from a folder or a Work (#827)', async () => {
    const w = effects()
    w.__prksPendingWorkFolderAttach = null
    const navs: Array<[string, string, boolean]> = []
    w.closeModals = () => {}
    w.prksNavigate = (next, options) => {
      navs.push([next, options?.tabId || '', options?.replace === true])
    }
    await finishFolderCreate('F-SUB', { tabId: 'tab-parent', route: { name: 'folder-detail' } }, () => true)
    await finishFolderCreate('F/2', { tabId: 'tab-work', lastResolvedRoute: { name: 'work' } }, () => true)
    await finishFolderCreate('F-3', null, () => true)
    expect(navs).toEqual([
      ['#/folders/F-SUB', 'tab-parent', false],
      ['#/folders/F%2F2', 'tab-work', false],
      ['#/folders/F-3', '', false],
    ])
  })

  it('drops the navigation when the tab that saved has closed', async () => {
    const w = effects()
    let navigated = false
    w.closeModals = () => {}
    w.prksGetTabContext = (tabId) => (tabId === 'tab-open' ? { ui: {} } : null)
    w.prksNavigate = () => {
      navigated = true
    }
    await finishFolderCreate('F-6', { tabId: 'tab-closed', route: { name: 'folder-detail' } }, () => true)
    expect(navigated).toBe(false)
    await finishFolderCreate('F-7', { tabId: 'tab-open', route: { name: 'folder-detail' } }, () => true)
    expect(navigated).toBe(true)
  })

  it('attaches only the Work captured at Save and leaves a newer opener its own attach', async () => {
    const w = effects()
    const captured = { workId: 'W-OLD', tabId: 'tab-1' }
    const newer = { workId: 'W-NEW', tabId: 'tab-2' }
    w.__prksPendingWorkFolderAttach = newer
    const patched: string[] = []
    w.prksCapturedWorkPanelStill = () => true
    w.patchWorkFolder = async (workId) => {
      patched.push(workId)
      return null
    }
    w.closeModals = () => {}
    w.fetchWorkDetails = async () => {
      throw new Error('offline')
    }
    await finishFolderCreate('F-4', null, () => true, captured)
    expect(patched).toEqual(['W-OLD'])
    expect(w.__prksPendingWorkFolderAttach).toBe(newer)
  })

  it('does not attach anything when no Work picker opened this Save', async () => {
    const w = effects()
    // A pending attach left by an earlier, cancelled picker is not this Save's.
    w.__prksPendingWorkFolderAttach = { workId: 'W-STALE', tabId: 'tab-1' }
    let patched = false
    w.patchWorkFolder = async () => {
      patched = true
      return null
    }
    w.closeModals = () => {}
    w.prksNavigate = () => {}
    await finishFolderCreate('F-5', null, () => true, null)
    expect(patched).toBe(false)
  })

  it('does not attach a folder when the captured Work panel is gone', async () => {
    const w = effects()
    const pending = { workId: 'W-1', tabId: 'tab-1' }
    w.__prksPendingWorkFolderAttach = pending
    let patched = false
    let alerted = false
    w.prksCapturedWorkPanelStill = () => false
    w.patchWorkFolder = async () => {
      patched = true
      return null
    }
    w.prksAlertMessage = async () => {
      alerted = true
    }
    w.closeModals = () => {}
    w.prksNavigate = () => {
      throw new Error('stale attach must not navigate')
    }
    await finishFolderCreate('F-3', null, () => true, pending)
    expect(patched).toBe(false)
    expect(alerted).toBe(false)
  })
})
