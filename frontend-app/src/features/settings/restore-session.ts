import { reactive } from 'vue'

/**
 * One backup restore, shared by the Settings Backup panel and its "Replace
 * library?" confirmation dialog (#1036). The panel stages and verifies the
 * backup and owns the restore call; the dialog, which ModalHost renders,
 * only collects the typed confirmation and asks the panel to restore.
 */
export const RESTORE_CONFIRM_WORD = 'RESTORE'

export const restoreSession = reactive({
  /** A verify or a restore is running. */
  busy: false,
  /** The confirmation dialog's typed word and its outcome line. */
  confirmText: '',
  confirmStatus: '',
})

let restoreHandler: (() => Promise<void>) | null = null

/** The Backup panel installs its restore while it is mounted. */
export function setRestoreHandler(handler: (() => Promise<void>) | null): void {
  restoreHandler = handler
}

export function confirmReady(): boolean {
  return !restoreSession.busy && restoreSession.confirmText.trim() === RESTORE_CONFIRM_WORD
}

/** The dialog's Restore: runs the panel's restore once the word is typed. */
export function requestRestore(): Promise<void> {
  if (!confirmReady() || !restoreHandler) return Promise.resolve()
  return restoreHandler()
}

export function resetRestoreConfirm(): void {
  restoreSession.confirmText = ''
  restoreSession.confirmStatus = ''
}
