import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  registerFolderPrivateNotesRecoveryBridge,
  resetFolderPrivateNotesRecoveryForTests,
} from './private-notes-recovery'
import { closeRecoveryReview } from '../editor-recovery/recovery-presenter'

const w = window as unknown as Record<string, unknown>

function owner(folderId = 'F-A') {
  let folder: { id: string; title: string } | null = { id: folderId, title: 'Synthetic Folder' }
  return {
    tabId: 'tab-1',
    destroyed: false,
    getEntity: (type: string) => (type === 'folder' ? folder : null),
    switchTo(id: string | null) {
      folder = id ? { id, title: 'Other' } : null
    },
  }
}

function card(folderId = 'F-A') {
  document.body.innerHTML = `<div id="panel-content"><div class="prks-private-notes-card">
    <div data-prks-role="private-notes-recovery-host" data-prks-notes-id="${folderId}"></div>
    <textarea id="prks-private-notes-folder-${folderId}"></textarea></div></div>`
  return document.querySelector('[data-prks-role="private-notes-recovery-host"]') as HTMLElement
}

const bridge = () => w as unknown as {
  prksVuePresentFolderPrivateNotesRecovery: (ctx: unknown, id: string) => boolean
  prksVueUpdateFolderPrivateNotesRecovery: (ctx: unknown) => void
  prksVueForgetFolderPrivateNotesRecovery: () => void
}

afterEach(() => {
  resetFolderPrivateNotesRecoveryForTests()
  closeRecoveryReview()
  for (const key of ['prksFolderPrivateNotesRecoveryView', 'prksFolderPrivateNotesRecoveryDetails', 'prksRefreshFolderPrivateNotesRecovery']) delete w[key]
  document.body.innerHTML = ''
})

describe('Folder Reminders recovery presenter', () => {
  it('mounts the notice into the Folder card and repaints it from ui.js', async () => {
    let view: unknown = { workId: 'F-A', drafts: 1, incomplete: 0, unprotected: null }
    w.prksFolderPrivateNotesRecoveryView = () => view
    registerFolderPrivateNotesRecoveryBridge(window)
    const host = card()
    const ctx = owner()
    expect(bridge().prksVuePresentFolderPrivateNotesRecovery(ctx, 'F-A')).toBe(true)
    await flushPromises()
    expect(host.textContent).toContain('Unsaved Reminders from an earlier session are available.')
    view = { workId: 'F-A', drafts: 0, incomplete: 0, unprotected: 'quota' }
    bridge().prksVueUpdateFolderPrivateNotesRecovery(ctx)
    await flushPromises()
    expect(host.querySelector('[data-prks-role="editor-recovery-drafts"]')).toBeNull()
    expect(host.textContent).toContain('Not protected if the browser closes')
  })

  it('does not mount for a Folder the pane no longer shows, and unmounts on dismiss', async () => {
    w.prksFolderPrivateNotesRecoveryView = () => ({ workId: 'F-A', drafts: 1, incomplete: 0, unprotected: null })
    registerFolderPrivateNotesRecoveryBridge(window)
    const host = card()
    const ctx = owner()
    ctx.switchTo('F-B')
    expect(bridge().prksVuePresentFolderPrivateNotesRecovery(ctx, 'F-A')).toBe(false)
    ctx.switchTo('F-A')
    expect(bridge().prksVuePresentFolderPrivateNotesRecovery(ctx, 'F-A')).toBe(true)
    await flushPromises()
    expect(host.querySelector('[data-prks-role="editor-recovery-notice"]')).toBeTruthy()
    bridge().prksVueForgetFolderPrivateNotesRecovery()
    expect(host.querySelector('[data-prks-role="editor-recovery-notice"]')).toBeNull()
  })

  it('opens Review for the Folder with its title, and names a Folder in the dirty-session reason', async () => {
    w.prksFolderPrivateNotesRecoveryView = () => ({ workId: 'F-A', drafts: 1, incomplete: 0, unprotected: null })
    const details = vi.fn(async () => ({
      workId: 'F-A',
      token: 't',
      current: { text: 'x', revision: 5, source: 'server', queue: 'none', queued: 0, unsaved: false },
      candidates: [{ expect: { draftId: 'd', pageInstanceId: 'p', generation: 1, status: 'active' }, draftId: 'd', lineage: 'dead-runtime', samePane: false, status: 'active', reason: 'dirty-session', action: null, generation: 1, updatedAt: 1, length: 1, body: 'y', typedOnRevision: 5, pipelineState: 'drafting' }],
    }))
    w.prksFolderPrivateNotesRecoveryDetails = details
    registerFolderPrivateNotesRecoveryBridge(window)
    card()
    const ctx = owner()
    bridge().prksVuePresentFolderPrivateNotesRecovery(ctx, 'F-A')
    await flushPromises()
    ;(document.querySelector('[data-prks-role="editor-recovery-open-review"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(details).toHaveBeenCalledWith(ctx, 'F-A')
    expect(document.querySelector('[data-prks-role="editor-recovery-entity"]')!.textContent).toBe('Synthetic Folder')
    expect(document.querySelector('[data-prks-role="editor-recovery-reason"]')!.textContent)
      .toBe('This Folder has unsaved changes in another pane. Finish there first.')
  })
})
