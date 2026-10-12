import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DiagnosticsDraft, RecoveryDiagnostics, RecoveryInventory } from '../../lifecycle/editor-recovery/diagnostics'
import { activatePerformanceDiagnostics, resetPerformanceDiagnosticsActivationForTests } from '../performance-diagnostics/activation'
import EditorRecoveryDiagnostics from './EditorRecoveryDiagnostics.vue'
import { downloadName } from './diagnostics-labels'

function draft(over: Partial<DiagnosticsDraft> = {}): DiagnosticsDraft {
  const draftId = over.draftId ?? 'd-1'
  return {
    source: 'record',
    draftId,
    kind: 'work-research-note',
    entityType: 'work',
    entityId: 'w1',
    status: 'active',
    lineage: 'dead-runtime',
    generation: 3,
    bodyLength: 12,
    createdAt: Date.UTC(2026, 9, 1),
    updatedAt: Date.UTC(2026, 9, 2),
    baseRevision: 4,
    baseSource: 'server',
    pipelineState: 'drafting',
    old: false,
    superseded: false,
    deletedHere: false,
    fromEmergency: false,
    expect: { draftId, pageInstanceId: 'p-old', generation: 3, status: 'active' },
    ...over,
  }
}

function inventory(over: Partial<RecoveryInventory> = {}): RecoveryInventory {
  return {
    storage: 'ok',
    emergencyStorage: 'ok',
    drafts: [],
    unreadable: [],
    emergency: [],
    unreadableEmergency: [],
    totalChars: 0,
    approxBytes: 0,
    sizeIncomplete: false,
    overBudget: false,
    ...over,
  }
}

type Win = Window & {
  prksEditorRecovery?: { diagnostics: () => RecoveryDiagnostics }
  prksCopyTextToClipboard?: (text: string) => Promise<void>
  prksConfirmDialog?: (options: { message: string; danger: boolean }) => Promise<boolean>
}
const win = window as Win

function install(loads: RecoveryInventory[], over: Partial<RecoveryDiagnostics> = {}) {
  let n = 0
  const api: RecoveryDiagnostics = {
    inventory: vi.fn(async () => loads[Math.min(n++, loads.length - 1)]!),
    readText: vi.fn(async () => ({ ok: true as const, text: 'Recovered text' })),
    discard: vi.fn(async () => 'deleted' as const),
    ...over,
  }
  win.prksEditorRecovery = { diagnostics: () => api }
  win.prksCopyTextToClipboard = vi.fn(async () => undefined)
  win.prksConfirmDialog = vi.fn(async () => true)
  return api
}

async function open() {
  const wrapper = mount(EditorRecoveryDiagnostics, { attachTo: document.body })
  activatePerformanceDiagnostics()
  await flushPromises()
  return wrapper
}

describe('EditorRecoveryDiagnostics', () => {
  beforeEach(() => resetPerformanceDiagnosticsActivationForTests())
  afterEach(() => {
    delete win.prksEditorRecovery
    delete win.prksCopyTextToClipboard
    delete win.prksConfirmDialog
    document.body.innerHTML = ''
  })

  it('reads nothing until Diagnostics opens, then lists every source with its identity', async () => {
    const api = install([
      inventory({
        drafts: [draft(), draft({ draftId: 'd-2', kind: 'folder-private-note', entityType: 'folder', entityId: 'f1', lineage: 'unknown', old: true, deletedHere: false })],
        unreadable: [{ source: 'unreadable-record', draftId: 'd-new', reason: 'newer-version', kind: 'work-private-note', entityType: 'work', entityId: 'w9', updatedAt: 1, bodyLength: null, snapshot: '{}' }],
        emergency: [{ source: 'emergency', key: 'k1', pageInstanceId: 'p-1', draftId: 'd-e', kind: 'work-private-note', entityType: 'work', entityId: 'w2', generation: 2, bodyLength: 5, at: 1, lineage: 'unknown' }],
        unreadableEmergency: [
          { source: 'unreadable-emergency', key: 'k2', pageInstanceId: 'p-2', reason: 'malformed', rawLength: 9, raw: '{"broken"' },
          { source: 'unreadable-emergency', key: 'k3', pageInstanceId: 'p-3', reason: 'read-failed', rawLength: null, raw: null },
        ],
        totalChars: 29,
        approxBytes: 58,
        sizeIncomplete: true,
      }),
    ])
    const wrapper = mount(EditorRecoveryDiagnostics, { attachTo: document.body })
    await flushPromises()
    expect(api.inventory).not.toHaveBeenCalled()
    activatePerformanceDiagnostics()
    await flushPromises()
    expect(api.inventory).toHaveBeenCalledTimes(1)
    const rows = wrapper.findAll('[data-prks-role="editor-recovery-diag-row"]')
    expect(rows.map((r) => r.attributes('data-source'))).toEqual(['record', 'record', 'emergency', 'unreadable-record', 'unreadable-emergency', 'unreadable-emergency'])
    expect(rows[0]!.text()).toContain('Research Notes')
    expect(rows[0]!.find('a').attributes('href')).toBe('#/works/w1')
    expect(rows[0]!.text()).toContain('A browser tab that was closed')
    expect(rows[1]!.find('a').attributes('href')).toBe('#/folders/f1')
    expect(rows[1]!.text()).toContain('Another tab that may still be open')
    expect(rows[1]!.text()).toContain('Not changed for over 30 days')
    expect(rows[3]!.text()).toContain('newer version of PRKS')
    // Emergency and unreadable entries are copy-only: never a discard.
    expect(rows[2]!.find('[data-prks-role="editor-recovery-diag-discard"]').exists()).toBe(false)
    expect(rows[3]!.find('[data-prks-role="editor-recovery-diag-discard"]').exists()).toBe(false)
    expect(rows[4]!.find('[data-prks-role="editor-recovery-diag-copy"]').text()).toBe('Copy raw data')
    // A key this browser could not read offers no text to read: Refresh tries again.
    expect(rows[5]!.find('[data-prks-role="editor-recovery-diag-view"]').exists()).toBe(false)
    expect(rows[5]!.find('[data-prks-role="editor-recovery-diag-copy"]').exists()).toBe(false)
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-summary"]').text()).toContain('6 editor drafts kept in this browser, at least about 58 B')
  })

  it('closes Settings when a draft\'s Work or Folder link is followed, unless Settings keeps itself open', async () => {
    install([inventory({ drafts: [draft()] })])
    const wrapper = await open()
    const close = vi.fn(() => true)
    Object.assign(win, { requestModalClose: close })
    const link = wrapper.find('[data-prks-role="editor-recovery-diag-row"] a')
    const followed = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })
    link.element.dispatchEvent(followed)
    expect(close).toHaveBeenCalledWith('navigate')
    expect(followed.defaultPrevented).toBe(false)
    close.mockReturnValue(false)
    const held = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })
    link.element.dispatchEvent(held)
    expect(held.defaultPrevented).toBe(true)
    // A new-tab click leaves Settings as it is.
    close.mockClear()
    link.element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ctrlKey: true }))
    expect(close).not.toHaveBeenCalled()
    Reflect.deleteProperty(win, 'requestModalClose')
  })

  it('shows, copies and downloads the whole text on request', async () => {
    const api = install([inventory({ drafts: [draft()] })])
    const wrapper = await open()
    await wrapper.find('[data-prks-role="editor-recovery-diag-view"]').trigger('click')
    await flushPromises()
    expect((wrapper.find('[data-prks-role="editor-recovery-diag-text"]').element as HTMLTextAreaElement).value).toBe('Recovered text')
    await wrapper.find('[data-prks-role="editor-recovery-diag-copy"]').trigger('click')
    await flushPromises()
    expect(win.prksCopyTextToClipboard).toHaveBeenCalledWith('Recovered text')
    const createUrl = vi.fn(() => 'blob:x')
    const revoke = vi.fn()
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    await wrapper.find('[data-prks-role="editor-recovery-diag-download"]').trigger('click')
    await flushPromises()
    expect(createUrl).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    expect(api.readText).toHaveBeenCalledWith({ source: 'record', expect: draft().expect })
    expect(api.discard).not.toHaveBeenCalled()
    click.mockRestore()
  })

  it('offers no text and no discard for a draft an editor holds', async () => {
    install([inventory({ drafts: [draft({ lineage: 'other-live' })] })])
    const wrapper = await open()
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-live"]').exists()).toBe(true)
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-view"]').exists()).toBe(false)
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-discard"]').exists()).toBe(false)
  })

  it('discards only after a danger confirmation that names the unknown owner risk, then refreshes', async () => {
    const unknown = draft({ lineage: 'unknown' })
    const api = install([inventory({ drafts: [unknown] }), inventory()])
    const wrapper = await open()
    win.prksConfirmDialog = vi.fn(async () => false)
    await wrapper.find('[data-prks-role="editor-recovery-diag-discard"]').trigger('click')
    await flushPromises()
    expect(api.discard).not.toHaveBeenCalled()
    const confirm = vi.fn(async (options: { message: string; danger: boolean }) => options.danger)
    win.prksConfirmDialog = confirm
    await wrapper.find('[data-prks-role="editor-recovery-diag-discard"]').trigger('click')
    await flushPromises()
    expect(confirm.mock.calls[0]![0].message).toContain('may belong to another browser tab that is still open')
    expect(api.discard).toHaveBeenCalledWith({ ...unknown.expect, kind: unknown.kind, entityType: 'work', entityId: 'w1', allowUnknownOwner: true })
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-message"]').text()).toBe('Draft discarded.')
    expect(wrapper.findAll('[data-prks-role="editor-recovery-diag-row"]')).toHaveLength(0)
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-summary"]').text()).toBe('No editor drafts are kept in this browser.')
  })

  it('reports a refused discard and a failed read without changing anything', async () => {
    install([inventory({ drafts: [draft()] })], {
      discard: vi.fn(async () => 'changed' as const),
      readText: vi.fn(async () => ({ ok: false as const, code: 'missing' as const })),
    })
    const wrapper = await open()
    await wrapper.find('[data-prks-role="editor-recovery-diag-copy"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-row-message"]').text()).toBe('Its text could not be read.')
    expect(win.prksCopyTextToClipboard).not.toHaveBeenCalled()
    await wrapper.find('[data-prks-role="editor-recovery-diag-discard"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-message"]').text()).toContain('was not discarded')
  })

  it('reads the list again when a draft changed or opened in an editor since it was listed', async () => {
    const api = install([inventory({ drafts: [draft()] }), inventory()], {
      readText: vi.fn(async () => ({ ok: false as const, code: 'changed' as const })),
    })
    const wrapper = await open()
    await wrapper.find('[data-prks-role="editor-recovery-diag-view"]').trigger('click')
    await flushPromises()
    expect(api.inventory).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-message"]').text()).toContain('The list was refreshed.')
    expect(wrapper.findAll('[data-prks-role="editor-recovery-diag-row"]')).toHaveLength(0)
  })

  it('says when storage is unavailable, local storage is refused, or the budget is exceeded', async () => {
    install([inventory({ storage: 'unavailable', emergencyStorage: 'unavailable', overBudget: true })])
    const wrapper = await open()
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-storage"]').text()).toContain('Recovery storage is unavailable')
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-emergency-storage"]').exists()).toBe(true)
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-budget"]').text()).toContain('256 MB')
  })

  it('drops the old rows when reading the list again fails', async () => {
    const api = install([inventory({ drafts: [draft()] })], {
      readText: vi.fn(async () => ({ ok: false as const, code: 'changed' as const })),
    })
    const wrapper = await open()
    expect(wrapper.findAll('[data-prks-role="editor-recovery-diag-row"]')).toHaveLength(1)
    api.inventory = vi.fn(async () => Promise.reject(new Error('boom')))
    await wrapper.find('[data-prks-role="editor-recovery-diag-view"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-prks-role="editor-recovery-diag-row"]')).toHaveLength(0)
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-summary"]').text()).toBe('Editor drafts could not be listed in this browser.')
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-message"]').text()).toBe('This draft changed since the list was read.')
  })

  it('says it could not list drafts when recovery is not loaded or the read throws', async () => {
    const wrapper = await open()
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-summary"]').text()).toBe('Editor drafts could not be listed in this browser.')
    install([inventory()], { inventory: vi.fn(async () => Promise.reject(new Error('boom'))) })
    await wrapper.find('[data-prks-role="editor-recovery-diag-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-prks-role="editor-recovery-diag-summary"]').text()).toBe('Editor drafts could not be listed in this browser.')
  })

  it('names downloads safely', () => {
    expect(downloadName('work-research-note', '../w/1?x', Date.UTC(2026, 9, 2, 14, 5))).toBe('prks-recovered-research-notes-w1x-202610021405.txt')
    expect(downloadName(null, null, Number.NaN)).toBe('prks-recovered-editor-draft-unknown-undated.txt')
  })
})
