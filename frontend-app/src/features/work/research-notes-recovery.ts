/**
 * Research Notes recovery presentation (#466 slice 3): the pane notice and
 * the Review dialog, through the shared recovery presenter. `works.js` stays
 * the editor authority (see `recovery-presenter.ts`).
 */
import type { ShallowRef } from 'vue'
import {
  closeRecoveryReview,
  createRecoveryPresenter,
  registerRecoveryReviewBridge,
  requestRecoveryReviewClose,
  type ClassicRecoveryAdapter,
  type RecoveryOwner,
} from '../editor-recovery/recovery-presenter'
import type { RecoveryNoticeView } from '../editor-recovery/types'

export type { RecoveryOwner } from '../editor-recovery/recovery-presenter'

interface ClassicResearchRecovery {
  prksResearchNotesRecoveryView?: ClassicRecoveryAdapter['view']
  prksResearchNotesRecoveryDetails?: ClassicRecoveryAdapter['details']
  prksResearchNotesRecoveryRestore?: ClassicRecoveryAdapter['restore']
  prksResearchNotesRecoveryReplace?: ClassicRecoveryAdapter['replace']
  prksResearchNotesRecoveryDiscard?: ClassicRecoveryAdapter['discard']
  prksRefreshResearchNotesRecovery?: ClassicRecoveryAdapter['refresh']
}

const presenter = createRecoveryPresenter({
  subject: 'Research Notes',
  adapter: () => {
    const w = window as unknown as ClassicResearchRecovery
    return {
      view: w.prksResearchNotesRecoveryView,
      details: w.prksResearchNotesRecoveryDetails,
      restore: w.prksResearchNotesRecoveryRestore,
      replace: w.prksResearchNotesRecoveryReplace,
      discard: w.prksResearchNotesRecoveryDiscard,
      refresh: w.prksRefreshResearchNotesRecovery,
    }
  },
})

/** The pane's reactive notice view, filled from the editor adapter now. */
export function researchNotesRecoveryView(ctx: RecoveryOwner): ShallowRef<RecoveryNoticeView | null> {
  return presenter.view(ctx)
}

/** The adapter's state changed: repaint this pane's notice. */
export function updateResearchNotesRecovery(ctx: RecoveryOwner): void {
  presenter.update(ctx)
}

export function forgetResearchNotesRecovery(ctx?: RecoveryOwner): void {
  presenter.forget(ctx)
}

/** Opens Review for the Work this pane shows. One Review at a time. */
export function openResearchNotesRecoveryReview(ctx: RecoveryOwner, opener: HTMLElement | null = null): boolean {
  return presenter.open(ctx, opener)
}

export function closeResearchNotesRecoveryReview(modal?: Element | null): boolean {
  return closeRecoveryReview(modal)
}

export function requestResearchNotesRecoveryReviewClose(modal?: Element | null): boolean {
  return requestRecoveryReviewClose(modal)
}

export function registerResearchNotesRecoveryBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVueUpdateResearchNotesRecovery?: (ctx: RecoveryOwner) => void
  }
  target.prksVueUpdateResearchNotesRecovery = (ctx) => updateResearchNotesRecovery(ctx)
  registerRecoveryReviewBridge(root)
}
