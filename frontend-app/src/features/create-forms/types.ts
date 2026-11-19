/**
 * The create-form dialogs ModalHost renders (#1036), one `ModalDialog` per
 * form. Each form runs its own opening, close and Escape steps through
 * `useDialogLifecycle` (`src/modal/dialogs.ts`). Settings stays on its own
 * shell. Saved View, Playlist and the person template have no
 * unsaved-changes confirm (`ui.js`).
 */
export const CREATE_FORM_MODAL_IDS = [
  'folder-modal',
  'person-modal',
  'group-modal',
  'playlist-modal',
  'work-modal',
  'saved-view-modal',
  'role-modal',
  'person-template-modal',
] as const

export type CreateFormModalId = (typeof CREATE_FORM_MODAL_IDS)[number]
