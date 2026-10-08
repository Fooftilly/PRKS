/**
 * Research Notes recovery presentation (#466 slice 3): the pane notice and
 * the Review dialog. `works.js` stays the editor authority: it builds the
 * notice view and the review details, and performs every action against the
 * editor session the dialog was opened for. This module only renders them.
 */
import { h, render, shallowRef, type ShallowRef } from 'vue'
import EditorRecoveryReview from '../editor-recovery/EditorRecoveryReview.vue'
import type {
  RecoveryActionResult,
  RecoveryCandidateView,
  RecoveryCurrentNote,
  RecoveryDetails,
  RecoveryExpectation,
  RecoveryNoticeView,
  ReviewActions,
} from '../editor-recovery/types'

export interface RecoveryOwner {
  tabId?: unknown
  destroyed?: boolean
  getEntity?: (type: string) => { id?: unknown; title?: unknown } | null
}

interface ClassicRecovery {
  prksResearchNotesRecoveryView?: (ctx: RecoveryOwner) => RecoveryNoticeView | null
  prksResearchNotesRecoveryDetails?: (ctx: RecoveryOwner, workId: string) => Promise<RecoveryDetails | null>
  prksResearchNotesRecoveryRestore?: (ctx: RecoveryOwner, workId: string, token: string, expect: RecoveryExpectation) => Promise<RecoveryActionResult>
  prksResearchNotesRecoveryReplace?: (
    ctx: RecoveryOwner,
    workId: string,
    token: string,
    expect: RecoveryExpectation,
    text: string,
    shown: { text: string; revision: number | null },
  ) => Promise<RecoveryActionResult>
  prksResearchNotesRecoveryDiscard?: (ctx: RecoveryOwner, workId: string, token: string, expect: RecoveryExpectation) => Promise<RecoveryActionResult>
  prksRefreshResearchNotesRecovery?: (ctx: RecoveryOwner, workId: string) => Promise<unknown>
  prksCopyTextToClipboard?: (text: string) => Promise<void>
  prksConfirmDialog?: (options: { title: string; message: string; confirmLabel: string; danger: boolean }) => Promise<boolean>
}

function classic(): ClassicRecovery {
  return window as unknown as ClassicRecovery
}

const SUBJECT = 'Research Notes'
const views = new Map<string, ShallowRef<RecoveryNoticeView | null>>()

interface OpenReview {
  host: HTMLElement
  ctx: RecoveryOwner
  tabId: string
  workId: string
  opener: HTMLElement | null
}
let openReview: OpenReview | null = null

function tabKey(ctx: RecoveryOwner): string {
  return String(ctx && ctx.tabId != null ? ctx.tabId : '')
}

function readView(ctx: RecoveryOwner): RecoveryNoticeView | null {
  const read = classic().prksResearchNotesRecoveryView
  try {
    return read ? read(ctx) : null
  } catch {
    return null
  }
}

/** The pane's reactive notice view, filled from the editor adapter now. */
export function researchNotesRecoveryView(ctx: RecoveryOwner): ShallowRef<RecoveryNoticeView | null> {
  const key = tabKey(ctx)
  let view = views.get(key)
  if (!view) {
    view = shallowRef<RecoveryNoticeView | null>(null)
    views.set(key, view)
  }
  view.value = readView(ctx)
  return view
}

/** The adapter's state changed: repaint this pane's notice. */
export function updateResearchNotesRecovery(ctx: RecoveryOwner): void {
  const view = views.get(tabKey(ctx))
  if (view) view.value = readView(ctx)
}

export function forgetResearchNotesRecovery(ctx?: RecoveryOwner): void {
  if (ctx) {
    views.delete(tabKey(ctx))
    if (openReview && openReview.tabId === tabKey(ctx)) closeResearchNotesRecoveryReview()
    return
  }
  views.clear()
  closeResearchNotesRecoveryReview()
}

function liveWorkId(ctx: RecoveryOwner): string {
  const live = ctx && !ctx.destroyed && ctx.getEntity ? ctx.getEntity('work') : null
  return live && live.id != null ? String(live.id) : ''
}

const unavailable: RecoveryActionResult = { ok: false, code: 'unavailable' }

function actionsFor(ctx: RecoveryOwner, workId: string): ReviewActions {
  const api = classic()
  // A dialog left open across a Work switch or pane change never acts.
  const current = () => liveWorkId(ctx) === workId
  return {
    async load() {
      if (!current() || !api.prksResearchNotesRecoveryDetails) return null
      return api.prksResearchNotesRecoveryDetails(ctx, workId)
    },
    async restore(details: RecoveryDetails, c: RecoveryCandidateView) {
      if (!current() || !api.prksResearchNotesRecoveryRestore) return unavailable
      return api.prksResearchNotesRecoveryRestore(ctx, workId, details.token, c.expect)
    },
    async replace(details: RecoveryDetails, c: RecoveryCandidateView, text: string, shown: RecoveryCurrentNote) {
      if (!current() || !api.prksResearchNotesRecoveryReplace) return unavailable
      return api.prksResearchNotesRecoveryReplace(ctx, workId, details.token, c.expect, text, { text: shown.text, revision: shown.revision })
    },
    async discard(details: RecoveryDetails, c: RecoveryCandidateView) {
      if (!current() || !api.prksResearchNotesRecoveryDiscard) return unavailable
      return api.prksResearchNotesRecoveryDiscard(ctx, workId, details.token, c.expect)
    },
    async copy(text: string) {
      if (!api.prksCopyTextToClipboard) throw new Error('Copy is unavailable')
      await api.prksCopyTextToClipboard(text)
    },
    async confirm(options) {
      return api.prksConfirmDialog ? api.prksConfirmDialog(options) : false
    },
  }
}

/** Opens Review for the Work this pane shows. One Review at a time. */
export function openResearchNotesRecoveryReview(ctx: RecoveryOwner, opener: HTMLElement | null = null): boolean {
  const workId = liveWorkId(ctx)
  if (!workId) return false
  closeResearchNotesRecoveryReview()
  const live = ctx.getEntity ? ctx.getEntity('work') : null
  const title = live && typeof live.title === 'string' && live.title ? live.title : 'This Work'
  const host = document.createElement('div')
  host.setAttribute('data-prks-role', 'editor-recovery-review-host')
  document.body.appendChild(host)
  openReview = { host, ctx, tabId: tabKey(ctx), workId, opener }
  render(
    h(EditorRecoveryReview, {
      subject: SUBJECT,
      entityTitle: title,
      actions: actionsFor(ctx, workId),
      onClose: () => closeResearchNotesRecoveryReview(),
    }),
    host,
  )
  return true
}

/**
 * Closes Review without changing anything, then refreshes the pane's notice.
 * Escape passes the dialog element; only the open dialog closes.
 */
export function closeResearchNotesRecoveryReview(modal?: Element | null): boolean {
  const open = openReview
  if (!open) return false
  if (modal instanceof Element && !open.host.contains(modal)) return false
  openReview = null
  render(null, open.host)
  open.host.remove()
  if (liveWorkId(open.ctx) === open.workId) {
    const refresh = classic().prksRefreshResearchNotesRecovery
    if (refresh) void refresh(open.ctx, open.workId).catch(() => undefined)
    if (open.opener && open.opener.isConnected) open.opener.focus()
  }
  return true
}

export function registerResearchNotesRecoveryBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVueUpdateResearchNotesRecovery?: (ctx: RecoveryOwner) => void
    prksVueCloseResearchNotesRecoveryReview?: (modal?: Element | null) => boolean
  }
  target.prksVueUpdateResearchNotesRecovery = (ctx) => updateResearchNotesRecovery(ctx)
  target.prksVueCloseResearchNotesRecoveryReview = (modal) => closeResearchNotesRecoveryReview(modal)
}
