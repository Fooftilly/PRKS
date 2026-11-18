import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RoleLinkForm from './RoleLinkForm.vue'

type Form = InstanceType<typeof RoleLinkForm>

interface StubWindow {
  allPersons?: unknown[]
  prksSetPersonCatalogue?: (rows: unknown[]) => void
  prksOfflinePeopleFetch?: () => Promise<{ value: unknown[] | null } | null>
  prksOfflineWorksBrowseFetch?: () => Promise<{ value: unknown[] | null } | null>
  fetchPersons?: () => Promise<unknown[]>
  fetchWorks?: () => Promise<unknown[]>
  prksFocusedEntity?: (type: string) => unknown
  prksRefreshRoleCreditPicker?: (prefix: string, person: unknown) => void
  prksBindRoleCreditPicker?: (prefix: string) => void
  prksResolveRoleCreditNameForLink?: (prefix: string, personId: string, searchId: string) => string
  prksLinkPersonFromRoleModal?: (input: unknown) => Promise<unknown>
  prksShowDuplicateRoleLinkAlert?: (roleType: string) => Promise<void>
  prksAlertDialog?: (opts: { title: string; message: string }) => Promise<void>
  prksQuickCreatePersonForSearchField?: (...args: unknown[]) => Promise<unknown>
  closeModals?: () => void
}

const KEYS: Array<keyof StubWindow> = [
  'allPersons',
  'prksSetPersonCatalogue',
  'prksOfflinePeopleFetch',
  'prksOfflineWorksBrowseFetch',
  'fetchPersons',
  'fetchWorks',
  'prksFocusedEntity',
  'prksRefreshRoleCreditPicker',
  'prksBindRoleCreditPicker',
  'prksResolveRoleCreditNameForLink',
  'prksLinkPersonFromRoleModal',
  'prksShowDuplicateRoleLinkAlert',
  'prksAlertDialog',
  'prksQuickCreatePersonForSearchField',
  'closeModals',
]

function stubs(): StubWindow {
  return window as unknown as StubWindow
}

const ADA = { id: 'P1', first_name: 'Ada', last_name: 'Lovelace' }
const GRACE = { id: 'P2', first_name: 'Grace', last_name: 'Hopper' }
const WORK_A = { id: 'W1', title: 'Notes on the Engine' }
const WORK_B = { id: 'W2', title: 'Compilers' }

let wrapper: ReturnType<typeof mount> | null = null
let pickerCalls: Array<[string, unknown]> = []

function form(): Form {
  return wrapper!.vm as unknown as Form
}

function value(id: string): string {
  return (document.getElementById(id) as HTMLInputElement).value
}

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

beforeEach(() => {
  document.body.innerHTML = '<div id="role-modal" class="modal"><div id="role-modal-vue"></div></div>'
  pickerCalls = []
  const w = stubs()
  w.prksSetPersonCatalogue = (rows) => {
    w.allPersons = rows
  }
  w.prksOfflinePeopleFetch = async () => ({ value: [ADA, GRACE] })
  w.prksOfflineWorksBrowseFetch = async () => ({ value: [WORK_A, WORK_B] })
  w.fetchPersons = vi.fn(async () => [])
  w.fetchWorks = vi.fn(async () => [])
  w.prksRefreshRoleCreditPicker = (prefix, person) => {
    pickerCalls.push([prefix, person])
  }
  w.prksBindRoleCreditPicker = vi.fn()
  w.prksResolveRoleCreditNameForLink = () => ''
  w.closeModals = vi.fn()
  window.location.hash = '#/folders'
  wrapper = mount(RoleLinkForm, { attachTo: '#role-modal-vue' })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  for (const key of KEYS) delete stubs()[key]
  window.location.hash = ''
})

describe('Link Person to Work form', () => {
  it('binds the shared credit picker under its own prefix', () => {
    expect(stubs().prksBindRoleCreditPicker).toHaveBeenCalledWith('role-link')
  })

  it('loads both catalogues together and names the Work route it opened on', async () => {
    const starts: string[] = []
    let releasePeople: (v: { value: unknown[] }) => void = () => {}
    let releaseWorks: (v: { value: unknown[] }) => void = () => {}
    stubs().prksOfflinePeopleFetch = () => {
      starts.push('people')
      return new Promise((r) => (releasePeople = r))
    }
    stubs().prksOfflineWorksBrowseFetch = () => {
      starts.push('works')
      return new Promise((r) => (releaseWorks = r))
    }
    window.location.hash = '#/works/W1'
    let settled = false
    const pending = form()
      .prepare(() => true)
      .then(() => (settled = true))
    expect(starts).toEqual(['people', 'works'])
    releasePeople({ value: [ADA] })
    await flush()
    expect(settled).toBe(false)
    releaseWorks({ value: [WORK_A] })
    await pending
    expect(value('role-work-id')).toBe('W1')
    expect(value('role-work-search')).toBe('Notes on the Engine')
    expect(stubs().allPersons).toEqual([ADA])
    expect(stubs().fetchPersons).not.toHaveBeenCalled()
  })

  it('falls back to the plain reads and to the focused Work', async () => {
    stubs().prksOfflinePeopleFetch = async () => ({ value: null })
    stubs().prksOfflineWorksBrowseFetch = async () => ({ value: null })
    stubs().fetchPersons = vi.fn(async () => [ADA])
    stubs().fetchWorks = vi.fn(async () => [WORK_B])
    stubs().prksFocusedEntity = () => ({ id: 'W1', title: 'Focused Work' })
    await form().prepare(() => true)
    expect(stubs().fetchPersons).toHaveBeenCalledTimes(1)
    expect(stubs().fetchWorks).toHaveBeenCalledTimes(1)
    expect(value('role-work-id')).toBe('W1')
    expect(value('role-work-search')).toBe('Focused Work')
  })

  it('names the Person route without inventing a File', async () => {
    window.location.hash = '#/people/P2'
    await form().prepare(() => true)
    expect(value('role-person-id')).toBe('P2')
    expect(value('role-person-search')).toBe('Grace Hopper')
    expect(pickerCalls.at(-1)).toEqual(['role-link', GRACE])
    expect(value('role-work-id')).toBe('')
  })

  it('a newer opening drops an older load', async () => {
    let release: (v: { value: unknown[] }) => void = () => {}
    stubs().prksOfflineWorksBrowseFetch = () => new Promise((r) => (release = r))
    window.location.hash = '#/works/W1'
    const older = form().prepare(() => true)
    stubs().prksOfflineWorksBrowseFetch = async () => ({ value: [WORK_B] })
    window.location.hash = '#/folders'
    await form().prepare(() => true)
    release({ value: [WORK_A] })
    await older
    expect(value('role-work-id')).toBe('')
  })

  it('picks a person and a file from their lists', async () => {
    await form().prepare(() => true)
    const person = document.getElementById('role-person-search') as HTMLInputElement
    person.value = 'gra'
    person.dispatchEvent(new Event('input'))
    await flush()
    const personRows = document.querySelectorAll('#role-person-results .result-item--person-pick')
    expect([...personRows].map((row) => row.textContent?.trim())).toContain('Grace Hopper')
    expect(document.querySelector('#role-person-results .result-item--create')?.textContent).toContain('"gra"')
    const grace = [...personRows].find((row) => row.textContent?.includes('Grace'))!
    grace.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flush()
    expect(value('role-person-id')).toBe('P2')
    expect(pickerCalls.at(-1)).toEqual(['role-link', GRACE])

    const work = document.getElementById('role-work-search') as HTMLInputElement
    work.value = 'comp'
    work.dispatchEvent(new Event('input'))
    await flush()
    const rows = document.querySelectorAll('#role-work-results .result-item:not(.no-results)')
    expect([...rows].map((row) => row.textContent?.trim())).toEqual(['Compilers'])
    rows[0].dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flush()
    expect(value('role-work-id')).toBe('W2')
    expect(value('role-work-search')).toBe('Compilers')
  })

  it('typing clears the picked id', async () => {
    await form().prepare(() => true)
    ;(document.getElementById('role-person-id') as HTMLInputElement).value = 'P1'
    const person = document.getElementById('role-person-search') as HTMLInputElement
    person.value = 'Ad'
    person.dispatchEvent(new Event('input'))
    expect(value('role-person-id')).toBe('')
  })

  it('the role is a radio group mirrored into the hidden field', async () => {
    await form().prepare(() => true)
    expect(value('role-type')).toBe('Author')
    const group = document.querySelector<HTMLElement>('#role-role-seg-mount [role="radiogroup"]')!
    const buttons = group.querySelectorAll<HTMLButtonElement>('.prks-segmented__btn')
    expect([...buttons].map((b) => b.dataset.value)).toEqual([
      'Author',
      'Editor',
      'Reviewer',
      'Mentioned',
      'Translator',
      'Introduction',
      'Foreword',
      'Afterword',
    ])
    group.querySelector<HTMLButtonElement>('[data-value="Reviewer"]')!.click()
    await nextTick()
    expect(value('role-type')).toBe('Reviewer')
    expect(group.querySelector('[data-value="Reviewer"]')?.getAttribute('aria-checked')).toBe('true')
    group.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    await nextTick()
    expect(value('role-type')).toBe('Mentioned')
    group.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))
    await nextTick()
    expect(value('role-type')).toBe('Author')
  })

  it('asks for both ends before linking', async () => {
    const link = vi.fn()
    stubs().prksLinkPersonFromRoleModal = link
    await form().prepare(() => true)
    ;(document.getElementById('save-role-btn') as HTMLButtonElement).click()
    await flush()
    expect(link).not.toHaveBeenCalled()
    expect(document.getElementById('role-link-error')?.textContent).toBe('Choose a person and a file.')
  })

  it('links with the picked ids, role and credit, then closes and settles', async () => {
    const settle = vi.fn()
    const link = vi.fn(async () => ({ code: 'saved', maySettle: () => true, settle }))
    stubs().prksLinkPersonFromRoleModal = link
    stubs().prksResolveRoleCreditNameForLink = (prefix, personId, searchId) =>
      prefix === 'role-link' && personId === 'P1' && searchId === 'role-person-search' ? ' Countess ' : ''
    await form().prepare(() => true)
    // E2E fills the hidden ids directly, as the classic dialog allowed.
    ;(document.getElementById('role-person-id') as HTMLInputElement).value = 'P1'
    ;(document.getElementById('role-work-id') as HTMLInputElement).value = 'W1'
    document.querySelector<HTMLButtonElement>('#role-role-seg-mount [data-value="Editor"]')!.click()
    await nextTick()
    ;(document.getElementById('save-role-btn') as HTMLButtonElement).click()
    await flush()
    expect(link).toHaveBeenCalledWith({ person_id: 'P1', work_id: 'W1', role_type: 'Editor', credit_name: 'Countess' })
    expect(stubs().closeModals).toHaveBeenCalledTimes(1)
    expect(settle).toHaveBeenCalledTimes(1)
  })

  it('an older failure neither alerts nor frees a newer opening', async () => {
    let release: (v: unknown) => void = () => {}
    stubs().prksLinkPersonFromRoleModal = vi.fn(
      () => new Promise((r) => (release = r)),
    ) as unknown as StubWindow['prksLinkPersonFromRoleModal']
    const alerts: string[] = []
    stubs().prksAlertDialog = async (opts) => {
      alerts.push(opts.message)
    }
    await form().prepare(() => true)
    ;(document.getElementById('role-person-id') as HTMLInputElement).value = 'P1'
    ;(document.getElementById('role-work-id') as HTMLInputElement).value = 'W1'
    const button = document.getElementById('save-role-btn') as HTMLButtonElement
    button.click()
    await flush()
    expect(button.disabled).toBe(true)
    expect(button.textContent).toContain('Linking')
    // A newer opening starts its own Save.
    await form().prepare(() => true)
    expect(button.disabled).toBe(false)
    ;(document.getElementById('role-person-id') as HTMLInputElement).value = 'P1'
    ;(document.getElementById('role-work-id') as HTMLInputElement).value = 'W1'
    const olderRelease = release
    button.click()
    await flush()
    expect(button.disabled).toBe(true)
    olderRelease({ code: 'failed', maySettle: () => false })
    await flush()
    expect(alerts).toEqual([])
    expect(stubs().closeModals).not.toHaveBeenCalled()
    expect(button.disabled).toBe(true)
  })

  it('quick-creates the typed person through the shared helper', async () => {
    const create = vi.fn(async () => undefined)
    stubs().prksQuickCreatePersonForSearchField = create
    await form().prepare(() => true)
    const person = document.getElementById('role-person-search') as HTMLInputElement
    person.value = 'New Person'
    person.dispatchEvent(new Event('input'))
    await flush()
    document
      .querySelector('#role-person-results .result-item--create')!
      .dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flush()
    expect(create).toHaveBeenCalledWith(
      'New Person',
      'role-person-search',
      'role-person-id',
      'Quick-created from Link Person to Work',
      expect.objectContaining({ stillApplies: expect.any(Function) }),
    )
  })

  it('focuses Person without opening its list, and Escape closes an open list first', async () => {
    await form().prepare(() => true)
    form().focusInitial()
    expect(document.activeElement?.id).toBe('role-person-search')
    expect(document.getElementById('role-person-results')?.classList.contains('hidden')).toBe(true)
    expect(form().closeOpenList()).toBe(false)
    const work = document.getElementById('role-work-search') as HTMLInputElement
    work.dispatchEvent(new Event('focus'))
    await flush()
    expect(document.getElementById('role-work-results')?.classList.contains('hidden')).toBe(false)
    expect(form().closeOpenList()).toBe(true)
    await nextTick()
    expect(document.getElementById('role-work-results')?.classList.contains('hidden')).toBe(true)
  })
})
