import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import CreateFormsHost from './CreateFormsHost.vue'
import { CREATE_FORM_MODAL_IDS } from './types'
import {
  beginOpening,
  dismissDialogInnerLayer,
  hideDialogs,
  isDialogDirty,
  ownsDialog,
  resetDialogsForTest,
  showDialog,
} from '../../modal/dialogs'

interface StubWindow {
  fetchFolders?: () => Promise<Array<{ id?: string; title?: string; parent_id?: string | null }>>
  prksFolderRowLabel?: (folder: { title?: string }, all: unknown[]) => string
  createFolder?: (title: string, description: string, options: { parent_id: string | null }) => Promise<string>
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
  prksOfflineWasGuardRefusal?: (error: unknown) => boolean
  prksGetFocusedTabContext?: () => { tabId?: string; route?: { name?: string } } | null
  prksNavigate?: (hash: string, options?: { replace?: boolean; tabId?: string }) => void
  closeModals?: () => void
  parsePersonBirthDeathField?: (text: string) => string | null
  PERSON_DATE_HELP?: string
  prksCreatePersonDurably?: (payload: { last_name: string; first_name: string }) => Promise<{ entity_id?: string }>
  prksTakePersonIndexCreateTabId?: () => string
  syncPersonAliasesFromNames?: () => void
  prksEnsureAllGroupsCache?: () => Promise<Array<{ id: string; name: string }>>
  prksGroupRowLabel?: (group: { name?: string }, all: unknown[]) => string
  prksResolvePersonGroupParent?: (parentId: string, parentName: string, selfId: null) => Promise<string>
  prksCreatePersonGroupDurably?: (body: { name: string }) => Promise<{ entity_id?: string }>
  prksTakePersonGroupCreateNavigation?: () => { mode: string; tabId?: string }
  createPlaylist?: (title: string, description: string) => Promise<string | null>
  prksTakePlaylistIndexCreateTabId?: () => string
  __prksPendingPlaylistAttach?: { workId?: string; tabId?: string; generation?: number } | null
  prksCapturedWorkPanelStill?: (pending: { workId?: string } | null | undefined) => boolean
  addWorkToPlaylist?: (playlistId: string, workId: string, still: () => boolean) => Promise<unknown>
  __prksRefreshPlaylistSelects?: (playlistId: string) => Promise<void>
  __prksRefreshAllPlaylistSelects?: (playlistId: string) => Promise<void>
}

function stubs(): StubWindow {
  return window as unknown as StubWindow
}

/** Opens a dialog through the host, as openModal does: a new opening. */
/** Shows a dialog and runs its form's opening, as openModal does. */
function open(modal: HTMLElement | null): void {
  if (!modal) return
  showDialog(modal.id)
  beginOpening(modal.id)
}

async function flush(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await Promise.resolve()
}

describe('create form host', () => {
  const mounted: Array<{ unmount: () => void }> = []

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    document.body.innerHTML = ''
    resetDialogsForTest()
    delete window.prksRefreshFolderModalValidation
    const w = stubs()
    delete w.fetchFolders
    delete w.prksFolderRowLabel
    delete w.createFolder
    delete w.prksAlertMessage
    delete w.prksGetFocusedTabContext
    delete w.prksNavigate
    delete w.closeModals
    delete w.parsePersonBirthDeathField
    delete w.PERSON_DATE_HELP
    delete w.prksCreatePersonDurably
    delete w.prksTakePersonIndexCreateTabId
    delete w.syncPersonAliasesFromNames
    delete w.prksEnsureAllGroupsCache
    delete w.prksGroupRowLabel
    delete w.prksResolvePersonGroupParent
    delete w.prksCreatePersonGroupDurably
    delete w.prksTakePersonGroupCreateNavigation
    delete w.createPlaylist
    delete w.prksTakePlaylistIndexCreateTabId
    delete w.__prksPendingPlaylistAttach
    delete w.prksCapturedWorkPanelStill
    delete w.addWorkToPlaylist
    delete w.__prksRefreshPlaylistSelects
    delete w.__prksRefreshAllPlaylistSelects
  })

  function install(): void {
    mounted.push(mount(CreateFormsHost, { attachTo: document.body }))
    // One element per dialog id: the host's own shells, no stray fixture.
    for (const id of CREATE_FORM_MODAL_IDS) expect(document.querySelectorAll(`#${id}`), id).toHaveLength(1)
  }

  it('renders each form inside its own dialog shell with the classic field ids', () => {
    install()
    expect(document.querySelector('#folder-modal #folder-title')).not.toBeNull()
    expect(document.querySelector('#folder-modal #save-folder-btn')).not.toBeNull()
    expect(document.querySelector('#person-modal #person-lname')).not.toBeNull()
    expect(document.querySelector('#person-modal #save-person-btn')).not.toBeNull()
    expect(document.querySelector('#group-modal #group-name')).not.toBeNull()
    expect(document.querySelector('#group-modal #save-group-btn')).not.toBeNull()
    expect(document.querySelector('#playlist-modal #playlist-title')).not.toBeNull()
    expect(document.querySelector('#playlist-modal #save-playlist-btn')).not.toBeNull()
    // New File: the body lands in the dialog body and Create File in its
    // sticky footer.
    const workBody = document.querySelector('#work-modal > .modal-body.modal-body--scroll')
    expect(workBody?.querySelector(':scope > .upload-grid.form-pane')).not.toBeNull()
    for (const id of ['upload-drop-zone', 'work-source-kind', 'work-title', 'work-folder-search', 'work-doc-type',
      'upload-person-search', 'upload-tag-search', 'work-abstract']) {
      expect(workBody?.querySelector(`#${id}`), id).not.toBeNull()
    }
    expect(document.querySelectorAll('#save-work-btn')).toHaveLength(1)
    expect(document.querySelector('#work-modal-footer #save-work-btn')).not.toBeNull()
    expect(document.querySelector('#work-modal-footer #work-modal-cancel')).not.toBeNull()
    expect(document.querySelector('#work-modal-footer #upload-status-msg')).not.toBeNull()
    // No error region until there is an error to describe the Title with.
    expect(document.querySelector('#playlist-modal #playlist-title-error')).toBeNull()
  })

  it('owns the Saved View dialog: body and title are its shell\'s, focus starts on Name', async () => {
    install()
    await nextTick()
    expect(ownsDialog('saved-view-modal')).toBe(true)
    expect(document.querySelector('#saved-view-modal .modal-body #saved-view-name')).not.toBeNull()
    expect(document.querySelector('#saved-view-modal .modal-body #save-saved-view-btn')).not.toBeNull()
    expect(document.getElementById('saved-view-modal-title')?.textContent).toBe('Save View')
    open(document.getElementById('saved-view-modal'))
    expect(document.activeElement?.id).toBe('saved-view-name')
    // No unsaved-changes confirm and no inner layer, as before.
    expect(dismissDialogInnerLayer('saved-view-modal')).toBe(false)
  })

  it('tracks a dirty folder draft from the view and blocks a duplicate title', async () => {
    install()
    const w = stubs()
    w.fetchFolders = async () => [{ id: 'f1', title: 'Notes', parent_id: '' }]
    w.prksFolderRowLabel = (folder) => String(folder.title || '')
    const modal = document.getElementById('folder-modal')
    // The opening loads the folders and takes the baseline.
    open(modal)
    await flush()
    expect(isDialogDirty('folder-modal')).toBe(false)
    const title = document.getElementById('folder-title') as HTMLInputElement
    title.value = 'Notes'
    title.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(isDialogDirty('folder-modal')).toBe(true)
    expect(document.getElementById('folder-title-error')?.textContent).toContain('already exists')
    const button = document.getElementById('save-folder-btn') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(title.getAttribute('aria-invalid')).toBe('true')
  })

  it('reads a programmatic folder title and creates once for a double click', async () => {
    install()
    const w = stubs()
    w.fetchFolders = async () => []
    let calls = 0
    let resolveCreate: (id: string) => void = () => {}
    w.createFolder = (title) => {
      calls += 1
      expect(title).toBe('Opening B')
      return new Promise((resolve) => {
        resolveCreate = resolve
      })
    }
    w.prksGetFocusedTabContext = () => ({ tabId: 'main', route: { name: 'home' } })
    let navigated = ''
    w.prksNavigate = (hash) => {
      navigated = hash
    }
    w.closeModals = () => {
      hideDialogs()
    }
    const modal = document.getElementById('folder-modal')
    open(modal)
    const title = document.getElementById('folder-title') as HTMLInputElement
    title.value = 'Opening B'
    const button = document.getElementById('save-folder-btn') as HTMLButtonElement
    button.click()
    button.click()
    expect(calls).toBe(1)
    expect(button.getAttribute('aria-busy')).toBe('true')
    expect(button.textContent?.trim()).toBe('Creating…')
    resolveCreate('F-NEW')
    await flush()
    expect(navigated).toBe('#/folders/F-NEW')
  })

  it('refuses a person without a last name and creates one with the durable writer', async () => {
    install()
    const w = stubs()
    const alerts: string[] = []
    w.prksAlertMessage = async (message) => {
      alerts.push(String(message))
    }
    w.parsePersonBirthDeathField = (text) => {
      const value = text.trim()
      if (value === 'bad') return null
      return value
    }
    w.PERSON_DATE_HELP = 'use a date'
    let created = 0
    w.prksCreatePersonDurably = async (payload) => {
      created += 1
      expect(payload.last_name).toBe('Ada')
      expect(payload.first_name).toBe('Augusta')
      return { entity_id: 'P-1' }
    }
    w.prksTakePersonIndexCreateTabId = () => 'tab-9'
    let hash = ''
    let tabId = ''
    w.prksNavigate = (next, options) => {
      hash = next
      tabId = options?.tabId || ''
    }
    w.closeModals = () => {}
    const personModal = document.getElementById('person-modal')
    open(personModal)
    document.getElementById('save-person-btn')?.click()
    await flush()
    expect(alerts[0]).toContain('Last name is required.')
    expect(created).toBe(0)

    const birth = document.getElementById('person-birth-date') as HTMLInputElement
    birth.value = 'bad'
    document.getElementById('save-person-btn')?.click()
    await flush()
    expect(alerts.at(-1)).toContain('Birth:')
    birth.value = ''

    const first = document.getElementById('person-fname') as HTMLInputElement
    const last = document.getElementById('person-lname') as HTMLInputElement
    first.value = 'Augusta'
    last.value = 'Ada'
    w.syncPersonAliasesFromNames = () => {
      const aliases = document.getElementById('person-aliases') as HTMLInputElement
      aliases.value = 'Ada'
    }
    first.dispatchEvent(new Event('input', { bubbles: true }))
    document.getElementById('save-person-btn')?.click()
    await flush()
    expect(created).toBe(1)
    expect((document.getElementById('person-aliases') as HTMLInputElement).value).toBe('Ada')
    expect(hash).toBe('#/people/P-1')
    expect(tabId).toBe('tab-9')
  })

  it('requires a group name and a playlist title before the durable create', async () => {
    install()
    const w = stubs()
    const alerts: string[] = []
    w.prksAlertMessage = async (message) => {
      alerts.push(String(message))
    }
    w.prksEnsureAllGroupsCache = async () => []
    let groups = 0
    w.prksCreatePersonGroupDurably = async (body) => {
      groups += 1
      expect(body.name).toBe('Frankfurt')
      return { entity_id: 'G-1' }
    }
    w.prksResolvePersonGroupParent = async () => ''
    w.prksTakePersonGroupCreateNavigation = () => ({ mode: 'owner', tabId: 'g-tab' })
    let hash = ''
    w.prksNavigate = (next) => {
      hash = next
    }
    w.closeModals = () => {}
    const groupModal = document.getElementById('group-modal')
    open(groupModal)
    document.getElementById('save-group-btn')?.click()
    await flush()
    expect(alerts[0]).toContain('Group name is required.')
    const name = document.getElementById('group-name') as HTMLInputElement
    name.value = 'Frankfurt'
    document.getElementById('save-group-btn')?.click()
    await flush()
    await flush()
    expect(groups).toBe(1)
    expect(hash).toBe('#/people/groups/G-1')

    let playlists = 0
    let playlistArgs: [string, string] | null = null
    w.createPlaylist = async (title, description) => {
      playlists += 1
      // Asserted after the save: an expect in here would be swallowed by its catch.
      playlistArgs = [title, description]
      return 'PL-1'
    }
    w.prksTakePlaylistIndexCreateTabId = () => ''
    const playlistModal = document.getElementById('playlist-modal')
    open(playlistModal)
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    expect(document.getElementById('playlist-title-error')?.textContent).toContain('Playlist title is required.')
    expect(document.getElementById('playlist-title')?.getAttribute('aria-describedby')).toContain('playlist-title-error')
    expect(playlists).toBe(0)
    const title = document.getElementById('playlist-title') as HTMLInputElement
    const description = document.getElementById('playlist-description') as HTMLTextAreaElement
    title.value = '  Lectures  '
    description.value = ' Week one '
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    expect(playlists).toBe(1)
    expect(playlistArgs).toEqual(['Lectures', 'Week one'])

    // A failed create is shown for the form, not as a Title error.
    w.createPlaylist = async () => {
      throw new Error('Server unavailable')
    }
    open(playlistModal)
    title.value = 'Seminars'
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    await flush()
    expect(document.getElementById('playlist-error')?.textContent).toContain('Server unavailable')
    expect(document.getElementById('playlist-error')?.getAttribute('role')).toBe('alert')
    expect(document.getElementById('playlist-title-error')).toBeNull()
    expect(title.getAttribute('aria-invalid')).toBeNull()

    // Retrying with an empty title replaces the stale failure.
    title.value = ''
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    expect(document.getElementById('playlist-error')?.textContent).toBe('')
    expect(document.getElementById('playlist-title-error')?.textContent).toContain('Playlist title is required.')
  })

  it('keeps a reopened Person draft when an older create is still pending', async () => {
    install()
    const w = stubs()
    w.parsePersonBirthDeathField = (text) => text.trim()
    w.PERSON_DATE_HELP = 'use a date'
    let resolveCreate: (value: { entity_id: string }) => void = () => {}
    let creates = 0
    w.prksCreatePersonDurably = () => {
      creates += 1
      return new Promise((resolve) => {
        resolveCreate = resolve
      })
    }
    let closed = 0
    let navigated = ''
    w.closeModals = () => {
      closed += 1
      hideDialogs()
    }
    w.prksNavigate = (hash) => {
      navigated = hash
    }
    w.prksTakePersonIndexCreateTabId = () => ''
    const modal = document.getElementById('person-modal')
    open(modal)
    ;(document.getElementById('person-lname') as HTMLInputElement).value = 'Old'
    document.getElementById('save-person-btn')?.click()
    await flush()
    expect(creates).toBe(1)
    expect(document.getElementById('save-person-btn')?.getAttribute('aria-busy')).toBe('true')

    // Dismiss → reopen clears fields and busy for the new opening.
    hideDialogs()
    await flush()
    // The new opening resets the fields and frees Save.
    open(modal)
    await flush()
    ;(document.getElementById('person-lname') as HTMLInputElement).value = 'New'
    expect(document.getElementById('save-person-btn')?.getAttribute('aria-busy')).not.toBe('true')

    let resolveSecond: (value: { entity_id: string }) => void = () => {}
    w.prksCreatePersonDurably = () => {
      creates += 1
      return new Promise((resolve) => {
        resolveSecond = resolve
      })
    }
    document.getElementById('save-person-btn')?.click()
    await flush()
    expect(creates).toBe(2)

    resolveCreate({ entity_id: 'P-OLD' })
    await flush()
    expect(closed).toBe(0)
    expect(navigated).toBe('')
    expect((document.getElementById('person-lname') as HTMLInputElement).value).toBe('New')
    expect(document.getElementById('save-person-btn')?.getAttribute('aria-busy')).toBe('true')

    resolveSecond({ entity_id: 'P-NEW' })
    await flush()
    expect(closed).toBe(1)
    expect(navigated).toBe('#/people/P-NEW')
  })

  it('attaches a playlist to the Work captured for that Save opening', async () => {
    install()
    const w = stubs()
    const firstAttach = { workId: 'W-1', tabId: 't1', generation: 1 }
    const secondAttach = { workId: 'W-2', tabId: 't2', generation: 2 }
    w.__prksPendingPlaylistAttach = firstAttach
    w.prksCapturedWorkPanelStill = (pending) => pending === w.__prksPendingPlaylistAttach
    const attached: Array<{ playlistId: string; workId: string }> = []
    w.addWorkToPlaylist = async (playlistId, workId) => {
      attached.push({ playlistId, workId })
    }
    w.__prksRefreshPlaylistSelects = async () => {}
    w.__prksRefreshAllPlaylistSelects = async () => {}
    let resolveFirst: (id: string) => void = () => {}
    let resolveSecond: (id: string) => void = () => {}
    let creates = 0
    w.createPlaylist = () => {
      creates += 1
      return new Promise((resolve) => {
        if (creates === 1) resolveFirst = resolve
        else resolveSecond = resolve
      })
    }
    let closed = 0
    w.closeModals = () => {
      closed += 1
      hideDialogs()
    }
    w.prksTakePlaylistIndexCreateTabId = () => ''
    const modal = document.getElementById('playlist-modal')
    open(modal)
    ;(document.getElementById('playlist-title') as HTMLInputElement).value = 'First'
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    expect(creates).toBe(1)

    hideDialogs()
    await flush()
    w.__prksPendingPlaylistAttach = secondAttach
    // The new opening resets the fields and frees Create.
    open(modal)
    await flush()
    ;(document.getElementById('playlist-title') as HTMLInputElement).value = 'Second'
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    expect(creates).toBe(2)

    resolveFirst('PL-OLD')
    await flush()
    expect(attached).toEqual([])
    expect(closed).toBe(0)
    expect(w.__prksPendingPlaylistAttach).toBe(secondAttach)

    resolveSecond('PL-NEW')
    await flush()
    expect(attached).toEqual([{ playlistId: 'PL-NEW', workId: 'W-2' }])
    expect(w.__prksPendingPlaylistAttach).toBeNull()
    expect(closed).toBe(1)
  })
})
