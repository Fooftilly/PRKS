export interface FolderDraft {
  title: string
  description: string
  parentId: string
}

interface PendingAttach {
  workId?: string
  tabId?: string
  generation?: number
}

interface TabCtx {
  tabId?: string
  ui?: { workFolderEditing?: boolean }
  lastResolvedRoute?: { name?: string }
  route?: { name?: string }
}

interface FolderEffectsWindow {
  __prksPendingWorkFolderAttach?: PendingAttach | null
  closeModals?: () => void
  prksGetTabContext?: (tabId: string) => TabCtx | null
  prksCapturedWorkPanelStill?: (pending: PendingAttach | null | undefined) => boolean
  patchWorkFolder?: (workId: string, folderId: string, still: () => boolean) => Promise<unknown>
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
  fetchWorkDetails?: (workId: string) => Promise<unknown>
  prksOfflineCacheEntityIfCurrent?: (
    kind: string,
    id: string,
    entity: unknown,
    token: unknown,
  ) => void
  prksApplyOwnedWorkEntity?: (owner: TabCtx | null, workId: string, work: unknown) => boolean
  prksTabContextIsFocused?: (owner: TabCtx | null) => boolean
  updatePanelContent?: (tab: string) => void
  prksNavigate?: (hash: string, options?: { replace?: boolean; tabId?: string }) => void
}

function effectsWindow(): FolderEffectsWindow {
  return window as unknown as FolderEffectsWindow
}

/**
 * After `createFolder` resolves: attach the new folder to the Work that
 * opened the modal, or open the new folder (the Folder Library root stays
 * on the library). The owner is the one
 * captured when Save was pressed.
 */
export async function finishFolderCreate(
  newFolderId: string,
  ownerCtx: unknown,
  stillOpening: () => boolean,
): Promise<void> {
  const w = effectsWindow()
  const owner = (ownerCtx || null) as TabCtx | null
  const pending = w.__prksPendingWorkFolderAttach
  if (pending && pending.workId && typeof w.patchWorkFolder === 'function') {
    const attachWid = String(pending.workId)
    const opener = pending.tabId && w.prksGetTabContext ? w.prksGetTabContext(String(pending.tabId)) : null
    const still = () => typeof w.prksCapturedWorkPanelStill === 'function' && w.prksCapturedWorkPanelStill(pending)
    w.__prksPendingWorkFolderAttach = null
    w.closeModals?.()
    if (!still()) return
    let attachCoherenceToken: unknown = null
    try {
      attachCoherenceToken = await w.patchWorkFolder(attachWid, newFolderId, still)
    } catch (error) {
      if (still()) {
        const message =
          error instanceof Error && error.message
            ? error.message
            : 'Folder created but could not assign this file.'
        await w.prksAlertMessage?.(message, 'Error')
      }
    }
    if (!still()) return
    if (typeof w.fetchWorkDetails === 'function') {
      const work = await w.fetchWorkDetails(attachWid)
      if (work && w.prksOfflineCacheEntityIfCurrent && attachCoherenceToken != null) {
        void w.prksOfflineCacheEntityIfCurrent('work', attachWid, work, attachCoherenceToken)
      }
      if (w.prksApplyOwnedWorkEntity?.(opener, attachWid, work)) {
        if (opener && opener.ui) opener.ui.workFolderEditing = false
        if (w.prksTabContextIsFocused?.(opener)) w.updatePanelContent?.('details')
      }
    }
    return
  }
  if (!stillOpening()) return
  w.closeModals?.()
  if (typeof w.prksNavigate !== 'function') return
  const ownerRoute = owner && (owner.lastResolvedRoute || owner.route)
  const tabOption = owner && owner.tabId ? { tabId: owner.tabId } : {}
  // Saved from the Folder Library root: the library already lists the new folder.
  if (ownerRoute && ownerRoute.name === 'folders') {
    w.prksNavigate('#/folders', { replace: true, ...tabOption })
    return
  }
  // Anywhere else, open the folder just made in the tab that saved it (#827),
  // as New Group, New Person and New File open what they create.
  w.prksNavigate(`#/folders/${encodeURIComponent(newFolderId)}`, tabOption)
}
