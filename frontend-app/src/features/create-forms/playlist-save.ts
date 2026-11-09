import type { PlaylistPending } from './submit'

export type { PlaylistPending }

export interface PlaylistSaveDeps {
  modal: () => HTMLElement | null
  isBusy: () => boolean
  setBusy: (busy: boolean) => void
  readDraft: () => { title: string; description: string } | null
  createPlaylist: (title: string, description: string) => Promise<string | null>
  pendingAttach: () => PlaylistPending | null | undefined
  clearPendingIfSame: (captured: PlaylistPending | null | undefined) => void
  capturedWorkStill: (pending: PlaylistPending | null | undefined) => boolean
  addWorkToPlaylist: (
    playlistId: string,
    workId: string,
    still: () => boolean,
  ) => Promise<unknown>
  refreshSelects: (playlistId: string) => Promise<void>
  refreshAllSelects: (playlistId: string) => Promise<void>
  takeIndexCreateTabId: () => string
  returnToWorkModal: () => boolean
  closeModals: () => void
  navigate: (hash: string, options?: { tabId?: string }) => void
  onError: (message: string) => Promise<void>
}

/**
 * One in-flight create per modal opening.
 * Capture the Work attachment for this Save before awaiting createPlaylist;
 * a later opener must not receive the first playlist, and an older completion
 * must not close or navigate a newer draft.
 */
export function createPlaylistSaveGate() {
  let inFlight: { opening: string } | null = null

  async function save(deps: PlaylistSaveDeps): Promise<void> {
    const playlistModal = deps.modal()
    const opening = playlistModal ? playlistModal.dataset.prksOpenGeneration || '' : ''
    const matchesOpening = () =>
      !!playlistModal && playlistModal.dataset.prksOpenGeneration === opening
    const isLiveOpening = () => matchesOpening() && !playlistModal!.classList.contains('hidden')
    if (inFlight && inFlight.opening === opening) return
    if (deps.isBusy()) return
    const draft = deps.readDraft()
    if (!draft) return
    const pending = deps.pendingAttach() ?? null
    const saveToken = { opening }
    inFlight = saveToken
    deps.setBusy(true)
    try {
      let newId: string | null
      try {
        newId = await deps.createPlaylist(draft.title, draft.description)
      } catch (error) {
        if (!isLiveOpening()) return
        const message =
          error instanceof Error && error.message ? error.message : 'Could not create playlist.'
        await deps.onError(message)
        return
      } finally {
        if (inFlight === saveToken) {
          inFlight = null
          if (isLiveOpening()) deps.setBusy(false)
        }
      }
      if (!newId) {
        if (isLiveOpening()) await deps.onError('Could not create playlist')
        return
      }
      if (!matchesOpening()) return
      deps.closeModals()
      const still = () => deps.capturedWorkStill(pending)
      let attachedWork = false
      if (pending && pending.workId && still()) {
        try {
          await deps.addWorkToPlaylist(newId, String(pending.workId), still)
          attachedWork = !!still()
        } catch {
          /* The durable layer reports the failure on the next read. */
        }
      }
      deps.clearPendingIfSame(pending)
      if (!matchesOpening()) return
      if (attachedWork) {
        await deps.refreshSelects(newId)
        await deps.refreshAllSelects(newId)
      }
      if (deps.returnToWorkModal()) return
      const createTabId = deps.takeIndexCreateTabId()
      if (!attachedWork && createTabId) {
        deps.navigate(`#/playlists/${encodeURIComponent(newId)}`, { tabId: createTabId })
      }
    } finally {
      if (inFlight === saveToken) {
        inFlight = null
        if (isLiveOpening()) deps.setBusy(false)
      }
    }
  }

  return { save }
}
