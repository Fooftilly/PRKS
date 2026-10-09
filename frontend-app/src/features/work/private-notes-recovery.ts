/**
 * Work Reminders recovery presentation (#474): the notice in the Reminders
 * card and the shared Review dialog. `ui.js` stays the editor authority (see
 * `recovery-presenter.ts`).
 */
import type { ShallowRef } from 'vue'
import {
  createRecoveryPresenter,
  type ClassicRecoveryAdapter,
  type RecoveryOwner,
} from '../editor-recovery/recovery-presenter'
import type { RecoveryNoticeView } from '../editor-recovery/types'

interface ClassicPrivateRecovery {
  prksWorkPrivateNotesRecoveryView?: ClassicRecoveryAdapter['view']
  prksWorkPrivateNotesRecoveryDetails?: ClassicRecoveryAdapter['details']
  prksWorkPrivateNotesRecoveryRestore?: ClassicRecoveryAdapter['restore']
  prksWorkPrivateNotesRecoveryReplace?: ClassicRecoveryAdapter['replace']
  prksWorkPrivateNotesRecoveryDiscard?: ClassicRecoveryAdapter['discard']
  prksRefreshWorkPrivateNotesRecovery?: ClassicRecoveryAdapter['refresh']
}

const presenter = createRecoveryPresenter({
  subject: 'Reminders',
  adapter: () => {
    const w = window as unknown as ClassicPrivateRecovery
    return {
      view: w.prksWorkPrivateNotesRecoveryView,
      details: w.prksWorkPrivateNotesRecoveryDetails,
      restore: w.prksWorkPrivateNotesRecoveryRestore,
      replace: w.prksWorkPrivateNotesRecoveryReplace,
      discard: w.prksWorkPrivateNotesRecoveryDiscard,
      refresh: w.prksRefreshWorkPrivateNotesRecovery,
    }
  },
})

export function privateNotesRecoveryView(ctx: RecoveryOwner): ShallowRef<RecoveryNoticeView | null> {
  return presenter.view(ctx)
}

export function updatePrivateNotesRecovery(ctx: RecoveryOwner): void {
  presenter.update(ctx)
}

export function forgetPrivateNotesRecovery(ctx?: RecoveryOwner): void {
  presenter.forget(ctx)
}

export function openPrivateNotesRecoveryReview(ctx: RecoveryOwner, opener: HTMLElement | null = null): boolean {
  return presenter.open(ctx, opener)
}

export function registerPrivateNotesRecoveryBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVueUpdateWorkPrivateNotesRecovery?: (ctx: RecoveryOwner) => void
  }
  target.prksVueUpdateWorkPrivateNotesRecovery = (ctx) => updatePrivateNotesRecovery(ctx)
}
