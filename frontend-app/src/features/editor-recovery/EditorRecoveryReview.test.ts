import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import EditorRecoveryReview from './EditorRecoveryReview.vue'
import type { RecoveryActionResult, RecoveryCandidateView, RecoveryDetails, ReviewActions } from './types'

function candidate(over: Partial<RecoveryCandidateView> = {}): RecoveryCandidateView {
  const draftId = over.draftId ?? 'd-1'
  return {
    expect: { draftId, pageInstanceId: 'p-old', generation: 4, status: 'active' },
    draftId,
    lineage: 'dead-runtime',
    samePane: false,
    status: 'active',
    reason: 'multiple-drafts',
    action: 'restore',
    generation: 4,
    updatedAt: Date.UTC(2026, 9, 8, 14, 2),
    length: 15,
    body: 'Recovered text.',
    typedOnRevision: 5,
    pipelineState: 'drafting',
    ...over,
  }
}

function details(candidates: RecoveryCandidateView[]): RecoveryDetails {
  return {
    workId: 'w1',
    token: 'review-1',
    current: { text: 'Current note.', revision: 6, source: 'server', queue: 'none', queued: 0, unsaved: false },
    candidates,
  }
}

function actions(over: Partial<ReviewActions> = {}, loads: RecoveryDetails[] = []): ReviewActions & { calls: string[] } {
  const calls: string[] = []
  let n = 0
  const ok: RecoveryActionResult = { ok: true }
  return {
    calls,
    load: vi.fn(async () => loads[Math.min(n++, loads.length - 1)] ?? null),
    restore: vi.fn(async () => (calls.push('restore'), ok)),
    replace: vi.fn(async () => (calls.push('replace'), ok)),
    discard: vi.fn(async () => (calls.push('discard'), ok)),
    copy: vi.fn(async () => undefined),
    confirm: vi.fn(async () => true),
    ...over,
  }
}

async function open(a: ReviewActions) {
  const wrapper = mount(EditorRecoveryReview, { props: { subject: 'Research Notes', entityTitle: 'A Work', actions: a }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

describe('EditorRecoveryReview', () => {
  it('lists every draft with its facts and selects none for applying', async () => {
    const a = actions({}, [details([candidate(), candidate({ draftId: 'd-2', body: 'Other text.', lineage: 'same-runtime-orphan', samePane: true })])])
    const wrapper = await open(a)
    expect(wrapper.findAll('[data-prks-role="editor-recovery-candidate"]')).toHaveLength(2)
    expect(wrapper.get('[data-prks-role="editor-recovery-entity"]').text()).toBe('A Work')
    expect(wrapper.get('[data-prks-role="editor-recovery-current"]').text()).toBe('revision 6, checked with the server')
    expect(wrapper.get('[data-prks-role="editor-recovery-origin"]').text()).toBe('A browser tab that was closed')
    expect((wrapper.get('[data-prks-role="editor-recovery-text"]').element as HTMLTextAreaElement).value).toBe('Recovered text.')
    expect(a.calls).toEqual([])
    await wrapper.findAll('input[type="radio"]')[1]!.setValue(true)
    expect(wrapper.get('[data-prks-role="editor-recovery-origin"]').text()).toBe('This pane, before the page reloaded')
    // No misleading Save.
    expect(wrapper.text()).not.toMatch(/\bSave\b/)
    wrapper.unmount()
  })

  it('restores the selected draft and closes', async () => {
    const a = actions({}, [details([candidate()])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-restore"]').trigger('click')
    await flushPromises()
    expect(a.calls).toEqual(['restore'])
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('compares before replacing, and writes nothing unless confirmed', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const confirm = vi.fn(async () => false)
    const a = actions({ confirm }, [details([draft])])
    const wrapper = await open(a)
    expect(wrapper.find('[data-prks-role="editor-recovery-restore"]').exists()).toBe(false)
    expect(wrapper.get('[data-prks-role="editor-recovery-reason"]').text()).toBe('The note changed after this text was typed on revision 5.')
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    expect((wrapper.get('[data-prks-role="editor-recovery-current-text"]').element as HTMLTextAreaElement).value).toBe('Current note.')
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Current note. Recovered text.')
    await wrapper.get('[data-prks-role="editor-recovery-replace"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(a.calls).toEqual([])
    confirm.mockResolvedValue(true)
    await wrapper.get('[data-prks-role="editor-recovery-replace"]').trigger('click')
    await flushPromises()
    expect(a.replace).toHaveBeenCalledWith(expect.anything(), draft, 'Current note. Recovered text.', expect.objectContaining({ text: 'Current note.', revision: 6 }))
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('reloads and explains when an action is refused because the draft changed', async () => {
    const first = details([candidate()])
    const second = details([candidate({ lineage: 'other-live', action: null, body: null })])
    const a = actions({ restore: vi.fn(async () => ({ ok: false, code: 'changed' }) as RecoveryActionResult) }, [first, second])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-restore"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-prks-role="editor-recovery-message"]').text()).toBe('This draft changed in another tab or pane. The list was refreshed.')
    expect(wrapper.find('[data-prks-role="editor-recovery-restore"]').exists()).toBe(false)
    expect(wrapper.find('[data-prks-role="editor-recovery-discard"]').exists()).toBe(false)
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('shows an incomplete draft for copy and discard only, and discards after confirmation', async () => {
    const draft = candidate({ status: 'tail-missing', reason: 'tail-missing', action: null })
    const a = actions({}, [details([draft]), details([])])
    const wrapper = await open(a)
    expect(wrapper.get('[data-prks-role="editor-recovery-completeness"]').text()).toBe('Incomplete: the newest changes were not captured')
    expect(wrapper.find('[data-prks-role="editor-recovery-restore"]').exists()).toBe(false)
    expect(wrapper.find('[data-prks-role="editor-recovery-compare-btn"]').exists()).toBe(false)
    await wrapper.get('[data-prks-role="editor-recovery-copy"]').trigger('click')
    await flushPromises()
    expect(a.copy).toHaveBeenCalledWith('Recovered text.')
    expect(wrapper.get('[data-prks-role="editor-recovery-copy"]').text()).toBe('Copied')
    await wrapper.get('[data-prks-role="editor-recovery-discard"]').trigger('click')
    await flushPromises()
    expect(a.confirm).toHaveBeenCalledWith(expect.objectContaining({ confirmLabel: 'Discard draft', danger: true }))
    expect(a.calls).toEqual(['discard'])
    expect(wrapper.get('[data-prks-role="editor-recovery-empty"]').text()).toBe('No unsaved drafts are left for this note.')
    wrapper.unmount()
  })

  it('confirms before Close, Back or the backdrop drop an edited text to keep', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const confirm = vi.fn(async () => false)
    const a = actions({ confirm }, [details([draft])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    // Unedited: nothing to lose, so Back asks nothing.
    await wrapper.get('[data-prks-role="editor-recovery-back"]').trigger('click')
    await flushPromises()
    expect(confirm).not.toHaveBeenCalled()
    expect(wrapper.find('[data-prks-role="editor-recovery-compare"]').exists()).toBe(false)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Combined.')
    for (const role of ['editor-recovery-cancel', 'editor-recovery-close', 'editor-recovery-review-backdrop', 'editor-recovery-back']) {
      await wrapper.get(`[data-prks-role="${role}"]`).trigger('click')
      await flushPromises()
    }
    expect(confirm).toHaveBeenCalledTimes(4)
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ confirmLabel: 'Discard text', cancelLabel: 'Keep editing', danger: true }))
    expect(wrapper.emitted('close')).toBeUndefined()
    expect((wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').element as HTMLTextAreaElement).value).toBe('Combined.')
    confirm.mockResolvedValue(true)
    await wrapper.get('[data-prks-role="editor-recovery-cancel"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(a.calls).toEqual([])
    wrapper.unmount()
  })

  it('keeps the combined text when Replace is refused and the draft can still be compared', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const replace = vi.fn(async () => ({ ok: false, code: 'current-changed' }) as RecoveryActionResult)
    const a = actions({ replace }, [details([draft]), details([draft])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Combined.')
    await wrapper.get('[data-prks-role="editor-recovery-replace"]').trigger('click')
    await flushPromises()
    expect(replace).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-prks-role="editor-recovery-message"]').exists()).toBe(true)
    expect((wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').element as HTMLTextAreaElement).value).toBe('Combined.')
    wrapper.unmount()
  })

  it('keeps edited text copyable when a refused Replace ends the comparison', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const gone = candidate({ action: null, lineage: 'other-live', body: null })
    const replace = vi.fn(async () => ({ ok: false, code: 'changed' }) as RecoveryActionResult)
    const confirm = vi.fn(async () => true)
    const a = actions({ replace, confirm }, [details([draft]), details([gone])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Combined.')
    await wrapper.get('[data-prks-role="editor-recovery-replace"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-prks-role="editor-recovery-compare"]').exists()).toBe(false)
    await wrapper.get('[data-prks-role="editor-recovery-copy-leftover"]').trigger('click')
    await flushPromises()
    expect(a.copy).toHaveBeenCalledWith('Combined.')
    // Still the only copy: closing asks first.
    confirm.mockClear()
    confirm.mockResolvedValue(false)
    await wrapper.get('[data-prks-role="editor-recovery-cancel"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ confirmLabel: 'Discard text' }))
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('names the edited text when discarding the draft from Compare', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const a = actions({}, [details([draft]), details([])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Combined.')
    await wrapper.get('[data-prks-role="editor-recovery-discard"]').trigger('click')
    await flushPromises()
    expect(a.confirm).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('your edited text to keep') }))
    wrapper.unmount()
  })

  it('keeps edited text copyable when the refresh after a refused Replace cannot be read', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const replace = vi.fn(async () => ({ ok: false, code: 'changed' }) as RecoveryActionResult)
    let n = 0
    const confirm = vi.fn(async () => true)
    const a = actions({ replace, confirm, load: vi.fn(async () => (n++ === 0 ? details([draft]) : null)) })
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Combined, exactly.')
    await wrapper.get('[data-prks-role="editor-recovery-replace"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-prks-role="editor-recovery-copy-leftover"]').trigger('click')
    await flushPromises()
    expect(a.copy).toHaveBeenCalledWith('Combined, exactly.')
    confirm.mockResolvedValue(false)
    await wrapper.get('[data-prks-role="editor-recovery-cancel"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenLastCalledWith(expect.objectContaining({ confirmLabel: 'Discard text' }))
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('a confirmed Discard from Compare drops the edited text; a refused one keeps it', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const other = candidate({ draftId: 'd-2', action: 'reconcile', reason: 'base-advanced' })
    const discardResults: RecoveryActionResult[] = [{ ok: false, code: 'changed' }, { ok: true }]
    const discard = vi.fn(async () => discardResults.shift()!)
    const a = actions({ discard }, [details([draft, other]), details([draft, other]), details([other])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Combined.')
    await wrapper.get('[data-prks-role="editor-recovery-discard"]').trigger('click')
    await flushPromises()
    // Refused: nothing was discarded, the edited text stays copyable.
    expect(wrapper.find('[data-prks-role="editor-recovery-leftover"]').exists()).toBe(true)
    await wrapper.get('[data-prks-role="editor-recovery-discard"]').trigger('click')
    await flushPromises()
    expect(discard).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-prks-role="editor-recovery-leftover"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('asks before Restore drops edited text left from a comparison', async () => {
    const draft = candidate({ action: 'reconcile', reason: 'base-advanced' })
    const restorable = candidate({ draftId: 'd-2', action: 'restore' })
    const gone = candidate({ action: null, lineage: 'other-live', body: null })
    const replace = vi.fn(async () => ({ ok: false, code: 'changed' }) as RecoveryActionResult)
    const confirm = vi.fn(async () => true)
    const a = actions({ replace, confirm }, [details([draft, restorable]), details([gone, restorable])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-compare-btn"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-prks-role="editor-recovery-chosen-text"]').setValue('Combined.')
    await wrapper.get('[data-prks-role="editor-recovery-replace"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-prks-role="editor-recovery-leftover"]').exists()).toBe(true)
    confirm.mockClear()
    confirm.mockResolvedValue(false)
    await wrapper.get('[data-draft-id="d-2"] input').setValue(true)
    await wrapper.get('[data-prks-role="editor-recovery-restore"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ confirmLabel: 'Discard text' }))
    expect(a.restore).not.toHaveBeenCalled()
    expect(wrapper.find('[data-prks-role="editor-recovery-leftover"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('warns before discarding a draft whose tab may still be open', async () => {
    const a = actions({}, [details([candidate({ lineage: 'unknown', reason: 'ownership-unknown', action: null })]), details([])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-discard"]').trigger('click')
    await flushPromises()
    expect(a.confirm).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('discard it only if that tab is gone') }))
    wrapper.unmount()
  })

  it('Close changes nothing', async () => {
    const a = actions({}, [details([candidate()])])
    const wrapper = await open(a)
    await wrapper.get('[data-prks-role="editor-recovery-cancel"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(a.calls).toEqual([])
    wrapper.unmount()
  })
})
