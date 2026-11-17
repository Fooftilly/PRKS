export interface FolderDraft {
  title: string
  description: string
  parentId: string
}

export interface PendingAttach {
  workId?: string
  tabId?: string
  generation?: number
  /** The Folder dialog opening this attach was made for (`openModal` stamps it). */
  modalOpening?: string
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
  pending: PendingAttach | null = null,
): Promise<void> {
  const w = effectsWindow()
  const owner = (ownerCtx || null) as TabCtx | null
  if (pending && pending.workId && typeof w.patchWorkFolder === 'function') {
    const attachWid = String(pending.workId)
    const opener = pending.tabId && w.prksGetTabContext ? w.prksGetTabContext(String(pending.tabId)) : null
    const still = () => typeof w.prksCapturedWorkPanelStill === 'function' && w.prksCapturedWorkPanelStill(pending)
    // Clear only this Save's attach; a newer opener may have set its own.
    if (w.__prksPendingWorkFolderAttach === pending) w.__prksPendingWorkFolderAttach = null
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
      let work: unknown
      try {
        work = await w.fetchWorkDetails(attachWid)
      } catch {
        // The folder is attached; leave folder-edit mode and let the panel
        // re-read on its next refresh.
        if (!still()) return
        if (opener && opener.ui) opener.ui.workFolderEditing = false
        if (w.prksTabContextIsFocused?.(opener)) w.updatePanelContent?.('details')
        return
      }
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
  // The tab that saved may have closed while the create resolved. Navigation
  // would then fall back to whichever tab has focus, so drop it instead.
  if (owner && owner.tabId && typeof w.prksGetTabContext === 'function' && !w.prksGetTabContext(owner.tabId)) {
    return
  }
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
