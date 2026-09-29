import { nextTick } from 'vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import docTypesSource from '../../../../frontend/js/doc-types.js?raw'
import { applyWorkMetadataChrome, presentWorkMetadataEditor, resetWorkMetadataEditorForTests } from './metadata-session'
import type { WorkMetadataOwner } from './metadata-session'
import { cloneWorkMetaDraft } from './metadata-draft'

function owner(tabId: string, workId: string, title: string): WorkMetadataOwner & {
  ui: {
    workDetailsMode: string
    workMetaDraft: ReturnType<typeof cloneWorkMetaDraft>
    workMetaBaseline: ReturnType<typeof cloneWorkMetaDraft>
    workMetaDraftWorkId: string
    workMetaEditSession: number
  }
} {
  const seed = cloneWorkMetaDraft({ title, status: 'Not Started', doc_type: 'article' })
  return {
    tabId,
    ui: {
      workDetailsMode: 'metadata',
      workMetaDraft: seed,
      workMetaBaseline: cloneWorkMetaDraft(seed),
      workMetaDraftWorkId: workId,
      workMetaEditSession: 1,
    },
    getEntity: () => ({ id: workId }),
  }
}

describe('work metadata editor session', () => {
  beforeAll(() => {
    ;(window as unknown as { eval: (code: string) => void }).eval(docTypesSource)
  })

  afterEach(() => {
    document.body.innerHTML = ''
    resetWorkMetadataEditorForTests()
  })

  it('keeps Work A draft when a late chrome update arrives for a panel Work B owns', async () => {
    document.body.innerHTML = `
      <div id="panel-content" data-prks-owner-tab-id="main" data-prks-owner-generation="1">
        <div data-prks-role="work-metadata-editor-anchor"></div>
      </div>`
    const main = owner('main', 'A', 'Alpha')
    expect(presentWorkMetadataEditor(main, 'pdf')).toBe(true)
    main.ui.workMetaDraft.title = 'Only Work A Draft'
    await nextTick()
    const title = document.getElementById('meta-title')
    expect(title).toBeInstanceOf(HTMLInputElement)
    expect((title as HTMLInputElement).value).toBe('Only Work A Draft')

    const panel = document.getElementById('panel-content') as HTMLElement
    panel.dataset.prksOwnerTabId = 'side'
    panel.innerHTML = '<div data-prks-role="work-metadata-editor-anchor"></div>'
    const side = owner('side', 'B', 'Beta')
    expect(presentWorkMetadataEditor(side, 'pdf')).toBe(true)
    await nextTick()
    expect(applyWorkMetadataChrome('main', 'A', [{
      name: 'identity',
      status: 'stale',
      saveDisabled: true,
      fields: { title: { disabled: true, title: 'stale' } },
      conflicts: [],
    }])).toBe(false)
    expect((document.getElementById('meta-title') as HTMLInputElement).value).toBe('Beta')
    expect(main.ui.workMetaDraft.title).toBe('Only Work A Draft')
  })

  it('clears the document-type menu aria-disabled state when chrome becomes editable', async () => {
    document.body.innerHTML = `
      <div id="panel-content" data-prks-owner-tab-id="main" data-prks-owner-generation="1">
        <div data-prks-role="work-metadata-editor-anchor"></div>
      </div>`
    const main = owner('main', 'A', 'Alpha')
    expect(presentWorkMetadataEditor(main, 'pdf')).toBe(true)
    await nextTick()
    const trigger = document.getElementById('meta-doc-type-trigger')
    expect(trigger).toBeInstanceOf(HTMLButtonElement)
    const button = trigger as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.getAttribute('aria-disabled')).toBe('true')
    const close = document.querySelector('[aria-label="Close metadata editor"]')
    expect(close).toBeInstanceOf(HTMLButtonElement)
    expect(close?.querySelector('[aria-hidden="true"]')?.textContent).toBe('×')

    expect(applyWorkMetadataChrome('main', 'A', [{
      name: 'identity',
      status: 'All changes synced',
      saveDisabled: false,
      fields: {
        title: { disabled: false, title: '' },
        doc_type: { disabled: false, title: '' },
      },
      conflicts: [],
    }])).toBe(true)
    await nextTick()
    expect(button.disabled).toBe(false)
    expect(button.hasAttribute('aria-disabled')).toBe(false)
  })
})
