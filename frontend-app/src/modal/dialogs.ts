import { reactive } from 'vue'

/**
 * Open state for the feature dialogs ModalHost renders (#1036).
 *
 * One dialog is open at a time. Each opening gets a new generation, so a
 * save or a prepare that settles after its dialog closed or reopened can
 * tell it is stale. `ModalDialog.vue` reflects this state onto its own root
 * (`hidden`, `data-prks-open-generation`, `inert`) for the readers that
 * still query the DOM.
 */
interface DialogState {
  active: string | null
  generations: Record<string, number>
  inert: Record<string, boolean>
}

export const dialogState = reactive<DialogState>({ active: null, generations: {}, inert: {} })

const registered = new Set<string>()

export function registerDialog(id: string): void {
  registered.add(id)
}

export function unregisterDialog(id: string): void {
  registered.delete(id)
  if (dialogState.active === id) dialogState.active = null
}

export function ownsDialog(id: string): boolean {
  return registered.has(id)
}

/** Opens `id` as a new opening and returns its generation. */
export function showDialog(id: string): number {
  const generation = (dialogState.generations[id] ?? 0) + 1
  dialogState.generations[id] = generation
  dialogState.inert[id] = false
  dialogState.active = id
  return generation
}

/**
 * Shows `id` again without starting a new opening, so work still in flight
 * for it stays current (New File after its nested New Playlist closes).
 */
export function restoreDialog(id: string): void {
  dialogState.active = id
}

export function hideDialogs(): void {
  dialogState.active = null
  for (const id of Object.keys(dialogState.inert)) dialogState.inert[id] = false
}

export function setDialogInert(id: string, on: boolean): void {
  dialogState.inert[id] = on
}

export function isDialogOpen(id: string): boolean {
  return dialogState.active === id
}

export function resetDialogsForTest(): void {
  registered.clear()
  dialogState.active = null
  dialogState.generations = {}
  dialogState.inert = {}
}
