import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import CreateFormsHost from './CreateFormsHost.vue'
import { CREATE_FORM_MODAL_IDS } from './types'
import {
  beginOpening,
  dialogsClosed,
  dismissDialogInnerLayer,
  hideDialogs,
  resetDialogsForTest,
  showDialog,
} from '../../modal/dialogs'

interface StubWindow {
  fetchFolders?: () => Promise<Array<{ id?: string; title?: string; parent_id?: string | null }>>
  createFolder?: (title: string, description: string, options: { parent_id: string | null }) => Promise<string>
  prksGetFocusedTabContext?: () => unknown
  prksNavigate?: (hash: string) => void
  closeModals?: () => void
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
  prksEnsureAllGroupsCache?: () => Promise<Array<{ id: string; name: string }>>
  prksResolvePersonGroupParent?: (parentId: string, parentName: string, selfId: null) => Promise<string>
  prksCreatePersonGroupDurably?: (body: { name: string; parent_id: string }) => Promise<{ entity_id?: string }>
  prksTakePersonGroupCreateNavigation?: () => { mode: string }
  createPlaylist?: (title: string, description: string) => Promise<string | null>
  parsePersonBirthDeathField?: (text: string) => string | null
  prksCreatePersonDurably?: (payload: { first_name: string; last_name: string }) => Promise<{ entity_id?: string }>
  prksTakePersonIndexCreateTabId?: () => string
  prksTakePlaylistIndexCreateTabId?: () => string
}

const STUB_KEYS: Array<keyof StubWindow> = [
  'fetchFolders',
  'createFolder',
  'prksGetFocusedTabContext',
  'prksNavigate',
  'closeModals',
  'prksAlertMessage',
  'prksEnsureAllGroupsCache',
  'prksResolvePersonGroupParent',
  'prksCreatePersonGroupDurably',
  'prksTakePersonGroupCreateNavigation',
  'createPlaylist',
  'parsePersonBirthDeathField',
  'prksCreatePersonDurably',
  'prksTakePersonIndexCreateTabId',
  'prksTakePlaylistIndexCreateTabId',
]

function stubs(): StubWindow {
  return window as unknown as StubWindow
}

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await nextTick()
    await Promise.resolve()
  }
}

/** Opens a dialog through the host and runs its opening, as openModal does. */
function show(modalId: string): HTMLElement {
  showDialog(modalId)
  beginOpening(modalId)
  const modal = document.getElementById(modalId) as HTMLElement
  expect(modal.classList.contains('hidden')).toBe(false)
  return modal
}

function hideAll(): void {
  hideDialogs()
  dialogsClosed()
}

function input(id: string): HTMLInputElement {
  return document.getElementById(id) as HTMLInputElement
}

function key(target: HTMLElement, keyName: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: keyName, bubbles: true, cancelable: true, ...init })
  target.dispatchEvent(event)
  return event
}

function type(el: HTMLInputElement, value: string): void {
  el.focus()
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('create form keyboard completion', () => {
  const mounted: Array<{ unmount: () => void }> = []

  beforeAll(async () => {
    // The shared combobox keyboard model the classic page loads before Vue.
    const nodeModule = 'node:module'
    const { createRequire } = (await import(/* @vite-ignore */ nodeModule)) as {
      createRequire: (url: string) => (id: string) => unknown
    }
    createRequire(import.meta.url)('../../../../frontend/js/combobox-keys.js')
  })

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    document.body.innerHTML = ''
    resetDialogsForTest()
    delete window.prksRefreshFolderModalValidation
    const w = stubs()
    for (const name of STUB_KEYS) delete w[name]
  })

  function install(): void {
    mounted.push(mount(CreateFormsHost, { attachTo: document.body }))
    // One element per dialog id: the host's own shells, no stray fixture.
    for (const id of CREATE_FORM_MODAL_IDS) expect(document.querySelectorAll(`#${id}`), id).toHaveLength(1)
  }

  it('focuses the first field of each dialog when it opens (#603, #589)', () => {
    install()
    const first: Record<string, string> = {
      'folder-modal': 'folder-title',
      'person-modal': 'person-fname',
      'group-modal': 'group-name',
      'playlist-modal': 'playlist-title',
    }
    for (const [modalId, fieldId] of Object.entries(first)) {
      show(modalId)
      expect(document.activeElement?.id).toBe(fieldId)
      hideAll()
    }
  })

  it('creates a folder on Enter in Title and on Ctrl+Enter in Description, not on Enter there', async () => {
    install()
    const w = stubs()
    w.fetchFolders = async () => []
    const titles: string[] = []
    w.createFolder = async (title) => {
      titles.push(title)
      return `F-${titles.length}`
    }
    w.closeModals = hideAll
    w.prksNavigate = () => {}
    await window.prksRefreshFolderModalValidation?.()
    show('folder-modal')
    type(input('folder-title'), 'By Enter')
    expect(key(input('folder-title'), 'Enter').defaultPrevented).toBe(true)
    await flush()
    expect(titles).toEqual(['By Enter'])

    show('folder-modal')
    type(input('folder-title'), 'By chord')
    const description = document.getElementById('folder-description') as HTMLTextAreaElement
    key(description, 'Enter')
    key(input('folder-title'), 'Enter', { isComposing: true })
    await flush()
    expect(titles).toEqual(['By Enter'])
    key(description, 'Enter', { ctrlKey: true })
    await flush()
    expect(titles).toEqual(['By Enter', 'By chord'])
  })

  it('creates a playlist on Enter in Title', async () => {
    install()
    const w = stubs()
    const created: string[] = []
    w.createPlaylist = async (title) => {
      created.push(title)
      return 'PL-1'
    }
    w.closeModals = hideAll
    w.prksNavigate = () => {}
    w.prksTakePlaylistIndexCreateTabId = () => ''
    show('playlist-modal')
    input('playlist-title').value = 'Lectures'
    key(input('playlist-title'), 'Enter')
    await flush()
    expect(created).toEqual(['Lectures'])
  })

  it('starts the next New Folder and New Group blank after a close (#373)', async () => {
    install()
    show('folder-modal')
    input('folder-title').value = 'Old folder'
    ;(document.getElementById('folder-description') as HTMLTextAreaElement).value = 'old'
    show('group-modal')
    input('group-name').value = 'Old group'
    hideAll()
    await flush()
    expect(input('folder-title').value).toBe('')
    expect((document.getElementById('folder-description') as HTMLTextAreaElement).value).toBe('')
    expect(input('group-name').value).toBe('')
  })

  it('picks a parent folder with ArrowDown and Enter, and refuses an unpicked query (#467)', async () => {
    install()
    const w = stubs()
    w.fetchFolders = async () => [
      { id: 'F-A', title: 'Alpha', parent_id: null },
      { id: 'F-S', title: 'Sibling', parent_id: null },
    ]
    const parents: Array<string | null> = []
    w.createFolder = async (_title, _description, options) => {
      parents.push(options.parent_id)
      return 'F-NEW'
    }
    w.closeModals = hideAll
    w.prksNavigate = () => {}
    await window.prksRefreshFolderModalValidation?.()
    show('folder-modal')
    input('folder-title').value = 'Child'
    const search = input('folder-parent-search')
    type(search, 'Sib')
    await flush()
    // A typed query is still a query: Save refuses it and says why.
    document.getElementById('save-folder-btn')?.click()
    await flush()
    expect(parents).toEqual([])
    expect(document.getElementById('folder-parent-search-error')?.textContent).toContain('Pick a parent folder')
    expect(document.activeElement?.id).toBe('folder-parent-search')

    // Enter on a typed query picks the first real match.
    type(search, 'Alp')
    await flush()
    key(search, 'Enter')
    await flush()
    expect(input('folder-parent-id').value).toBe('F-A')

    type(search, 'i')
    await flush()
    key(search, 'ArrowDown')
    await flush()
    key(search, 'Enter')
    await flush()
    expect(input('folder-parent-id').value).toBe('F-S')
    expect(search.value).toBe('Sibling')
    expect(document.getElementById('folder-parent-search-error')).toBeNull()
    document.getElementById('save-folder-btn')?.click()
    await flush()
    expect(parents).toEqual(['F-S'])
  })

  it('offers an explicit Create group row and refuses an unpicked parent query (#467)', async () => {
    install()
    const w = stubs()
    w.prksEnsureAllGroupsCache = async () => [{ id: 'G-1', name: 'Group Parent' }]
    const resolved: Array<[string, string]> = []
    w.prksResolvePersonGroupParent = async (parentId, parentName) => {
      resolved.push([parentId, parentName])
      return parentId || 'G-NEW-PARENT'
    }
    const bodies: Array<{ name: string; parent_id: string }> = []
    w.prksCreatePersonGroupDurably = async (body) => {
      bodies.push(body)
      return { entity_id: 'G-2' }
    }
    w.prksTakePersonGroupCreateNavigation = () => ({ mode: 'unscoped' })
    w.closeModals = hideAll
    w.prksNavigate = () => {}
    show('group-modal')
    await flush()
    input('group-name').value = 'Child group'
    const search = input('group-parent-search')

    type(search, 'Brand New')
    await flush()
    const createRow = document.querySelector('#group-parent-results .result-item--create')
    expect(createRow?.textContent).toBe('Create group "Brand New"')
    document.getElementById('save-group-btn')?.click()
    await flush()
    expect(bodies).toEqual([])
    expect(document.getElementById('group-parent-search-error')?.textContent).toContain('Pick a parent group')

    // Enter on a typed query picks the first real match before the Create row.
    type(search, 'Group Paren')
    await flush()
    key(search, 'Enter')
    await flush()
    expect(input('group-parent-id').value).toBe('G-1')
    document.getElementById('save-group-btn')?.click()
    await flush()
    expect(resolved.at(-1)).toEqual(['G-1', 'Group Parent'])
    expect(bodies.at(-1)?.parent_id).toBe('G-1')

    show('group-modal')
    await flush()
    input('group-name').value = 'Second child'
    type(input('group-parent-search'), 'Brand New')
    await flush()
    key(input('group-parent-search'), 'Enter')
    await flush()
    expect(input('group-parent-search').value).toBe('Brand New')
    document.getElementById('save-group-btn')?.click()
    await flush()
    expect(resolved.at(-1)).toEqual(['', 'Brand New'])
    expect(bodies.at(-1)?.parent_id).toBe('G-NEW-PARENT')
  })

  it('creates a person on Enter in either name field and on Ctrl+Enter, and still requires a last name', async () => {
    install()
    const w = stubs()
    const alerts: string[] = []
    w.prksAlertMessage = async (message) => {
      alerts.push(String(message))
    }
    w.parsePersonBirthDeathField = (text) => text.trim()
    const created: string[] = []
    w.prksCreatePersonDurably = async (payload) => {
      created.push(`${payload.first_name} ${payload.last_name}`)
      return { entity_id: `P-${created.length}` }
    }
    w.prksTakePersonIndexCreateTabId = () => ''
    w.closeModals = hideAll
    w.prksNavigate = () => {}

    show('person-modal')
    input('person-fname').value = 'Ada'
    key(input('person-fname'), 'Enter')
    await flush()
    expect(created).toEqual([])
    expect(alerts.at(-1)).toContain('Last name is required.')

    input('person-lname').value = 'Lovelace'
    key(input('person-lname'), 'Enter')
    await flush()
    expect(created).toEqual(['Ada Lovelace'])

    show('person-modal')
    input('person-fname').value = 'Grace'
    input('person-lname').value = 'Hopper'
    key(document.getElementById('person-about') as HTMLElement, 'Enter', { metaKey: true })
    await flush()
    expect(created).toEqual(['Ada Lovelace', 'Grace Hopper'])
  })

  it('lets Escape close an open parent list before the dialog (#467)', async () => {
    install()
    const w = stubs()
    w.fetchFolders = async () => [{ id: 'F-A', title: 'Alpha', parent_id: null }]
    await window.prksRefreshFolderModalValidation?.()
    show('folder-modal')
    expect(dismissDialogInnerLayer('folder-modal')).toBe(false)
    type(input('folder-parent-search'), 'Alp')
    await flush()
    const results = document.getElementById('folder-parent-results') as HTMLElement
    expect(results.classList.contains('is-open')).toBe(true)
    expect(dismissDialogInnerLayer('folder-modal')).toBe(true)
    await flush()
    expect(results.classList.contains('is-open')).toBe(false)
    // Nothing left to close: the next Escape belongs to the dialog.
    expect(dismissDialogInnerLayer('folder-modal')).toBe(false)
  })

  it('keeps the parent list open when focus returns to the picker before the blur collapse', async () => {
    vi.useFakeTimers()
    try {
      install()
      const w = stubs()
      w.fetchFolders = async () => [{ id: 'F-A', title: 'Alpha', parent_id: null }]
      await window.prksRefreshFolderModalValidation?.()
      show('folder-modal')
      const search = input('folder-parent-search')
      type(search, 'Alp')
      await nextTick()
      search.dispatchEvent(new FocusEvent('blur'))
      search.dispatchEvent(new FocusEvent('focus'))
      vi.advanceTimersByTime(500)
      await nextTick()
      const results = document.getElementById('folder-parent-results') as HTMLElement
      expect(results.classList.contains('is-open')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })
})
