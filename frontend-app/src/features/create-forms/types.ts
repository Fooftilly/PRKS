/**
 * Bridge from the classic modal shells to the Vue create forms.
 *
 * Temporary adapter (#1036). `ModalHost` paints confirm, alert, prompt, and
 * unsaved dismissal only. Folder, Person, Person Group, and Playlist bodies
 * teleport into the shells in `frontend/index.html`, and so do New File's
 * body and footer, the Saved View body and title, and the Link Person to Work
 * body. Retire this object when those shells move into the Vue host. The
 * person template and Settings stay on their current shells until their own
 * slices. Saved View and Playlist have no unsaved-changes confirm (`ui.js`).
 */
export interface PrksCreateFormsApi {
  owns: (modalId: string) => boolean
  isDirty: (modalId: string) => boolean
  captureBaseline: (modalId: string) => void
  resetBaselines: () => void
  releaseFolderSave: () => void
  refreshFolderValidation: () => Promise<void>
  prepareGroup: () => Promise<void>
  resetPlaylistFields: () => void
  afterPersonReset: () => void
  /**
   * New File's opening: a blank form, the People and Folder catalogues, and
   * the default Folder. Resolves once the dialog may thaw; work after a
   * newer opening is dropped through `stillOpening`.
   */
  prepareNewFile: (stillOpening: () => boolean) => Promise<void>
  /**
   * Link Person to Work's opening: a blank form, the People and File
   * catalogues, and the opener's Person or File. Resolves once the dialog may
   * thaw; work after a newer opening is dropped through `stillOpening`.
   */
  prepareRoleLink: (stillOpening: () => boolean) => Promise<void>
  /** Focuses the first field of an owned dialog that `openModal` just showed. */
  focusInitial: (modalId: string) => void
  /** Clears the Folder and Group drafts once `closeModals` has hidden them. */
  resetClosed: () => void
  /**
   * Escape's inner layer for an owned dialog: closes an open parent list and
   * returns true, so the dialog itself stays open. False when none was open.
   */
  dismissInnerLayer: (modalId: string) => boolean
}

export const CREATE_FORM_MODAL_IDS = [
  'folder-modal',
  'person-modal',
  'group-modal',
  'playlist-modal',
  'work-modal',
  'saved-view-modal',
  'role-modal',
] as const

export type CreateFormModalId = (typeof CREATE_FORM_MODAL_IDS)[number]
