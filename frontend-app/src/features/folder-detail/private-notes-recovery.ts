/**
 * Folder Reminders recovery presentation (#534): the notice in the classic
 * Reminders card of the Folder right panel and the shared Review dialog.
 * `ui.js` stays the editor authority (see `recovery-presenter.ts`); its Folder
 * kind runs on the same adapter as Work Reminders, with the Folder field
 * revision as its base.
 */
import { h, render, type ShallowRef } from 'vue'
import {
  createRecoveryPresenter,
  type ClassicRecoveryAdapter,
  type RecoveryOwner,
} from '../editor-recovery/recovery-presenter'
import type { RecoveryNoticeView } from '../editor-recovery/types'
import FolderPrivateNotesRecovery from './FolderPrivateNotesRecovery.vue'

interface ClassicFolderPrivateRecovery {
  prksFolderPrivateNotesRecoveryView?: ClassicRecoveryAdapter['view']
  prksFolderPrivateNotesRecoveryDetails?: ClassicRecoveryAdapter['details']
  prksFolderPrivateNotesRecoveryRestore?: ClassicRecoveryAdapter['restore']
  prksFolderPrivateNotesRecoveryReplace?: ClassicRecoveryAdapter['replace']
  prksFolderPrivateNotesRecoveryDiscard?: ClassicRecoveryAdapter['discard']
  prksRefreshFolderPrivateNotesRecovery?: ClassicRecoveryAdapter['refresh']
}

const presenter = createRecoveryPresenter({
  subject: 'Reminders',
  entityType: 'folder',
  entityLabel: 'Folder',
  adapter: () => {
    const w = window as unknown as ClassicFolderPrivateRecovery
    return {
      view: w.prksFolderPrivateNotesRecoveryView,
      details: w.prksFolderPrivateNotesRecoveryDetails,
      restore: w.prksFolderPrivateNotesRecoveryRestore,
      replace: w.prksFolderPrivateNotesRecoveryReplace,
      discard: w.prksFolderPrivateNotesRecoveryDiscard,
      refresh: w.prksRefreshFolderPrivateNotesRecovery,
    }
  },
})

interface MountedNotice {
  host: HTMLElement
  tabId: string
}

/* The one mounted notice: the Folder right panel shows one Reminders card. */
let mounted: MountedNotice | null = null

export function folderPrivateNotesRecoveryView(ctx: RecoveryOwner): ShallowRef<RecoveryNoticeView | null> {
  return presenter.view(ctx)
}

export function openFolderPrivateNotesRecoveryReview(ctx: RecoveryOwner, opener: HTMLElement | null = null): boolean {
  return presenter.open(ctx, opener)
}

export function forgetFolderPrivateNotesRecovery(): void {
  const was = mounted
  mounted = null
  if (!was) return
  // Review belongs to the Reminders field it was opened for.
  presenter.forget({ tabId: was.tabId })
  render(null, was.host)
}

function hostFor(folderId: string): HTMLElement | null {
  const panel = document.getElementById('panel-content')
  if (!panel) return null
  const hosts = panel.querySelectorAll('[data-prks-role="private-notes-recovery-host"]')
  for (const host of Array.from(hosts)) {
    if (host instanceof HTMLElement && host.dataset.prksNotesId === folderId) return host
  }
  return null
}

/** Mounts the notice into the bound Folder Reminders card of this pane. */
export function presentFolderPrivateNotesRecovery(ctx: RecoveryOwner, folderId: string): boolean {
  if (!ctx || ctx.destroyed) return false
  const live = ctx.getEntity ? ctx.getEntity('folder') : null
  if (!live || String(live.id ?? '') !== String(folderId)) return false
  const host = hostFor(String(folderId))
  if (!host) return false
  const tabId = String(ctx.tabId ?? '')
  if (mounted && (mounted.host !== host || mounted.tabId !== tabId)) forgetFolderPrivateNotesRecovery()
  render(h(FolderPrivateNotesRecovery, { owner: ctx }), host)
  mounted = { host, tabId }
  return true
}

/** The adapter's state changed: repaint the pane's notice, or let go of a card the panel replaced. */
export function updateFolderPrivateNotesRecovery(ctx: RecoveryOwner): void {
  if (mounted && !mounted.host.isConnected) forgetFolderPrivateNotesRecovery()
  presenter.update(ctx)
}

export function registerFolderPrivateNotesRecoveryBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVuePresentFolderPrivateNotesRecovery?: (ctx: RecoveryOwner, folderId: string) => boolean
    prksVueUpdateFolderPrivateNotesRecovery?: (ctx: RecoveryOwner) => void
    prksVueForgetFolderPrivateNotesRecovery?: () => void
  }
  target.prksVuePresentFolderPrivateNotesRecovery = (ctx, folderId) => presentFolderPrivateNotesRecovery(ctx, folderId)
  target.prksVueUpdateFolderPrivateNotesRecovery = (ctx) => updateFolderPrivateNotesRecovery(ctx)
  target.prksVueForgetFolderPrivateNotesRecovery = () => forgetFolderPrivateNotesRecovery()
}

export function resetFolderPrivateNotesRecoveryForTests(): void {
  forgetFolderPrivateNotesRecovery()
  presenter.forget()
}
