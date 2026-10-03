import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readRouteSurface } from '../../route-surface/lifecycle'
import {
  dismissPublishers,
  presentPublishers,
  registerPublishersBridge,
  reportPublishersRefreshFailure,
  resetPublishersSessionForTests,
} from './session'

afterEach(() => {
  resetPublishersSessionForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissPublishers
  delete window.prksVueClosePublishersAliasModal
  delete window.prksVueReportPublishersRefreshFailure
  delete window.prksIcon
  delete window.prksTagPlusIconHtml
  delete window.prksRefreshIcons
  delete window.fetchPublishersInUse
  delete window.prksAlertMessage
  delete window.prksPublishersCreate
  delete window.prksPublishersAddAlias
  delete window.prksPublishersRemoveAlias
  delete window.prksPublishersDelete
  delete window.prksReloadPublishersPage
  delete window.prksConfirmDestructive
})

async function flush(): Promise<void> {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function owner() {
  return {
    isCurrent: () => true,
    lastResolvedRoute: { name: 'publishers' },
  }
}

const OUP = {
  id: 'p1',
  name: 'Oxford University Press',
  work_count: 2,
  aliases: ['OUP'],
}
const CUP = {
  id: 'p2',
  name: 'Cambridge',
  work_count: 1,
  aliases: [],
}

describe('Publishers route bridge', () => {
  it('paints each owner from the coordinator list and does not fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    window.fetchPublishersInUse = vi.fn()
    window.prksIcon = () => '<i data-lucide="building-2"></i>'
    window.prksTagPlusIconHtml = () => '<span class="tag-add-shell__icon"></span>'
    registerPublishersBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentPublishers({
      owner: main,
      host: mainHost,
      publishers: [OUP, CUP],
      generation: 3,
      shell: true,
    })
    presentPublishers({
      owner: secondary,
      host: secondaryHost,
      publishers: [{ id: 'side', name: 'Side', work_count: 1, aliases: [] }],
      generation: 1,
      shell: false,
    })
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Publishers')
    expect(mainHost.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
    expect(mainHost.querySelector('[data-prks-route="#/search?publisher=Oxford%20University%20Press"]')).not.toBeNull()
    expect(mainHost.querySelector('.publishers-page__list-stats')?.textContent).toBe('2 files · 1 alias')
    expect(mainHost.querySelector('[data-publisher-alias-edit="side"]')).toBeNull()
    expect(secondaryHost.querySelector('[data-publisher-alias-edit="side"]')).not.toBeNull()
    expect(secondaryHost.querySelector('[data-publisher-alias-edit="p1"]')).toBeNull()
    expect(mainHost.querySelector('#publishers-page-new-name')).not.toBeNull()
    expect(readRouteSurface(main)).toMatchObject({
      name: 'publishers',
      canonicalHash: '#/publishers',
      ownsMainShell: true,
    })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(window.fetchPublishersInUse).not.toHaveBeenCalled()

    mainHost.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p1"]')?.click()
    await nextTick()
    expect(mainHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Oxford University Press')
    expect(mainHost.querySelector('[data-publisher-alias-remove="OUP"]')).not.toBeNull()
    expect(secondaryHost.querySelector('#publishers-page-alias-modal')).toBeNull()
    window.prksVueClosePublishersAliasModal?.()
    await nextTick()
    expect(mainHost.querySelector('#publishers-page-alias-modal')).toBeNull()
  })

  it('reopens only the resumed alias dialog and ignores a stale generation', async () => {
    const pane = owner()
    const el = host()
    presentPublishers({ owner: pane, host: el, publishers: [], generation: 2 })
    expect(el.querySelector('.publishers-page__empty')?.textContent).toContain('No publisher groups yet')
    dismissPublishers(pane)
    presentPublishers({
      owner: pane,
      host: el,
      publishers: [OUP],
      generation: 2,
      resume: { aliasPublisherId: 'p1' },
    })
    await nextTick()
    expect(el.querySelector('#publishers-page-delete-btn')).toBeNull()
    presentPublishers({
      owner: pane,
      host: el,
      publishers: [OUP, CUP],
      generation: 3,
      resume: { aliasPublisherId: 'p1' },
    })
    expect(el.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Oxford University Press')
    expect(el.querySelector('#publishers-page-delete-btn')).not.toBeNull()
    const ids = [...el.querySelectorAll('[data-publisher-alias-edit]')].map((node) =>
      node.getAttribute('data-publisher-alias-edit'),
    )
    expect(ids).toEqual(['p1', 'p2'])
  })

  it('registers the bridge and paints the host that stored the request', () => {
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    const pane = owner()
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'publishers',
      owner: pane,
      host: decoy,
      publishers: [OUP],
      generation: 1,
      shell: true,
    }
    registerPublishersBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksVueDismissPublishers).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    expect(el.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-publishers-page]')).toBeNull()
  })

  it('dismisses one owner and leaves the other mounted', () => {
    registerPublishersBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentRoute?.({
      feature: 'publishers',
      owner: main,
      host: mainHost,
      publishers: [OUP],
      generation: 2,
      shell: true,
    })
    window.prksVuePresentRoute?.({
      feature: 'publishers',
      owner: secondary,
      host: secondaryHost,
      publishers: [CUP],
      generation: 1,
      shell: false,
      resume: { aliasPublisherId: 'p2' },
    })
    expect(secondaryHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
    window.prksVueDismissPublishers?.(main)
    expect(mainHost.querySelector('[data-prks-publishers-page]')).toBeNull()
    expect(secondaryHost.querySelector('[data-publisher-alias-edit="p2"]')).not.toBeNull()
    expect(secondaryHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
  })

  it('opens files from a click on the publisher name, not a nested control', () => {
    const el = host()
    presentPublishers({ owner: owner(), host: el, publishers: [OUP], generation: 1 })
    const nameText = [...el.querySelectorAll('span')].find((node) => node.textContent === 'Oxford University Press')
    expect(nameText).toBeTruthy()
    nameText!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    const route = nameText!.closest('[data-prks-route]')
    const interactive = nameText!.closest('button, [role="button"]')
    expect(route).toBe(interactive)
    expect(route?.getAttribute('role')).toBe('button')
    expect(route?.getAttribute('data-prks-route')).toBe('#/search?publisher=Oxford%20University%20Press')
    expect(route?.getAttribute('data-prks-middleclick-nav')).toBe('1')
    expect(route?.classList.contains('publishers-page__list-main')).toBe(true)
    const alias = el.querySelector('[data-publisher-alias-edit="p1"]')
    expect(route?.contains(alias)).toBe(false)
    expect(alias?.closest('[data-prks-route]')).toBeNull()
    expect(el.querySelector('.publishers-page__list-item')?.hasAttribute('data-prks-route')).toBe(false)
  })

  it('returns focus to the alias button after a resumed dialog closes', async () => {
    const el = host()
    presentPublishers({
      owner: owner(),
      host: el,
      publishers: [OUP],
      generation: 3,
      resume: { aliasPublisherId: 'p1' },
    })
    await nextTick()
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    expect(document.activeElement).toBe(el.querySelector('[data-publisher-alias-edit="p1"]'))
  })

  it('keeps the publisher list when a refresh fails and shows that failure', async () => {
    const pane = owner()
    const el = host()
    presentPublishers({ owner: pane, host: el, publishers: [OUP, CUP], generation: 4 })
    reportPublishersRefreshFailure(pane, 'Could not refresh publishers.')
    await nextTick()
    expect(el.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
    expect(el.querySelector('.publishers-page__empty')).toBeNull()
    expect(el.querySelector('[data-publishers-refresh-error]')?.textContent).toContain('Could not refresh publishers.')
  })

  it('shows create, alias, and delete failures locally and stays quiet for a no-op', async () => {
    const alert = vi.fn()
    window.prksAlertMessage = alert
    window.prksPublishersCreate = async () => {
      throw new Error('Could not add publisher.')
    }
    window.prksPublishersAddAlias = async () => {
      throw new Error('Duplicate alias')
    }
    window.prksPublishersRemoveAlias = async () => {
      throw new Error('Alias is in use')
    }
    window.prksConfirmDestructive = async () => true
    window.prksPublishersDelete = async () => {
      throw new Error('Could not delete publisher.')
    }
    const el = host()
    presentPublishers({ owner: owner(), host: el, publishers: [OUP, CUP], generation: 5 })
    const name = el.querySelector<HTMLInputElement>('#publishers-page-new-name')
    name!.value = 'New Press'
    name!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#publishers-page-add-btn')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-create-error]')?.textContent).toContain('Could not add publisher.')
    expect(alert).not.toHaveBeenCalled()

    el.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#publishers-page-alias-input')
    input!.value = 'Oxford'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-alias-add-error]')?.textContent).toContain('Duplicate alias')

    el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-alias-remove-error]')?.textContent).toContain('Alias is in use')

    const deleteBtn = el.querySelector<HTMLButtonElement>('#publishers-page-delete-btn')
    expect(deleteBtn?.classList.contains('prks-btn--danger')).toBe(true)
    deleteBtn?.click()
    await flush()
    expect(el.querySelector('[data-publishers-delete-error]')?.textContent).toContain('Could not delete publisher.')
    expect(el.querySelector('#publishers-page-alias-modal')).not.toBeNull()
    expect(alert).not.toHaveBeenCalled()

    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    expect(el.querySelector('[data-publishers-alias-add-error]')).toBeNull()

    window.prksPublishersAddAlias = async () => ({ ok: false, reason: 'offline' })
    el.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p1"]')?.click()
    await nextTick()
    const again = el.querySelector<HTMLInputElement>('#publishers-page-alias-input')
    again!.value = 'Quiet'
    again!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-alias-add-error]')).toBeNull()
    expect(alert).not.toHaveBeenCalled()
  })

  it('marks create, alias, and delete busy while the write is in flight', async () => {
    let releaseCreate: () => void = () => {}
    let releaseAdd: () => void = () => {}
    let releaseRemove: () => void = () => {}
    let releaseDelete: () => void = () => {}
    window.prksPublishersCreate = () => new Promise((resolve) => {
      releaseCreate = () => resolve({ ok: true })
    })
    window.prksPublishersAddAlias = () => new Promise((resolve) => {
      releaseAdd = () => resolve({ ok: true })
    })
    window.prksPublishersRemoveAlias = () => new Promise((resolve) => {
      releaseRemove = () => resolve({ ok: true })
    })
    window.prksConfirmDestructive = async () => true
    window.prksPublishersDelete = () => new Promise((resolve) => {
      releaseDelete = () => resolve({ ok: true })
    })
    window.prksReloadPublishersPage = async () => false
    const el = host()
    presentPublishers({ owner: owner(), host: el, publishers: [OUP, CUP], generation: 6 })
    const name = el.querySelector<HTMLInputElement>('#publishers-page-new-name')
    name!.value = 'New Press'
    name!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#publishers-page-add-btn')?.click()
    await flush()
    const createBtn = el.querySelector<HTMLButtonElement>('#publishers-page-add-btn')
    expect(createBtn?.getAttribute('aria-busy')).toBe('true')
    expect(createBtn?.disabled).toBe(true)
    expect(createBtn?.textContent).toContain('Adding…')
    releaseCreate()
    await flush()

    el.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#publishers-page-alias-input')
    input!.value = 'Oxford'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    const addBtn = el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')
    expect(addBtn?.getAttribute('aria-busy')).toBe('true')
    expect(addBtn?.disabled).toBe(true)
    expect(addBtn?.textContent).toContain('Adding…')
    const removeBtn = el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')
    expect(removeBtn?.disabled).toBe(true)
    releaseAdd()
    await flush()

    el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')?.click()
    await flush()
    const removing = el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')
    expect(removing?.getAttribute('aria-busy')).toBe('true')
    expect(removing?.disabled).toBe(true)
    expect(removing?.textContent).toContain('Removing…')
    expect(removing?.getAttribute('aria-label')).toBe('Removing…')
    releaseRemove()
    await flush()

    el.querySelector<HTMLButtonElement>('#publishers-page-delete-btn')?.click()
    await flush()
    const deleteBtn = el.querySelector<HTMLButtonElement>('#publishers-page-delete-btn')
    expect(deleteBtn?.getAttribute('aria-busy')).toBe('true')
    expect(deleteBtn?.disabled).toBe(true)
    expect(deleteBtn?.textContent).toContain('Deleting…')
    releaseDelete()
    await flush()
    expect(deleteBtn?.getAttribute('aria-busy')).toBeNull()
  })

  it('does not reopen a closed alias dialog when the write finishes', async () => {
    let releaseAdd: (value: { ok: boolean }) => void = () => {}
    let resume: { aliasPublisherId?: string | null } | null | undefined = { aliasPublisherId: 'pending' }
    window.prksPublishersAddAlias = () => new Promise((resolve) => {
      releaseAdd = resolve
    })
    const pane = owner()
    const el = host()
    window.prksReloadPublishersPage = async (ownerArg, generation, nextResume) => {
      resume = nextResume
      presentPublishers({
        owner: ownerArg as ReturnType<typeof owner>,
        host: el,
        publishers: [OUP, CUP],
        generation,
        resume: nextResume,
      })
      return true
    }
    presentPublishers({ owner: pane, host: el, publishers: [OUP, CUP], generation: 8 })
    el.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#publishers-page-alias-input')
    input!.value = 'Oxford'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    releaseAdd({ ok: true })
    await flush()
    expect(resume).toBeNull()
    expect(el.querySelector('#publishers-page-alias-modal')).toBeNull()
    expect(el.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
  })

  it('keeps a newer publisher dialog when an earlier alias write finishes', async () => {
    let releaseAdd: (value: { ok: boolean }) => void = () => {}
    window.prksPublishersAddAlias = () => new Promise((resolve) => {
      releaseAdd = resolve
    })
    const pane = owner()
    const el = host()
    window.prksReloadPublishersPage = async (ownerArg, generation, nextResume) => {
      presentPublishers({
        owner: ownerArg as ReturnType<typeof owner>,
        host: el,
        publishers: [OUP, CUP],
        generation,
        resume: nextResume,
      })
      return true
    }
    presentPublishers({ owner: pane, host: el, publishers: [OUP, CUP], generation: 10 })
    el.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p1"]')?.click()
    await nextTick()
    const input = el.querySelector<HTMLInputElement>('#publishers-page-alias-input')
    input!.value = 'Oxford'
    input!.dispatchEvent(new Event('input'))
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    el.querySelector<HTMLButtonElement>('[data-publisher-alias-edit="p2"]')?.click()
    await nextTick()
    expect(el.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
    releaseAdd({ ok: true })
    await flush()
    expect(el.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
    expect(el.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
  })
})
