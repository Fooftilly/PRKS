import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import EditorRecoveryNotice from './EditorRecoveryNotice.vue'
import type { RecoveryNoticeView } from './types'

const view = (over: Partial<RecoveryNoticeView> = {}): RecoveryNoticeView => ({ workId: 'w1', drafts: 1, incomplete: 0, unprotected: null, ...over })

describe('EditorRecoveryNotice', () => {
  it('renders nothing without drafts or a warning', () => {
    expect(mount(EditorRecoveryNotice, { props: { view: null, subject: 'Research Notes' } }).html()).not.toContain('editor-recovery-notice')
  })

  it('says drafts are available, offers Review, and hides only until the Work changes', async () => {
    const wrapper = mount(EditorRecoveryNotice, { props: { view: view(), subject: 'Research Notes' } })
    expect(wrapper.text()).toContain('Unsaved Research Notes from an earlier session are available.')
    await wrapper.get('[data-prks-role="editor-recovery-open-review"]').trigger('click')
    expect(wrapper.emitted('review')).toHaveLength(1)
    await wrapper.get('[data-prks-role="editor-recovery-hide"]').trigger('click')
    expect(wrapper.find('[data-prks-role="editor-recovery-drafts"]').exists()).toBe(false)
    await wrapper.setProps({ view: view({ workId: 'w2' }) })
    expect(wrapper.find('[data-prks-role="editor-recovery-drafts"]').exists()).toBe(true)
  })

  it('distinguishes incomplete drafts', () => {
    const one = mount(EditorRecoveryNotice, { props: { view: view({ incomplete: 1 }), subject: 'Research Notes' } })
    expect(one.text()).toContain('Incomplete unsaved Research Notes')
    const some = mount(EditorRecoveryNotice, { props: { view: view({ drafts: 3, incomplete: 1 }), subject: 'Research Notes' } })
    expect(some.text()).toContain('3 unsaved Research Notes drafts from earlier sessions are available. 1 of them is incomplete.')
  })

  it('says when other changes are waiting to sync, never that anything is saved', () => {
    const queued = mount(EditorRecoveryNotice, { props: { view: view({ pendingSync: 'queued' }), subject: 'Research Notes' } })
    expect(queued.text()).toContain('Other changes to this note are waiting to sync.')
    const unknown = mount(EditorRecoveryNotice, { props: { view: view({ pendingSync: 'unknown' }), subject: 'Research Notes' } })
    expect(unknown.text()).toContain('Changes waiting to sync could not be checked.')
    const none = mount(EditorRecoveryNotice, { props: { view: view({ pendingSync: 'none' }), subject: 'Research Notes' } })
    expect(none.text()).not.toContain('sync')
    for (const w of [queued, unknown, none]) expect(w.text()).not.toMatch(/\bsaved\b/i)
  })

  it('keeps the unprotected warning visible; it cannot be hidden', async () => {
    const wrapper = mount(EditorRecoveryNotice, { props: { view: view({ unprotected: 'quota' }), subject: 'Research Notes' } })
    const warning = wrapper.get('[data-prks-role="editor-recovery-unprotected"]')
    expect(warning.classes()).toContain('prks-inline-message--warning')
    expect(warning.attributes('role')).toBe('status')
    expect(warning.text()).toBe('Not protected if the browser closes: recovery storage on this device is full. Keep this tab open until the note saves.')
    await wrapper.get('[data-prks-role="editor-recovery-hide"]').trigger('click')
    expect(wrapper.find('[data-prks-role="editor-recovery-unprotected"]').exists()).toBe(true)
    // Never "saved": that word belongs to what the server acknowledged.
    expect(wrapper.text()).not.toMatch(/\bsaved\b/i)
  })
})
