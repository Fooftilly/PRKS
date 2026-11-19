/** Options the classic `prksConfirmDialog` bridge forwards unchanged. */
export interface ModalConfirmOptions {
  title?: string
  message?: string
  messageHtml?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  alertOnly?: boolean
}

/** Options the classic `prksPromptTextDialog` bridge forwards unchanged. */
export interface ModalPromptOptions {
  title?: string
  message?: string
  defaultValue?: string
  okLabel?: string
  cancelLabel?: string
  multiline?: boolean
  rows?: number
}

export interface ModalUnsavedHideOptions {
  restoreFocus?: boolean
}

/**
 * One Vue-owned dialog stack. Classic callers keep their function names and
 * talk only to this object, so confirm, alert, prompt, and unsaved dismissal
 * cannot paint a second presenter.
 */
export interface PrksModalHostApi {
  confirm: (options?: ModalConfirmOptions) => Promise<boolean>
  prompt: (options?: ModalPromptOptions) => Promise<string | null>
  openUnsavedConfirm: (onDiscard: (() => void) | null) => void
  hideUnsavedConfirm: (options?: ModalUnsavedHideOptions) => void
  isConfirmOpen: () => boolean
  isUnsavedConfirmOpen: () => boolean
  handleEscape: (event: KeyboardEvent) => void
  /** True for a feature dialog this host renders (#1036). */
  ownsDialog: (id: string) => boolean
  /** Shows one owned dialog as a new opening; returns its generation. */
  showDialog: (id: string) => number
  /**
   * Runs the form's steps for that opening (prepare, baseline, focus), after
   * the opener has set its own state.
   */
  beginOpening: (id: string) => void
  /** Shows an owned dialog again without starting a new opening. */
  restoreDialog: (id: string) => void
  /** Hides every owned dialog. */
  hideDialogs: () => void
  /** `closeModals` finished: the closed dialogs drop their drafts. */
  dialogsClosed: () => void
  /** True when an owned dialog's fields differ from its opening's baseline. */
  isDialogDirty: (id: string) => boolean
  /** Takes an owned dialog's fields as its baseline now. */
  captureDialogBaseline: (id: string) => void
  /** Drops every owned dialog's baseline. */
  resetDialogBaselines: () => void
}
