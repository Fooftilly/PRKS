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
  /** Shows an owned dialog again without starting a new opening. */
  restoreDialog: (id: string) => void
  /** Hides every owned dialog. */
  hideDialogs: () => void
  /** Keeps an owned dialog inert while its opening prepares. */
  setDialogInert: (id: string, on: boolean) => void
}
