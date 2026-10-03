import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissPeople,
  PEOPLE_RETAIN_SURFACE_KEY,
  presentPeopleIndex,
  presentPersonDetail,
  resetPeopleSessionForTests,
} from './session'

afterEach(() => {
  resetPeopleSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissPeople
  delete window.openModal
  delete window.prksNavigate
  delete window.openPersonProfileEdit
  delete window.closePersonProfileEdit
  delete window.savePersonProfileDraft
  delete window.deletePerson
  delete window.prksTogglePersonWorksEdit
  delete window.prksRemoveWorkRoleLink
  delete window.prksBindPersonProfileDraft
  delete window.prksTabContextIsFocused
  delete window.prksIcon
  delete window.prksPaintScopeHost
  delete window.prksOpenNewPersonModalFromPeoplePage
  delete window.prksWorkCardHtml
  sessionStorage.removeItem('prks-people-library-filter')
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

function owner(tabId = 'tab') {
  return {
    tabId,
    isCurrent: () => true,
    lastResolvedRoute: { name: 'person' as const, params: { personId: 'P1' } },
    ui: {
      personDetailEditing: false,
      personEditSession: 0,
      personProfileDraft: null as null | Record<string, unknown>,
      personWorksEditing: false,
    },
    getEntity: () => ({ id: 'P1', groups: [] as { id: string; name: string }[] }),
    setEntity: () => undefined,
  }
}

const ada = {
  id: 'P1',
  first_name: 'Ada',
  last_name: 'Lovelace',
  aliases: 'A. Lovelace',
  about: 'Mathematician and writer',
  birth_date: '1815',
  death_date: '1852',
  assigned_roles: ['Author'],
  groups: [{ id: 'G1', name: 'Analysts' }],
}

const grace = {
  id: 'P2',
  first_name: 'Grace',
  last_name: 'Hopper',
  aliases: '',
  about: 'Compiler',
  birth_date: '',
  death_date: '',
  assigned_roles: ['Reviewer'],
  groups: [],
}

describe('People route surface', () => {
  it('renders ready, empty, and unavailable indexes independently', () => {
    window.openModal = vi.fn()
    window.prksOpenNewPersonModalFromPeoplePage = vi.fn()
    const main = owner('main')
    const secondary = owner('side')
    const mainHost = host()
    const sideHost = host()
    presentPeopleIndex({
      owner: main,
      host: mainHost,
      items: [ada],
      generation: 4,
      shell: true,
    })
    presentPeopleIndex({
      owner: secondary,
      host: sideHost,
      items: [],
      generation: 1,
      shell: false,
    })
    expect(mainHost.textContent).toContain('Ada Lovelace')
    expect(mainHost.textContent).toContain('Mathematician and writer')
    expect(mainHost.querySelector('#prks-people-header-new')?.className).toContain('prks-btn--primary')
    expect(mainHost.querySelector('#prks-people-empty-new')).toBeNull()
    expect(sideHost.textContent).toContain('No people yet.')
    expect(sideHost.querySelector('#prks-people-empty-new')?.getAttribute('data-prks-role')).toBe(
      'person-create-control',
    )
    expect(sideHost.querySelector('#prks-people-header-new')?.className).toContain('prks-btn--secondary')
    expect(sideHost.querySelector('#prks-people-header-new')?.getAttribute('data-prks-role')).toBeNull()
    sideHost.querySelector<HTMLButtonElement>('#prks-people-empty-new')?.click()
    expect(window.prksOpenNewPersonModalFromPeoplePage).toHaveBeenCalledWith(secondary)
    expect(window.openModal).not.toHaveBeenCalled()
    expect(readRouteSurface(main)?.ownsMainShell).toBe(true)
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(readRouteSurface(secondary)?.canonicalHash).toBe('#/people')

    presentPeopleIndex({
      owner: main,
      host: mainHost,
      availability: 'unavailable',
      items: [ada],
      generation: 5,
    })
    expect(mainHost.querySelector('[data-prks-role="offline-unavailable"]')?.textContent).toContain(
      'This list has not been cached on this device.',
    )
    expect(mainHost.textContent).toContain('People not available offline')
    expect(mainHost.textContent).not.toContain('Ada Lovelace')
    expect(sideHost.textContent).toContain('No people yet.')
  })

  it('filters a role view from the same collection and keeps search local', async () => {
    window.prksNavigate = vi.fn()
    const pane = owner()
    const el = host()
    presentPeopleIndex({
      owner: pane,
      host: el,
      items: [ada, grace],
      roleFilter: 'Author',
      generation: 2,
    })
    expect(el.textContent).toContain('Authors')
    expect(el.textContent).toContain('Ada Lovelace')
    expect(el.textContent).not.toContain('Grace Hopper')
    expect(el.textContent).toContain('Analysts')
    const search = el.querySelector<HTMLInputElement>('#prks-people-library-search')
    expect(search).not.toBeNull()
    if (!search) return
    search.value = 'nope'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(el.textContent).toContain('No people match your search.')
    expect(sessionStorage.getItem('prks-people-library-filter')).toBe('nope')
    search.value = ''
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    presentPeopleIndex({
      owner: pane,
      host: el,
      items: [ada, grace],
      roleFilter: 'Reviewer',
      generation: 3,
    })
    await nextTick()
    expect(el.textContent).toContain('Grace Hopper')
    expect(el.textContent).not.toContain('Ada Lovelace')
    expect(readRouteSurface(pane)?.canonicalHash).toBe('#/people/role/Reviewer')
  })

  it('shows an unknown role without a second list', () => {
    const pane = owner()
    const el = host()
    presentPeopleIndex({
      owner: pane,
      host: el,
      unknownRole: true,
      items: [ada],
      roleFilter: 'NotARole',
      generation: 1,
    })
    expect(el.textContent).toContain('Unknown role filter.')
    expect(el.textContent).not.toContain('Ada Lovelace')
  })

  it('activates an index row on Enter', () => {
    const pane = owner('main')
    const el = host()
    presentPeopleIndex({ owner: pane, host: el, items: [ada], generation: 1 })
    const row = el.querySelector<HTMLElement>('[data-prks-route="#/people/P1"]')
    expect(row?.getAttribute('role')).toBe('link')
    expect(row?.getAttribute('tabindex')).toBe('0')
    const clicks = vi.fn()
    row?.addEventListener('click', clicks)
    row?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    expect(clicks).toHaveBeenCalledOnce()
  })

  it('renders detail states from the projection', () => {
    const pane = owner()
    const el = host()
    presentPersonDetail({
      owner: pane,
      host: el,
      person: {
        ...ada,
        death_date: '',
        works: [{ id: 'W1', title: 'Notes', role_type: 'Author', order_index: '0', subtitle: 'Author' }],
        link_wikipedia: 'https://example.test/wiki',
      },
      editing: false,
      generation: 1,
    })
    expect(el.textContent).toContain('Ada Lovelace')
    expect(el.textContent).toContain('Born 1815')
    expect(el.textContent).toContain('Mathematician and writer')
    expect(el.textContent).toContain('Analysts')
    expect(el.textContent).toContain('Notes')
    expect(el.querySelector('[data-prks-role="person-group-link"]')?.getAttribute('href')).toBe('#/people/groups/G1')
    expect(el.querySelector('img.person-portrait')).toBeNull()

    presentPersonDetail({
      owner: pane,
      host: el,
      availability: 'unavailable',
      person: ada,
      personId: 'P1',
      generation: 2,
    })
    expect(el.textContent).toContain('Person not available offline')
    expect(el.textContent).not.toContain('Mathematician')

    presentPersonDetail({
      owner: pane,
      host: el,
      availability: 'not-found',
      person: null,
      personId: 'missing',
      generation: 3,
    })
    expect(el.textContent).toContain('Person not found')
    expect(el.textContent).not.toContain('not available offline')
  })

  it('saves only fields that differ from the edit baseline', async () => {
    const saved: unknown[] = []
    window.savePersonProfileDraft = vi.fn(async (...args) => {
      saved.push(args)
      return { ok: true, message: '' }
    })
    window.prksBindPersonProfileDraft = vi.fn()
    const pane = owner()
    pane.ui.personDetailEditing = true
    const el = host()
    presentPersonDetail({
      owner: pane,
      host: el,
      person: ada,
      editing: true,
      generation: 1,
    })
    await flushView()
    const first = el.querySelector<HTMLInputElement>('#pd-first-name')
    const about = el.querySelector<HTMLTextAreaElement>('#pd-about')
    expect(first?.value).toBe('Ada')
    expect(about?.value).toBe('Mathematician and writer')
    if (!first) throw new Error('editor missing')
    first.value = 'Augusta'
    first.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    presentPersonDetail({
      owner: pane,
      host: el,
      person: { ...ada, about: 'Refreshed biography' },
      editing: true,
      generation: 2,
    })
    await flushView()
    expect(el.querySelector<HTMLInputElement>('#pd-first-name')?.value).toBe('Augusta')
    expect(el.querySelector<HTMLTextAreaElement>('#pd-about')?.value).toBe('Mathematician and writer')
    el.querySelector<HTMLButtonElement>('#pd-save-btn')?.click()
    await flushView()
    expect(window.savePersonProfileDraft).toHaveBeenCalledOnce()
    const draft = saved[0] as unknown[]
    expect(draft[2]).toMatchObject({ first_name: 'Augusta', about: 'Mathematician and writer' })
    expect(draft[3]).toMatchObject({ first_name: 'Ada', about: 'Mathematician and writer' })
    expect(draft[7]).toBe(2)
  })

  it('keeps a failed save and ignores an older save after cancel and reopen', async () => {
    let release: ((value: { ok: boolean; message: string }) => void) | undefined
    window.savePersonProfileDraft = vi.fn(
      () =>
        new Promise<{ ok: boolean; message: string }>((resolve) => {
          release = resolve
        }),
    )
    window.prksBindPersonProfileDraft = vi.fn()
    const pane = owner()
    pane.ui.personDetailEditing = true
    const el = host()
    window.closePersonProfileEdit = () => {
      pane.ui.personEditSession += 1
      pane.ui.personDetailEditing = false
      pane.ui.personProfileDraft = null
    }
    presentPersonDetail({ owner: pane, host: el, person: ada, editing: true, generation: 1 })
    await flushView()
    const first = el.querySelector<HTMLInputElement>('#pd-first-name')
    if (!first) throw new Error('editor missing')
    first.value = 'Augusta'
    first.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    el.querySelector<HTMLButtonElement>('#pd-save-btn')?.click()
    await flushView()
    expect(el.querySelector('#pd-save-btn')?.textContent).toContain('Saving…')

    el.querySelector<HTMLButtonElement>('[data-prks-person-cancel]')?.click()
    presentPersonDetail({ owner: pane, host: el, person: ada, editing: false, generation: 2 })
    await nextTick()
    pane.ui.personDetailEditing = true
    presentPersonDetail({ owner: pane, host: el, person: ada, editing: true, generation: 3 })
    await flushView()
    const reopened = el.querySelector<HTMLInputElement>('#pd-first-name')
    expect(reopened?.value).toBe('Ada')
    if (!reopened) return
    reopened.value = 'Later'
    reopened.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    release?.({ ok: true, message: '' })
    await flushView()
    expect(el.querySelector<HTMLInputElement>('#pd-first-name')?.value).toBe('Later')
    expect(el.querySelector('[data-prks-role="person-save-status"]')).toBeNull()
    expect(el.querySelector('.person-panel-edit')).not.toBeNull()
  })

  it('does not let a save for person A settle person B', async () => {
    let release: ((value: { ok: boolean; message: string }) => void) | undefined
    window.savePersonProfileDraft = vi.fn(
      () =>
        new Promise<{ ok: boolean; message: string }>((resolve) => {
          release = resolve
        }),
    )
    window.prksBindPersonProfileDraft = vi.fn()
    const pane = owner()
    pane.ui.personDetailEditing = true
    const el = host()
    presentPersonDetail({ owner: pane, host: el, person: ada, editing: true, generation: 1 })
    await flushView()
    const first = el.querySelector<HTMLInputElement>('#pd-first-name')
    if (!first) throw new Error('editor missing')
    first.value = 'Augusta'
    first.dispatchEvent(new Event('input', { bubbles: true }))
    el.querySelector<HTMLButtonElement>('#pd-save-btn')?.click()
    await flushView()
    presentPersonDetail({
      owner: pane,
      host: el,
      person: grace,
      personId: 'P2',
      editing: true,
      generation: 2,
    })
    await flushView()
    expect(el.querySelector<HTMLInputElement>('#pd-first-name')?.value).toBe('Grace')
    release?.({ ok: false, message: 'Could not save this profile.' })
    await flushView()
    expect(el.textContent).not.toContain('Could not save this profile.')
    expect(el.querySelector('[data-person-edit-id]')?.getAttribute('data-person-edit-id')).toBe('P2')
  })

  it('keeps linked work navigation open beside the profile editor', async () => {
    const cards: { work: Record<string, unknown>; subtitle?: string }[] = []
    window.prksWorkCardHtml = vi.fn((work, options) => {
      cards.push({ work: work as Record<string, unknown>, subtitle: options?.subtitle })
      const id = String((work as { id?: unknown }).id || '')
      return `<a data-prks-route="#/works/${id}">${String((work as { title?: unknown }).title || '')}</a>`
    })
    window.prksBindPersonProfileDraft = vi.fn()
    const pane = owner()
    pane.ui.personDetailEditing = true
    const el = host()
    presentPersonDetail({
      owner: pane,
      host: el,
      person: {
        ...ada,
        works: [
          {
            id: 'W1',
            title: 'Notes',
            role_type: 'Author',
            order_index: '0',
            file_path: '/api/pdfs/notes.pdf',
            status: 'read',
            year: '1843',
            file_size_bytes: 1200,
          },
        ],
      },
      editing: true,
      generation: 1,
    })
    await flushView()
    expect(el.querySelector('.person-panel-edit')).not.toBeNull()
    expect(el.querySelectorAll('[data-prks-route^="#/works/"]').length).toBeGreaterThan(0)
    expect(el.querySelector('#prks-person-delete-btn')).toBeNull()
    expect(el.querySelector('.person-profile__card-unlink')).toBeNull()
    expect(cards[0]?.work).toMatchObject({
      id: 'W1',
      file_path: '/api/pdfs/notes.pdf',
      status: 'read',
      year: '1843',
      file_size_bytes: 1200,
    })
  })

  it('names the file and role on each unlinkable relationship', async () => {
    window.prksWorkCardHtml = vi.fn((work, options) => {
      const id = String((work as { id?: unknown }).id || '')
      const subtitle = options?.subtitle || ''
      return `<a data-prks-route="#/works/${id}"><span class="work-card-subtitle">${subtitle}</span></a>`
    })
    window.prksBindPersonProfileDraft = vi.fn()
    const pane = owner()
    pane.ui.personDetailEditing = true
    const el = host()
    presentPersonDetail({
      owner: pane,
      host: el,
      person: {
        ...ada,
        works: [
          { id: 'W1', title: 'Notes', role_type: 'Author', order_index: '0', subtitle: 'Author' },
          { id: 'W1', title: 'Notes', role_type: 'Editor', order_index: '1', subtitle: 'Editor' },
        ],
      },
      editing: true,
      worksEditing: true,
      generation: 1,
    })
    await flushView()
    expect(el.querySelector('[aria-label="Remove link to Notes (Author)"]')).not.toBeNull()
    expect(el.querySelector('[aria-label="Remove link to Notes (Editor)"]')).not.toBeNull()
    const subtitles = Array.from(el.querySelectorAll('.work-card-subtitle')).map((node) => node.textContent)
    expect(subtitles).toEqual(['Author', 'Editor'])
    expect(el.querySelectorAll('[data-prks-route^="#/works/"]').length).toBe(2)
  })

  it('keeps the index host across a retained refresh and unmounts a failed one', async () => {
    const cleanups = new Set<() => void>()
    const pane: {
      tabId: string
      isCurrent: () => boolean
      registerCleanup: (fn: () => void) => () => void
      [PEOPLE_RETAIN_SURFACE_KEY]?: boolean
    } = {
      tabId: 'coord',
      isCurrent: () => true,
      registerCleanup(fn) {
        cleanups.add(fn)
        return () => {
          cleanups.delete(fn)
        }
      },
    }
    const contentDiv = document.createElement('div')
    document.body.appendChild(contentDiv)
    const routeHost = document.createElement('div')
    routeHost.setAttribute('data-prks-vue-route-host', 'true')
    contentDiv.appendChild(routeHost)
    presentPeopleIndex({ owner: pane, host: routeHost, items: [ada], generation: 1 })
    expect(routeHost.querySelector('[data-prks-people-index-view]')).not.toBeNull()

    pane[PEOPLE_RETAIN_SURFACE_KEY] = true
    const drained = Array.from(cleanups)
    cleanups.clear()
    drained.forEach((fn) => fn())
    pane[PEOPLE_RETAIN_SURFACE_KEY] = false
    expect(cleanups.size).toBe(1)
    expect(routeHost.textContent).toContain('Ada Lovelace')

    presentPeopleIndex({
      owner: pane,
      host: routeHost,
      items: [{ ...ada, first_name: 'Augusta' }],
      generation: 2,
    })
    await nextTick()
    expect(routeHost.textContent).toContain('Augusta Lovelace')

    pane[PEOPLE_RETAIN_SURFACE_KEY] = true
    const failed = Array.from(cleanups)
    cleanups.clear()
    failed.forEach((fn) => fn())
    pane[PEOPLE_RETAIN_SURFACE_KEY] = false
    dismissPeople(pane)
    expect(routeHost.querySelector('[data-prks-people-index-view]')).toBeNull()
    expect(readRouteSurface(pane)?.mounted).toBe(false)
    contentDiv.innerHTML = '<p><button type="button" id="prks-route-retry">Retry</button></p>'
    expect(contentDiv.querySelector('#prks-route-retry')).not.toBeNull()
    expect(document.body.querySelector('[data-prks-people-index-view]')).toBeNull()
  })

  it('unmounts one owner without affecting the other', () => {
    const a = owner('a')
    const b = owner('b')
    const aHost = host()
    const bHost = host()
    presentPeopleIndex({ owner: a, host: aHost, items: [ada], generation: 1 })
    presentPersonDetail({ owner: b, host: bHost, person: grace, personId: 'P2', generation: 1 })
    dismissPeople(a)
    expect(aHost.querySelector('[data-prks-people-index-view]')).toBeNull()
    expect(bHost.textContent).toContain('Grace Hopper')
  })
})
