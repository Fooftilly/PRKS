import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  closeResearchNotesRecoveryReview,
  forgetResearchNotesRecovery,
  openResearchNotesRecoveryReview,
  registerResearchNotesRecoveryBridge,
  researchNotesRecoveryView,
  updateResearchNotesRecovery,
} from './research-notes-recovery'

const w = window as unknown as Record<string, unknown>

function owner(workId = 'w1') {
  let work: { id: string; title: string } | null = { id: workId, title: 'A Work' }
  return {
    tabId: 'tab-1',
    destroyed: false,
    getEntity: () => work,
    switchTo(id: string | null) {
      work = id ? { id, title: 'Other' } : null
    },
  }
}

afterEach(() => {
  forgetResearchNotesRecovery()
  for (const key of ['prksResearchNotesRecoveryView', 'prksResearchNotesRecoveryDetails', 'prksRefreshResearchNotesRecovery', 'prksResearchNotesRecoveryRestore', 'prksConfirmDialog', 'prksVueCloseResearchNotesRecoveryReview', 'prksVueUpdateResearchNotesRecovery']) delete w[key]
  document.body.innerHTML = ''
})

describe('Research Notes recovery presenter', () => {
  it('keeps one reactive notice view per pane, repainted from works.js', () => {
    let drafts = 1
    w.prksResearchNotesRecoveryView = () => ({ workId: 'w1', drafts, incomplete: 0, unprotected: null })
    const ctx = owner()
    const view = researchNotesRecoveryView(ctx)
    expect(view.value).toMatchObject({ drafts: 1 })
    drafts = 2
    updateResearchNotesRecovery(ctx)
    expect(view.value).toMatchObject({ drafts: 2 })
  })

  it('opens one Review, closes it without acting, refreshes the notice and returns focus', async () => {
    const refresh = vi.fn(async () => null)
    w.prksRefreshResearchNotesRecovery = refresh
    w.prksResearchNotesRecoveryDetails = vi.fn(async () => ({ workId: 'w1', token: 't', current: { text: '', revision: 5, source: 'server', queue: 'none', queued: 0, unsaved: false }, candidates: [] }))
    const ctx = owner()
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    expect(openResearchNotesRecoveryReview(ctx, opener)).toBe(true)
    await flushPromises()
    const modal = document.getElementById('editor-recovery-review-modal')
    expect(modal).toBeTruthy()
    // Escape passes another dialog: not this one.
    expect(closeResearchNotesRecoveryReview(document.createElement('div'))).toBe(false)
    expect(closeResearchNotesRecoveryReview(modal)).toBe(true)
    expect(document.getElementById('editor-recovery-review-modal')).toBeNull()
    expect(refresh).toHaveBeenCalledWith(ctx, 'w1')
    expect(document.activeElement).toBe(opener)
  })

  it('Escape asks the dialog, which confirms before dropping an edited text to keep', async () => {
    const confirm = vi.fn(async () => false)
    w.prksConfirmDialog = confirm
    w.prksResearchNotesRecoveryDetails = vi.fn(async () => ({
      workId: 'w1',
      token: 't',
      current: { text: 'x', revision: 5, source: 'server', queue: 'none', queued: 0, unsaved: false },
      candidates: [{ expect: { draftId: 'd', pageInstanceId: 'p', generation: 1, status: 'active' }, draftId: 'd', lineage: 'dead-runtime', samePane: false, status: 'active', reason: 'base-advanced', action: 'reconcile', generation: 1, updatedAt: 1, length: 1, body: 'y', typedOnRevision: 4, pipelineState: 'drafting' }],
    }))
    registerResearchNotesRecoveryBridge(window)
    const escape = w.prksVueCloseResearchNotesRecoveryReview as (modal?: Element | null) => boolean
    openResearchNotesRecoveryReview(owner())
    await flushPromises()
    ;(document.querySelector('[data-prks-role="editor-recovery-compare-btn"]') as HTMLButtonElement).click()
    await flushPromises()
    const text = document.querySelector('[data-prks-role="editor-recovery-chosen-text"]') as HTMLTextAreaElement
    text.value = 'y and x'
    text.dispatchEvent(new Event('input'))
    const modal = document.getElementById('editor-recovery-review-modal')
    expect(escape(modal)).toBe(true)
    await flushPromises()
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(document.getElementById('editor-recovery-review-modal')).toBeTruthy()
    confirm.mockResolvedValue(true)
    expect(escape(modal)).toBe(true)
    await flushPromises()
    expect(document.getElementById('editor-recovery-review-modal')).toBeNull()
  })

  it('opening Review again asks the open one to close under its own policy', async () => {
    const confirm = vi.fn(async () => false)
    w.prksConfirmDialog = confirm
    w.prksResearchNotesRecoveryDetails = vi.fn(async () => ({
      workId: 'w1',
      token: 't',
      current: { text: 'x', revision: 5, source: 'server', queue: 'none', queued: 0, unsaved: false },
      candidates: [{ expect: { draftId: 'd', pageInstanceId: 'p', generation: 1, status: 'active' }, draftId: 'd', lineage: 'dead-runtime', samePane: false, status: 'active', reason: 'base-advanced', action: 'reconcile', generation: 1, updatedAt: 1, length: 1, body: 'y', typedOnRevision: 4, pipelineState: 'drafting' }],
    }))
    const ctx = owner()
    openResearchNotesRecoveryReview(ctx)
    await flushPromises()
    ;(document.querySelector('[data-prks-role="editor-recovery-compare-btn"]') as HTMLButtonElement).click()
    await flushPromises()
    const text = document.querySelector('[data-prks-role="editor-recovery-chosen-text"]') as HTMLTextAreaElement
    text.value = 'y and x'
    text.dispatchEvent(new Event('input'))
    expect(openResearchNotesRecoveryReview(ctx)).toBe(false)
    await flushPromises()
    expect(confirm).toHaveBeenCalledTimes(1)
    expect((document.querySelector('[data-prks-role="editor-recovery-chosen-text"]') as HTMLTextAreaElement).value).toBe('y and x')
  })

  it('a dialog left open across a Work switch never acts, and the pane going away closes it', async () => {
    const restore = vi.fn(async () => ({ ok: true }))
    w.prksResearchNotesRecoveryRestore = restore
    const details = vi.fn(async () => ({
      workId: 'w1',
      token: 't',
      current: { text: 'x', revision: 5, source: 'server', queue: 'none', queued: 0, unsaved: false },
      candidates: [{ expect: { draftId: 'd', pageInstanceId: 'p', generation: 1, status: 'active' }, draftId: 'd', lineage: 'dead-runtime', samePane: false, status: 'active', reason: 'multiple-drafts', action: 'restore', generation: 1, updatedAt: 1, length: 1, body: 'y', typedOnRevision: 5, pipelineState: 'drafting' }],
    }))
    w.prksResearchNotesRecoveryDetails = details
    const ctx = owner()
    openResearchNotesRecoveryReview(ctx)
    await flushPromises()
    ctx.switchTo('w2')
    ;(document.querySelector('[data-prks-role="editor-recovery-restore"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(restore).not.toHaveBeenCalled()
    forgetResearchNotesRecovery({ tabId: 'tab-1' })
    expect(document.getElementById('editor-recovery-review-modal')).toBeNull()
  })
})
