/**
 * Recovery presentation shared by the Work note editors (#466 slice 3, #474):
 * a pane notice per editor and one Review dialog for the page. The classic
 * adapter of each editor (`works.js` for Research Notes, `ui.js` for
 * Reminders, both over `work-note-recovery.js`) stays the authority: it builds
 * the notice view and the review details, and performs every action against
 * the editor session the dialog was opened for. This module only renders them.
 */
import { h, render, shallowRef, type ShallowRef } from 'vue'
import EditorRecoveryReview from './EditorRecoveryReview.vue'
import type {
  RecoveryActionResult,
  RecoveryCandidateView,
  RecoveryCurrentNote,
  RecoveryDetails,
  RecoveryExpectation,
  RecoveryNoticeView,
  ReviewActions,
} from './types'

export interface RecoveryOwner {
  tabId?: unknown
  destroyed?: boolean
  getEntity?: (type: string) => { id?: unknown; title?: unknown } | null
}

/** One editor's classic recovery adapter, as `window` functions. */
export interface ClassicRecoveryAdapter {
  view?: (ctx: RecoveryOwner) => RecoveryNoticeView | null
  details?: (ctx: RecoveryOwner, workId: string) => Promise<RecoveryDetails | null>
  restore?: (ctx: RecoveryOwner, workId: string, token: string, expect: RecoveryExpectation) => Promise<RecoveryActionResult>
  replace?: (
    ctx: RecoveryOwner,
    workId: string,
    token: string,
    expect: RecoveryExpectation,
    text: string,
    shown: { text: string; revision: number | null },
  ) => Promise<RecoveryActionResult>
  discard?: (ctx: RecoveryOwner, workId: string, token: string, expect: RecoveryExpectation) => Promise<RecoveryActionResult>
  refresh?: (ctx: RecoveryOwner, workId: string) => Promise<unknown>
}

interface PageHelpers {
  prksCopyTextToClipboard?: (text: string) => Promise<void>
  prksConfirmDialog?: (options: { title: string; message: string; confirmLabel: string; cancelLabel?: string; danger: boolean }) => Promise<boolean>
}

export interface RecoveryPresenterConfig {
  /** What the drafts are, e.g. "Research Notes" or "Reminders". */
  subject: string
  adapter: () => ClassicRecoveryAdapter
}

export interface RecoveryPresenter {
  subject: string
  /** The pane's reactive notice view, filled from the editor adapter now. */
  view(ctx: RecoveryOwner): ShallowRef<RecoveryNoticeView | null>
  /** The adapter's state changed: repaint this pane's notice. */
  update(ctx: RecoveryOwner): void
  /** The pane's editor is gone: its notice goes, and an open Review of it detaches or closes. */
  forget(ctx?: RecoveryOwner): void
  /** Opens Review for the Work this pane shows. One Review at a time. */
  open(ctx: RecoveryOwner, opener?: HTMLElement | null): boolean
}

interface OpenReview {
  presenter: RecoveryPresenter
  host: HTMLElement
  ctx: RecoveryOwner
  tabId: string
  workId: string
  opener: HTMLElement | null
  /** The dialog's own close request, bound once it mounts. */
  requestClose: (() => void) | null
  /** The dialog's detach, bound once it mounts. */
  detach: (() => boolean) | null
  /** The editor it was opened for is gone: it never acts again. */
  detached: boolean
  refresh: (() => void) | null
}
let openReview: OpenReview | null = null

function tabKey(ctx: RecoveryOwner): string {
  return String(ctx && ctx.tabId != null ? ctx.tabId : '')
}

function liveWorkId(ctx: RecoveryOwner): string {
  const live = ctx && !ctx.destroyed && ctx.getEntity ? ctx.getEntity('work') : null
  return live && live.id != null ? String(live.id) : ''
}

const unavailable: RecoveryActionResult = { ok: false, code: 'unavailable' }

function helpers(): PageHelpers {
  return window as unknown as PageHelpers
}

/**
 * The open Review's editor is gone. A route change cannot wait for a
 * confirmation, so a Review holding edited text to keep stays open, detached,
 * with that text to copy; any other Review closes.
 */
function detachOrCloseReview(): void {
  const open = openReview
  if (!open) return
  open.detached = true
  if (!(open.detach && open.detach())) closeRecoveryReview()
}

/**
 * Closes Review without changing anything, then refreshes the pane's notice.
 * Escape passes the dialog element; only the open dialog closes.
 */
export function closeRecoveryReview(modal?: Element | null): boolean {
  const open = openReview
  if (!open) return false
  if (modal instanceof Element && !open.host.contains(modal)) return false
  openReview = null
  render(null, open.host)
  open.host.remove()
  if (!open.detached && liveWorkId(open.ctx) === open.workId) {
    if (open.refresh) open.refresh()
    if (open.opener && open.opener.isConnected) open.opener.focus()
  }
  return true
}

/**
 * Escape on the open Review: the dialog decides, so a combined text it holds
 * is confirmed before it is dropped. Returns whether that Review was open.
 */
export function requestRecoveryReviewClose(modal?: Element | null): boolean {
  const open = openReview
  if (!open) return false
  if (modal instanceof Element && !open.host.contains(modal)) return false
  if (!open.requestClose) return closeRecoveryReview(modal)
  open.requestClose()
  return true
}

export function createRecoveryPresenter(config: RecoveryPresenterConfig): RecoveryPresenter {
  const views = new Map<string, ShallowRef<RecoveryNoticeView | null>>()

  function readView(ctx: RecoveryOwner): RecoveryNoticeView | null {
    const read = config.adapter().view
    try {
      return read ? read(ctx) : null
    } catch {
      return null
    }
  }

  function actionsFor(ctx: RecoveryOwner, workId: string, review: { detached: boolean }): ReviewActions {
    const api = config.adapter()
    const page = helpers()
    // A dialog left open across a Work switch or pane change never acts.
    const current = () => !review.detached && liveWorkId(ctx) === workId
    return {
      async load() {
        if (!current() || !api.details) return null
        return api.details(ctx, workId)
      },
      async restore(details: RecoveryDetails, c: RecoveryCandidateView) {
        if (!current() || !api.restore) return unavailable
        return api.restore(ctx, workId, details.token, c.expect)
      },
      async replace(details: RecoveryDetails, c: RecoveryCandidateView, text: string, shown: RecoveryCurrentNote) {
        if (!current() || !api.replace) return unavailable
        return api.replace(ctx, workId, details.token, c.expect, text, { text: shown.text, revision: shown.revision })
      },
      async discard(details: RecoveryDetails, c: RecoveryCandidateView) {
        if (!current() || !api.discard) return unavailable
        return api.discard(ctx, workId, details.token, c.expect)
      },
      async copy(text: string) {
        if (!page.prksCopyTextToClipboard) throw new Error('Copy is unavailable')
        await page.prksCopyTextToClipboard(text)
      },
      async confirm(options) {
        return page.prksConfirmDialog ? page.prksConfirmDialog(options) : false
      },
    }
  }

  const presenter: RecoveryPresenter = {
    subject: config.subject,
    view(ctx) {
      const key = tabKey(ctx)
      let view = views.get(key)
      if (!view) {
        view = shallowRef<RecoveryNoticeView | null>(null)
        views.set(key, view)
      }
      view.value = readView(ctx)
      return view
    },
    update(ctx) {
      const view = views.get(tabKey(ctx))
      if (view) view.value = readView(ctx)
    },
    forget(ctx) {
      const mine = openReview && openReview.presenter === presenter
      if (ctx) {
        views.delete(tabKey(ctx))
        if (mine && openReview && openReview.tabId === tabKey(ctx)) detachOrCloseReview()
        return
      }
      views.clear()
      if (mine) detachOrCloseReview()
    },
    open(ctx, opener = null) {
      const workId = liveWorkId(ctx)
      if (!workId) return false
      /* One Review at a time: an open one closes under its own policy (it may hold
       * edited text and ask first), and Review is opened again once it is gone. */
      if (openReview && openReview.requestClose) {
        openReview.requestClose()
        return false
      }
      closeRecoveryReview()
      const live = ctx.getEntity ? ctx.getEntity('work') : null
      const title = live && typeof live.title === 'string' && live.title ? live.title : 'This Work'
      const host = document.createElement('div')
      host.setAttribute('data-prks-role', 'editor-recovery-review-host')
      document.body.appendChild(host)
      const review: OpenReview = {
        presenter,
        host,
        ctx,
        tabId: tabKey(ctx),
        workId,
        opener,
        requestClose: null,
        detach: null,
        detached: false,
        refresh: () => {
          const refresh = config.adapter().refresh
          if (refresh) void refresh(ctx, workId).catch(() => undefined)
        },
      }
      openReview = review
      render(
        h(EditorRecoveryReview, {
          subject: config.subject,
          entityTitle: title,
          actions: actionsFor(ctx, workId, review),
          bindClose: (request: () => void) => {
            review.requestClose = request
          },
          bindDetach: (detach: () => boolean) => {
            review.detach = detach
          },
          onClose: () => closeRecoveryReview(),
        }),
        host,
      )
      return true
    },
  }
  return presenter
}

/** Escape on any editor's Review reaches the one open dialog. */
export function registerRecoveryReviewBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVueCloseRecoveryReview?: (modal?: Element | null) => boolean
  }
  target.prksVueCloseRecoveryReview = (modal) => requestRecoveryReviewClose(modal)
}
