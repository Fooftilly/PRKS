import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import CreateFormsHost from './CreateFormsHost.vue'

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
  createPlaylist?: (title: string, description: string) => Promise<string>
  prksTakePlaylistIndexCreateTabId?: () => string
}

function stubs(): StubWindow {
  return window as unknown as StubWindow
}

function installShells(): void {
  document.body.innerHTML = [
    '<div id="folder-modal" class="modal hidden"><div id="folder-modal-vue" class="modal-body"></div></div>',
    '<div id="person-modal" class="modal hidden"><div id="person-modal-vue" class="modal-body"></div></div>',
    '<div id="group-modal" class="modal hidden"><div id="group-modal-vue" class="modal-body"></div></div>',
    '<div id="playlist-modal" class="modal hidden"><div id="playlist-modal-vue" class="modal-body"></div></div>',
  ].join('')
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
    delete window.prksCreateForms
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
  })

  function install(): void {
    installShells()
    mounted.push(mount(CreateFormsHost, { attachTo: document.body }))
  }

  it('teleports the classic field ids into the shells', () => {
    install()
    expect(document.querySelector('#folder-modal #folder-title')).not.toBeNull()
    expect(document.querySelector('#folder-modal #save-folder-btn')).not.toBeNull()
    expect(document.querySelector('#person-modal #person-lname')).not.toBeNull()
    expect(document.querySelector('#person-modal #save-person-btn')).not.toBeNull()
    expect(document.querySelector('#group-modal #group-name')).not.toBeNull()
    expect(document.querySelector('#group-modal #save-group-btn')).not.toBeNull()
    expect(document.querySelector('#playlist-modal #playlist-title')).not.toBeNull()
    expect(document.querySelector('#playlist-modal #save-playlist-btn')).not.toBeNull()
    expect(document.querySelector('#playlist-modal #playlist-error')?.classList.contains('hidden')).toBe(true)
  })

  it('tracks a dirty folder draft from the view and blocks a duplicate title', async () => {
    install()
    const w = stubs()
    w.fetchFolders = async () => [{ id: 'f1', title: 'Notes', parent_id: '' }]
    w.prksFolderRowLabel = (folder) => String(folder.title || '')
    await window.prksCreateForms?.refreshFolderValidation()
    const modal = document.getElementById('folder-modal')
    modal?.classList.remove('hidden')
    window.prksCreateForms?.captureBaseline('folder-modal')
    expect(window.prksCreateForms?.isDirty('folder-modal')).toBe(false)
    const title = document.getElementById('folder-title') as HTMLInputElement
    title.value = 'Notes'
    title.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(window.prksCreateForms?.isDirty('folder-modal')).toBe(true)
    expect(document.getElementById('folder-title-error')?.textContent).toContain('already exists')
    const button = document.getElementById('save-folder-btn') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    title.setAttribute('aria-invalid', 'true')
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
      document.getElementById('folder-modal')?.classList.add('hidden')
    }
    const modal = document.getElementById('folder-modal')
    modal?.classList.remove('hidden')
    if (modal) modal.dataset.prksOpenGeneration = '4'
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
    expect(navigated).toBe('#/folders')
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
    document.getElementById('save-group-btn')?.click()
    await flush()
    expect(alerts[0]).toContain('Group name is required.')
    const name = document.getElementById('group-name') as HTMLInputElement
    name.value = 'Frankfurt'
    document.getElementById('save-group-btn')?.click()
    await flush()
    expect(groups).toBe(1)
    expect(hash).toBe('#/people/groups/G-1')

    let playlists = 0
    w.createPlaylist = async (title, description) => {
      playlists += 1
      expect(title).toBe('Lectures')
      expect(description).toBe('Week one')
      return 'PL-1'
    }
    w.prksTakePlaylistIndexCreateTabId = () => ''
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    expect(document.getElementById('playlist-error')?.textContent).toContain('Playlist title is required.')
    expect(playlists).toBe(0)
    const title = document.getElementById('playlist-title') as HTMLInputElement
    const description = document.getElementById('playlist-description') as HTMLTextAreaElement
    title.value = '  Lectures  '
    description.value = ' Week one '
    document.getElementById('save-playlist-btn')?.click()
    await flush()
    expect(playlists).toBe(1)
  })
})
