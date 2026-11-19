import { inject, onBeforeUnmount, provide, reactive, type InjectionKey } from 'vue'
import { snapshotModalFields } from './snapshot'

/**
 * Open state for the feature dialogs ModalHost renders (#1036).
 *
 * One dialog is open at a time. Each opening gets a new generation, so a
 * save or a prepare that settles after its dialog closed or reopened can
 * tell it is stale. `ModalDialog.vue` reflects this state onto its own root
 * (`hidden`, `data-prks-open-generation`, `inert`, `data-prks-dialog-ready`)
 * for the readers that still query the DOM.
 *
 * Each dialog's form registers its lifecycle here (`useDialogLifecycle`), so
 * an opening runs the form's own steps: prepare, then the unsaved-changes
 * baseline, then focus. `openModal` in `ui.js` only shows the dialog, sets
 * the opener's own state, and starts the opening.
 */
interface DialogState {
  active: string | null
  generations: Record<string, number>
  inert: Record<string, boolean>
  /** The opening is prepared, its baseline taken and its first field focused. */
  ready: Record<string, boolean>
}

export const dialogState = reactive<DialogState>({ active: null, generations: {}, inert: {}, ready: {} })

/** One opening of a dialog, as its form sees it. */
export interface DialogOpening {
  generation: number
  /** False once the dialog closed, was covered, or opened again. */
  isCurrent: () => boolean
}

/** A form's own steps around its dialog. Every hook is optional. */
export interface DialogLifecycle {
  /**
   * Prepares a new opening: a blank form, the catalogues it lists. A returned
   * promise keeps the dialog inert until it settles, and the baseline and
   * focus wait for it.
   */
  opened?: (opening: DialogOpening) => Promise<void> | void
  /** Focuses the form's first field once the opening is prepared. */
  focusInitial?: () => void
  /** Runs on every `closeModals` while this dialog is closed: drop the draft. */
  closed?: () => void
  /**
   * Escape's inner layer: closes an open list and returns true, so the
   * dialog itself stays open. False when none was open.
   */
  dismissInnerLayer?: () => boolean
}

const registered = new Set<string>()
const lifecycles = new Map<string, DialogLifecycle>()
const baselines = new Map<string, string>()

export function registerDialog(id: string): void {
  registered.add(id)
}

export function unregisterDialog(id: string): void {
  registered.delete(id)
  lifecycles.delete(id)
  baselines.delete(id)
  if (dialogState.active === id) dialogState.active = null
}

export function ownsDialog(id: string): boolean {
  return registered.has(id)
}

export function setDialogLifecycle(id: string, lifecycle: DialogLifecycle): void {
  lifecycles.set(id, lifecycle)
}

/** Drops `lifecycle` for `id`, unless another one replaced it since. */
export function clearDialogLifecycle(id: string, lifecycle: DialogLifecycle): void {
  if (lifecycles.get(id) === lifecycle) lifecycles.delete(id)
}

/** Opens `id` as a new opening and returns its generation. */
export function showDialog(id: string): number {
  const generation = (dialogState.generations[id] ?? 0) + 1
  dialogState.generations[id] = generation
  dialogState.inert[id] = false
  dialogState.ready[id] = false
  baselines.delete(id)
  dialogState.active = id
  return generation
}

function dialogRoot(id: string): HTMLElement | null {
  const el = document.getElementById(id)
  return el instanceof HTMLElement ? el : null
}

/**
 * Runs the form's steps for the opening `showDialog` just started: prepare
 * (inert while a returned promise is pending), then the baseline, then focus.
 * `openModal` calls this after it has set the opener's own state, which the
 * form may read (the role linker's opener, a Work's pending Folder attach).
 */
export function beginOpening(id: string): void {
  if (!registered.has(id) || dialogState.active !== id) return
  const generation = dialogState.generations[id] ?? 0
  const isCurrent = () => dialogState.active === id && dialogState.generations[id] === generation
  const lifecycle = lifecycles.get(id)
  const settle = () => {
    if (!isCurrent()) return
    dialogState.inert[id] = false
    captureDialogBaseline(id)
    lifecycle?.focusInitial?.()
    requestAnimationFrame(() => {
      if (isCurrent()) dialogState.ready[id] = true
    })
  }
  let pending: Promise<void> | void = undefined
  try {
    pending = lifecycle?.opened?.({ generation, isCurrent })
  } catch (error) {
    console.error(`[prks] ${id}: opening failed`, error)
  }
  if (pending && typeof pending.then === 'function') {
    dialogState.inert[id] = true
    // Two-argument then: a throw in settle must not run it a second time.
    pending.then(settle, settle)
  } else {
    settle()
  }
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

/**
 * `closeModals` finished: every closed dialog drops its draft (#373). An
 * opener that prefills a form writes it after this, right before `openModal`.
 */
export function dialogsClosed(): void {
  for (const [id, lifecycle] of lifecycles) {
    if (dialogState.active !== id) lifecycle.closed?.()
  }
}

export function setDialogInert(id: string, on: boolean): void {
  dialogState.inert[id] = on
}

export function isDialogOpen(id: string): boolean {
  return dialogState.active === id
}

/** The fields as the opening left them, for the unsaved-changes confirm. */
export function captureDialogBaseline(id: string): void {
  const root = dialogRoot(id)
  if (registered.has(id) && root) baselines.set(id, snapshotModalFields(root))
}

/** True when the fields differ from the opening's baseline. False without one. */
export function isDialogDirty(id: string): boolean {
  const baseline = baselines.get(id)
  const root = dialogRoot(id)
  if (baseline == null || !root) return false
  return snapshotModalFields(root) !== baseline
}

export function resetDialogBaselines(): void {
  baselines.clear()
}

/** Escape's inner layer for the open dialog. True when its form closed one. */
export function dismissDialogInnerLayer(id: string): boolean {
  if (dialogState.active !== id) return false
  return lifecycles.get(id)?.dismissInnerLayer?.() === true
}

const DIALOG_ID: InjectionKey<string> = Symbol('prks-dialog-id')

/** `ModalDialog` names itself to the form in its body. */
export function provideDialogId(id: string): void {
  provide(DIALOG_ID, id)
}

/**
 * Registers a form's lifecycle with the dialog it is the body of. A form
 * mounted outside a `ModalDialog` (a unit test) registers nothing.
 */
export function useDialogLifecycle(lifecycle: DialogLifecycle): void {
  const id = inject(DIALOG_ID, null)
  if (!id) return
  setDialogLifecycle(id, lifecycle)
  onBeforeUnmount(() => clearDialogLifecycle(id, lifecycle))
}

export function resetDialogsForTest(): void {
  registered.clear()
  lifecycles.clear()
  baselines.clear()
  dialogState.active = null
  dialogState.generations = {}
  dialogState.inert = {}
  dialogState.ready = {}
}
