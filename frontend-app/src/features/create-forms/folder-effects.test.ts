import { describe, expect, it } from 'vitest'
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

describe('folder create follow-up', () => {
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
    await finishFolderCreate('F-9', { tabId: 'library' }, () => true)
    expect(w.__prksPendingWorkFolderAttach).toBeNull()
    expect(opener.ui.workFolderEditing).toBe(false)
    expect(calls).toEqual(['close', 'patch:W-1:F-9', 'cache', 'panel:details'])
    delete w.__prksPendingWorkFolderAttach
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

  it('does not attach a folder when the captured Work panel is gone', async () => {
    const w = effects()
    w.__prksPendingWorkFolderAttach = { workId: 'W-1', tabId: 'tab-1' }
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
    await finishFolderCreate('F-3', null, () => true)
    expect(patched).toBe(false)
    expect(alerted).toBe(false)
    delete w.__prksPendingWorkFolderAttach
  })
})
