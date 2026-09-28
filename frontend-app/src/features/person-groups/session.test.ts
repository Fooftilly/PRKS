import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  PERSON_GROUPS_RETAIN_SURFACE_KEY,
  dismissPersonGroups,
  presentPersonGroupDetail,
  presentPersonGroupsIndex,
  resetPersonGroupsSessionForTests,
} from './session'

afterEach(() => {
  resetPersonGroupsSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

async function flushView(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

function owner(tabId: string) {
  const cleanups: Array<() => void> = []
  return {
    tabId,
    generation: 1,
    isCurrent: () => true,
    lastResolvedRoute: { name: 'people-groups' as const, params: {} },
    ui: {
      personGroupEditing: false,
      personGroupMembersEditing: false,
      personGroupEditSession: 0,
      personGroupMemberSession: 0,
      personGroupFieldBaseline: null as null | {
        name: string
        description: string
        parent_id: string
        parent_name: string
        groupId?: string
      },
    },
    getEntity: () => null as { id?: string; name?: string } | null,
    registerCleanup(fn: () => void) {
      cleanups.push(fn)
    },
    runCleanups() {
      const pending = cleanups.splice(0)
      pending.forEach((fn) => fn())
    },
    [PERSON_GROUPS_RETAIN_SURFACE_KEY]: false,
  }
}

const parent = { id: 'G1', name: 'Parent Branch', parent_id: '', member_count: 0, child_count: 1 }
const child = { id: 'G2', name: 'Child Branch', parent_id: 'G1', member_count: 0, child_count: 0 }

function typeSearch(input: HTMLInputElement, value: string): void {
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('Person Groups session', () => {
  it('keeps Main and Secondary search apart and drops it when the surface unmounts', async () => {
    const main = owner('main')
    const secondary = owner('secondary')
    const mainHost = host()
    const secondaryHost = host()
    presentPersonGroupsIndex({ owner: main, host: mainHost, items: [parent, child], generation: 1 })
    presentPersonGroupsIndex({ owner: secondary, host: secondaryHost, items: [parent, child], generation: 1 })
    await flushView()
    const mainSearch = mainHost.querySelector('#prks-group-library-search')
    const secondarySearch = secondaryHost.querySelector('#prks-group-library-search')
    expect(mainSearch).toBeInstanceOf(HTMLInputElement)
    expect(secondarySearch).toBeInstanceOf(HTMLInputElement)
    typeSearch(mainSearch as HTMLInputElement, 'Parent')
    typeSearch(secondarySearch as HTMLInputElement, 'zzzz-only')
    await flushView()
    expect(mainHost.textContent).toContain('Parent Branch')
    expect(secondaryHost.textContent).toContain('No groups match your search.')
    expect(secondaryHost.textContent).not.toContain('Parent Branch')
    dismissPersonGroups(secondary)
    presentPersonGroupsIndex({ owner: secondary, host: secondaryHost, items: [parent, child], generation: 2 })
    await flushView()
    const remounted = secondaryHost.querySelector('#prks-group-library-search')
    expect(remounted).toBeInstanceOf(HTMLInputElement)
    expect((remounted as HTMLInputElement).value).toBe('')
    expect(secondaryHost.textContent).toContain('Parent Branch')
  })

  it('retains the mounted surface across beginRoute and dismisses when the flag is clear', async () => {
    const pane = owner('main')
    const paneHost = host()
    presentPersonGroupsIndex({ owner: pane, host: paneHost, items: [parent], generation: 2 })
    await flushView()
    expect(paneHost.querySelector('[data-prks-person-groups-index-view]')).toBeTruthy()
    pane[PERSON_GROUPS_RETAIN_SURFACE_KEY] = true
    pane.runCleanups()
    expect(paneHost.querySelector('[data-prks-person-groups-index-view]')).toBeTruthy()
    pane[PERSON_GROUPS_RETAIN_SURFACE_KEY] = false
    pane.runCleanups()
    expect(paneHost.querySelector('[data-prks-person-groups-index-view]')).toBeNull()
  })

  it('shows the editor for the owning pane and keeps Cancel addressable', async () => {
    const pane = owner('main')
    pane.ui.personGroupEditing = true
    pane.ui.personGroupEditSession = 3
    pane.ui.personGroupFieldBaseline = {
      groupId: 'G1',
      name: 'Parent Branch',
      description: 'Group description',
      parent_id: '',
      parent_name: '',
    }
    pane.getEntity = () => ({ id: 'G1', name: 'Parent Branch' })
    const paneHost = host()
    presentPersonGroupDetail({
      owner: pane,
      host: paneHost,
      group: {
        id: 'G1',
        name: 'Parent Branch',
        description: 'Group description',
        parent: null,
        children: [],
        members: [],
      },
      editing: true,
      generation: 4,
    })
    await flushView()
    const name = paneHost.querySelector('#gd-name')
    expect(name).toBeInstanceOf(HTMLInputElement)
    expect((name as HTMLInputElement).value).toBe('Parent Branch')
    const save = paneHost.querySelector('#gd-save-btn')
    expect(save).toBeInstanceOf(HTMLButtonElement)
    expect((save as HTMLButtonElement).onclick).toEqual(expect.any(Function))
    expect(paneHost.querySelector('[data-prks-group-edit-cancel]')?.getAttribute('onclick')).toBeNull()
    expect(paneHost.querySelector('#group-add-member-search')).toBeNull()
    const remove = paneHost.querySelector('#gd-delete-btn')
    expect(remove).toBeInstanceOf(HTMLButtonElement)
    expect(remove?.textContent).toContain('Delete group')
    dismissPersonGroups(pane)
    expect(paneHost.querySelector('.group-sidebar-pane--edit')).toBeNull()
  })

  it('reopens an edit from the new baseline instead of a canceled draft', async () => {
    const pane = owner('main')
    pane.ui.personGroupEditing = true
    pane.ui.personGroupEditSession = 1
    pane.ui.personGroupFieldBaseline = {
      groupId: 'G1',
      name: 'Parent Branch',
      description: 'Group description',
      parent_id: '',
      parent_name: '',
    }
    pane.getEntity = () => ({ id: 'G1', name: 'Parent Branch' })
    const paneHost = host()
    const detail = {
      id: 'G1',
      name: 'Parent Branch',
      description: 'Group description',
      parent: null,
      children: [],
      members: [],
    }
    presentPersonGroupDetail({ owner: pane, host: paneHost, group: detail, editing: true, generation: 4 })
    await flushView()
    const name = paneHost.querySelector('#gd-name')
    expect(name).toBeInstanceOf(HTMLInputElement)
    const field = name as HTMLInputElement
    field.value = 'Canceled draft'
    field.dataset.prksGroupDraft = '1'
    pane.ui.personGroupEditSession = 2
    pane.ui.personGroupFieldBaseline = {
      groupId: 'G1',
      name: 'Fresh baseline',
      description: 'Kept description',
      parent_id: '',
      parent_name: '',
    }
    presentPersonGroupDetail({ owner: pane, host: paneHost, group: detail, editing: true, generation: 4 })
    await flushView()
    const reopened = paneHost.querySelector('#gd-name')
    expect(reopened).toBeInstanceOf(HTMLInputElement)
    expect((reopened as HTMLInputElement).value).toBe('Fresh baseline')
    expect(paneHost.querySelector('#gd-description')).toBeInstanceOf(HTMLTextAreaElement)
    expect((paneHost.querySelector('#gd-description') as HTMLTextAreaElement).value).toBe('Kept description')
  })

  it('closes the originating editor when another pane is focused', async () => {
    const closed: Array<string | undefined> = []
    const toggled: Array<string | undefined> = []
    vi.stubGlobal('closePersonGroupEdit', (target?: { tabId?: string }) => {
      closed.push(target?.tabId)
    })
    vi.stubGlobal('prksTogglePersonGroupMembersEdit', (target?: { tabId?: string }) => {
      toggled.push(target?.tabId)
    })
    const pane = owner('origin')
    pane.ui.personGroupEditing = true
    pane.getEntity = () => ({ id: 'G1', name: 'Parent Branch' })
    const paneHost = host()
    presentPersonGroupDetail({
      owner: pane,
      host: paneHost,
      group: {
        id: 'G1',
        name: 'Parent Branch',
        description: '',
        parent: null,
        children: [],
        members: [],
      },
      editing: true,
      generation: 4,
    })
    await flushView()
    const cancel = paneHost.querySelector('[data-prks-group-edit-cancel]')
    const members = paneHost.querySelector('[data-prks-group-members-toggle]')
    expect(cancel?.getAttribute('onclick')).toBeNull()
    expect(members?.getAttribute('onclick')).toBeNull()
    cancel?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    members?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(closed).toEqual(['origin'])
    expect(toggled).toEqual(['origin'])
  })
})
