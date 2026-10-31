import type { FolderDraft } from './folder-effects'

export interface FolderSaveDeps {
  modal: () => HTMLElement | null
  buttonDisabled: () => boolean
  setBusy: (busy: boolean) => void
  readDraft: () => FolderDraft
  createFolder: (title: string, description: string, parentId: string | null) => Promise<string>
  alert: (message: string, title: string) => Promise<void>
  guardRefusal: (error: unknown) => boolean
  revalidate: () => void
  owner: () => unknown
  afterCreate: (newId: string, owner: unknown, stillOpening: () => boolean) => Promise<void>
}

/**
 * One in-flight create per modal opening.
 * A newer opening may save while an older create is still resolving.
 * The older completion must not clear the newer opening's busy state.
 */
export function createFolderSaveGate() {
  let inFlight: { opening: string } | null = null

  async function save(deps: FolderSaveDeps): Promise<void> {
    const folderModal = deps.modal()
    const opening = folderModal ? folderModal.dataset.prksOpenGeneration || '' : ''
    const isCurrentOpening = () =>
      !!folderModal &&
      !folderModal.classList.contains('hidden') &&
      folderModal.dataset.prksOpenGeneration === opening
    if (inFlight && inFlight.opening === opening) return
    if (deps.buttonDisabled()) return
    const saveToken = { opening }
    inFlight = saveToken
    deps.setBusy(true)
    const owner = deps.owner()
    try {
      const draft = deps.readDraft()
      const parentId = draft.parentId.trim() ? draft.parentId.trim() : null
      let newFolderId: string
      try {
        newFolderId = await deps.createFolder(draft.title, draft.description, parentId)
      } catch (error) {
        if (!isCurrentOpening()) return
        if (!deps.guardRefusal(error)) {
          const message = error instanceof Error && error.message ? error.message : 'Could not create folder'
          await deps.alert(message, 'Could not save')
        }
        return
      } finally {
        if (inFlight === saveToken) {
          inFlight = null
          if (isCurrentOpening()) {
            deps.setBusy(false)
            deps.revalidate()
          }
        }
      }
      if (!isCurrentOpening()) return
      await deps.afterCreate(newFolderId, owner, isCurrentOpening)
    } finally {
      if (inFlight === saveToken) {
        inFlight = null
        if (isCurrentOpening()) {
          deps.setBusy(false)
          deps.revalidate()
        }
      }
    }
  }

  return { save }
}
