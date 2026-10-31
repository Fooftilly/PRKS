/**
 * Bridge from the classic modal shells to the Vue create forms.
 *
 * Temporary adapter (#1036). `ModalHost` paints confirm, alert, prompt, and
 * unsaved dismissal only. Folder, Person, Person Group, and Playlist bodies
 * teleport into the shells in `frontend/index.html`. Retire this object when
 * those shells move into the Vue host. Work, Saved View, role link, the
 * person template, and Settings stay on their current shells until their
 * own slices.
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
}

export const CREATE_FORM_MODAL_IDS = [
  'folder-modal',
  'person-modal',
  'group-modal',
  'playlist-modal',
] as const

export type CreateFormModalId = (typeof CREATE_FORM_MODAL_IDS)[number]
