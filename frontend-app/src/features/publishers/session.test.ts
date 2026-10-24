import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetPrksQueryClientForTests } from '../../query/client'
import { readRouteSurface } from '../../route-surface/lifecycle'
import { presentPublishers, registerPublishersBridge, resetPublishersSessionForTests } from './session'

afterEach(() => {
  resetPublishersSessionForTests()
  resetPrksQueryClientForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  delete window.prksVuePresentRoute
  delete window.prksVueDismissRoute
  delete window.prksVueClosePublishersAliasModal
  delete window.prksIcon
  delete window.prksTagPlusIconHtml
  delete window.prksRefreshIcons
  delete window.prksAlertMessage
  delete window.prksConfirmDestructive
})

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
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

interface Row {
  id: string
  name: string
  aliases: string[]
  work_count: number
}

const OUP: Row = { id: 'p1', name: 'Oxford University Press', work_count: 2, aliases: ['OUP'] }
const CUP: Row = { id: 'p2', name: 'Cambridge', work_count: 1, aliases: [] }

type Reply = { status: number; body: unknown }

/**
 * An in-memory Publishers server behind `fetch`. `refuse` answers the next
 * matching request with an error; `hold` keeps it pending until released.
 */
function fakeServer(initial: Row[]) {
  const rows = initial.map((row) => ({ ...row, aliases: [...row.aliases] }))
  const requests: string[] = []
  const refusals = new Map<string, Reply>()
  const holds = new Map<string, Promise<void>>()
  let nextId = 1

  function handle(method: string, path: string, query: URLSearchParams, body: Record<string, string>): Reply {
    if (method === 'GET' && path === '/api/publishers') {
      return { status: 200, body: rows.map((row) => ({ ...row, aliases: [...row.aliases] })) }
    }
    if (method === 'POST' && path === '/api/publishers') {
      const row = { id: `new-${nextId++}`, name: body.name, aliases: [], work_count: 0 }
      rows.push(row)
      return { status: 200, body: { id: row.id, name: row.name, existed: false } }
    }
    const match = /^\/api\/publishers\/([^/]+)(\/aliases)?$/.exec(path)
    const row = match ? rows.find((candidate) => candidate.id === decodeURIComponent(match[1])) : undefined
    if (match?.[2] && method === 'POST' && row) {
      row.aliases.push(body.alias)
      return { status: 200, body: { status: 'added' } }
    }
    if (match?.[2] && method === 'DELETE' && row) {
      row.aliases = row.aliases.filter((alias) => alias !== query.get('alias'))
      return { status: 200, body: { status: 'deleted' } }
    }
    if (match && !match[2] && method === 'DELETE') {
      rows.splice(rows.findIndex((candidate) => candidate.id === decodeURIComponent(match[1])), 1)
      return { status: 200, body: { status: 'deleted' } }
    }
    return { status: 404, body: { error: 'not found' } }
  }

  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const parsed = new URL(url, location.origin)
    const method = init.method ?? 'GET'
    const key = `${method} ${parsed.pathname}`
    requests.push(key)
    const held = holds.get(key)
    if (held) {
      holds.delete(key)
      await held
    }
    const refused = refusals.get(key)
    if (refused) refusals.delete(key)
    const body = typeof init.body === 'string' ? (JSON.parse(init.body) as Record<string, string>) : {}
    const reply = refused ?? handle(method, parsed.pathname, parsed.searchParams, body)
    return new Response(JSON.stringify(reply.body), { status: reply.status })
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    requests,
    refuse(key: string, reply: Reply) {
      refusals.set(key, reply)
    },
    hold(key: string): () => void {
      let release: () => void = () => {}
      holds.set(
        key,
        new Promise<void>((resolve) => {
          release = resolve
        }),
      )
      return () => release()
    },
    gets: () => requests.filter((request) => request === 'GET /api/publishers').length,
  }
}

function aliasIds(el: HTMLElement): (string | null)[] {
  return [...el.querySelectorAll('[data-publisher-alias-edit]')].map((node) =>
    node.getAttribute('data-publisher-alias-edit'),
  )
}

async function openAliasDialog(el: HTMLElement, publisherId: string): Promise<void> {
  el.querySelector<HTMLButtonElement>(`[data-publisher-alias-edit="${publisherId}"]`)?.click()
  await nextTick()
}

function typeInto(el: HTMLElement, selector: string, value: string): void {
  const input = el.querySelector<HTMLInputElement>(selector)
  input!.value = value
  input!.dispatchEvent(new Event('input'))
}

describe('Publishers route surface', () => {
  it('shows loading, then paints both panes from one shared read', async () => {
    const server = fakeServer([OUP, CUP])
    window.prksIcon = () => '<i data-lucide="building-2"></i>'
    window.prksTagPlusIconHtml = () => '<span class="tag-add-shell__icon"></span>'
    const refreshIcons = vi.fn()
    window.prksRefreshIcons = refreshIcons
    registerPublishersBridge(window)
    const main = owner()
    const secondary = owner()
    const mainHost = host()
    const secondaryHost = host()
    presentPublishers({ owner: main, host: mainHost, generation: 3, shell: true })
    presentPublishers({ owner: secondary, host: secondaryHost, generation: 1, shell: false })
    expect(mainHost.querySelector('[data-publishers-loading]')?.textContent).toContain('Loading publishers…')
    expect(mainHost.querySelector('.publishers-page__empty')).toBeNull()
    await flush()
    expect(server.gets()).toBe(1)
    expect(mainHost.querySelector('[data-publishers-loading]')).toBeNull()
    expect(aliasIds(mainHost)).toEqual(['p1', 'p2'])
    expect(aliasIds(secondaryHost)).toEqual(['p1', 'p2'])
    expect(mainHost.querySelector('.prks-page-title')?.textContent).toBe('Publishers')
    expect(mainHost.querySelector('[data-prks-route="#/search?publisher=Oxford%20University%20Press"]')).not.toBeNull()
    expect(mainHost.querySelector('.publishers-page__list-stats')?.textContent).toBe('2 files · 1 alias')
    expect(refreshIcons).toHaveBeenCalledWith(mainHost.querySelector('[data-prks-publishers-page]'))
    expect(readRouteSurface(main)).toMatchObject({ name: 'publishers', canonicalHash: '#/publishers', ownsMainShell: true })
    expect(readRouteSurface(secondary)?.ownsMainShell).toBe(false)

    await openAliasDialog(mainHost, 'p1')
    expect(mainHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Oxford University Press')
    expect(secondaryHost.querySelector('#publishers-page-alias-modal')).toBeNull()
    window.prksVueClosePublishersAliasModal?.()
    await nextTick()
    expect(mainHost.querySelector('#publishers-page-alias-modal')).toBeNull()
  })

  it('shows the empty state only after an empty read succeeds', async () => {
    fakeServer([])
    const el = host()
    presentPublishers({ owner: owner(), host: el, generation: 1 })
    expect(el.querySelector('.publishers-page__empty')).toBeNull()
    await flush()
    expect(el.querySelector('.publishers-page__empty')?.textContent).toContain('No publisher groups yet')
  })

  it('shows a first-load failure in place of the list and retries', async () => {
    const server = fakeServer([OUP])
    server.refuse('GET /api/publishers', { status: 400, body: { error: 'nope' } })
    const el = host()
    presentPublishers({ owner: owner(), host: el, generation: 1 })
    await flush()
    expect(el.querySelector('[data-publishers-load-error]')?.textContent).toContain('Could not load publishers.')
    expect(el.querySelector('.publishers-page__empty')).toBeNull()
    expect(el.querySelector('#publishers-page-cloud')).toBeNull()
    el.querySelector<HTMLButtonElement>('[data-publishers-load-error] button')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-load-error]')).toBeNull()
    expect(aliasIds(el)).toEqual(['p1'])
  })

  it('refetches on each mount and keeps the painted list when that refetch fails', async () => {
    const server = fakeServer([OUP, CUP])
    const pane = owner()
    const el = host()
    presentPublishers({ owner: pane, host: el, generation: 1 })
    await flush()
    resetPublishersSessionForTests()
    server.refuse('GET /api/publishers', { status: 400, body: { error: 'nope' } })
    const again = host()
    presentPublishers({ owner: owner(), host: again, generation: 1 })
    expect(aliasIds(again)).toEqual(['p1', 'p2'])
    await flush()
    expect(server.gets()).toBe(2)
    expect(aliasIds(again)).toEqual(['p1', 'p2'])
    expect(again.querySelector('.publishers-page__empty')).toBeNull()
    expect(again.querySelector('[data-publishers-refresh-error]')?.textContent).toContain('Could not refresh publishers.')
  })

  it('registers the bridge and paints the host that stored the request', async () => {
    fakeServer([OUP])
    const el = host()
    const decoy = host()
    el.setAttribute('data-prks-vue-route-host', 'true')
    decoy.setAttribute('data-prks-vue-route-host', 'true')
    ;(el as HTMLElement & { __prksVueRouteRequest?: object }).__prksVueRouteRequest = {
      feature: 'publishers',
      owner: owner(),
      host: decoy,
      generation: 1,
      shell: true,
    }
    registerPublishersBridge(window)
    expect(window.prksVuePresentRoute).toBeTypeOf('function')
    expect(window.prksVueDismissRoute).toBeTypeOf('function')
    expect((el as HTMLElement & { __prksVueRouteRequest?: unknown }).__prksVueRouteRequest).toBeUndefined()
    await flush()
    expect(el.querySelector('[data-publisher-alias-edit="p1"]')).not.toBeNull()
    expect(decoy.querySelector('[data-prks-publishers-page]')).toBeNull()
  })

  it('dismisses one owner and leaves the other mounted', async () => {
    fakeServer([OUP, CUP])
    registerPublishersBridge(window)
    const main = owner()
    const mainHost = host()
    const secondaryHost = host()
    window.prksVuePresentRoute?.({ feature: 'publishers', owner: main, host: mainHost, generation: 2, shell: true })
    window.prksVuePresentRoute?.({ feature: 'publishers', owner: owner(), host: secondaryHost, generation: 1, shell: false })
    await flush()
    await openAliasDialog(secondaryHost, 'p2')
    window.prksVueDismissRoute?.(main)
    expect(mainHost.querySelector('[data-prks-publishers-page]')).toBeNull()
    expect(secondaryHost.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
  })

  it('opens files from a click on the publisher name, not a nested control', async () => {
    fakeServer([OUP])
    const el = host()
    presentPublishers({ owner: owner(), host: el, generation: 1 })
    await flush()
    const nameText = [...el.querySelectorAll('span')].find((node) => node.textContent === 'Oxford University Press')
    expect(nameText).toBeTruthy()
    const route = nameText!.closest('[data-prks-route]')
    expect(route).toBe(nameText!.closest('button, [role="button"]'))
    expect(route?.getAttribute('role')).toBe('button')
    expect(route?.getAttribute('data-prks-route')).toBe('#/search?publisher=Oxford%20University%20Press')
    expect(route?.getAttribute('data-prks-middleclick-nav')).toBe('1')
    expect(route?.classList.contains('publishers-page__list-main')).toBe(true)
    const alias = el.querySelector('[data-publisher-alias-edit="p1"]')
    expect(route?.contains(alias)).toBe(false)
    expect(alias?.closest('[data-prks-route]')).toBeNull()
    expect(el.querySelector('.publishers-page__list-item')?.hasAttribute('data-prks-route')).toBe(false)
  })

  it('returns focus to the alias button after the dialog closes', async () => {
    fakeServer([OUP])
    const el = host()
    presentPublishers({ owner: owner(), host: el, generation: 1 })
    await flush()
    await openAliasDialog(el, 'p1')
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    expect(document.activeElement).toBe(el.querySelector('[data-publisher-alias-edit="p1"]'))
  })

  it('creates, edits aliases, and deletes through the server and refreshes every pane', async () => {
    const server = fakeServer([OUP, CUP])
    window.prksConfirmDestructive = async () => true
    const el = host()
    const other = host()
    presentPublishers({ owner: owner(), host: el, generation: 1 })
    presentPublishers({ owner: owner(), host: other, generation: 1, shell: false })
    await flush()

    typeInto(el, '#publishers-page-new-name', 'New Press')
    el.querySelector<HTMLButtonElement>('#publishers-page-add-btn')?.click()
    await flush()
    expect(el.querySelector<HTMLInputElement>('#publishers-page-new-name')?.value).toBe('')
    expect(aliasIds(el)).toEqual(['p1', 'p2', 'new-1'])
    expect(aliasIds(other)).toEqual(['p1', 'p2', 'new-1'])

    await openAliasDialog(el, 'p1')
    typeInto(el, '#publishers-page-alias-input', 'Oxford UP')
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    expect(el.querySelector('#publishers-page-alias-modal')).not.toBeNull()
    expect(el.querySelector<HTMLInputElement>('#publishers-page-alias-input')?.value).toBe('')
    expect(el.querySelector('[data-publisher-alias-remove="Oxford UP"]')).not.toBeNull()
    expect(other.querySelectorAll('.publishers-page__list-stats')[0]?.textContent).toBe('2 files · 2 aliases')

    el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')?.click()
    await flush()
    expect(el.querySelector('[data-publisher-alias-remove="OUP"]')).toBeNull()

    el.querySelector<HTMLButtonElement>('#publishers-page-delete-btn')?.click()
    await flush()
    expect(el.querySelector('#publishers-page-alias-modal')).toBeNull()
    expect(aliasIds(el)).toEqual(['p2', 'new-1'])
    expect(aliasIds(other)).toEqual(['p2', 'new-1'])
    expect(server.requests).toEqual([
      'GET /api/publishers',
      'POST /api/publishers',
      'GET /api/publishers',
      'POST /api/publishers/p1/aliases',
      'GET /api/publishers',
      'DELETE /api/publishers/p1/aliases',
      'GET /api/publishers',
      'DELETE /api/publishers/p1',
      'GET /api/publishers',
    ])
  })

  it('shows create, alias, and delete failures locally', async () => {
    const server = fakeServer([OUP, CUP])
    const alert = vi.fn()
    window.prksAlertMessage = alert
    window.prksConfirmDestructive = async () => true
    const el = host()
    presentPublishers({ owner: owner(), host: el, generation: 5 })
    await flush()

    server.refuse('POST /api/publishers', { status: 400, body: { error: 'publisher name is empty' } })
    typeInto(el, '#publishers-page-new-name', 'New Press')
    el.querySelector<HTMLButtonElement>('#publishers-page-add-btn')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-create-error]')?.textContent).toContain('publisher name is empty')

    await openAliasDialog(el, 'p1')
    server.refuse('POST /api/publishers/p1/aliases', { status: 400, body: { error: 'alias already used' } })
    typeInto(el, '#publishers-page-alias-input', 'Oxford')
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-alias-add-error]')?.textContent).toContain('alias already used')

    server.refuse('DELETE /api/publishers/p1/aliases', { status: 404, body: { error: 'alias not found' } })
    el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')?.click()
    await flush()
    expect(el.querySelector('[data-publishers-alias-remove-error]')?.textContent).toContain('alias not found')

    server.refuse('DELETE /api/publishers/p1', { status: 500, body: null })
    const deleteBtn = el.querySelector<HTMLButtonElement>('#publishers-page-delete-btn')
    expect(deleteBtn?.classList.contains('prks-btn--danger')).toBe(true)
    deleteBtn?.click()
    await flush()
    expect(el.querySelector('[data-publishers-delete-error]')?.textContent).toContain('Could not delete publisher.')
    expect(el.querySelector('#publishers-page-alias-modal')).not.toBeNull()

    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    expect(el.querySelector('[data-publishers-alias-add-error]')).toBeNull()
    expect(alert).not.toHaveBeenCalled()
  })

  it('marks create, alias, and delete busy while the write is in flight', async () => {
    const server = fakeServer([OUP, CUP])
    window.prksConfirmDestructive = async () => true
    const el = host()
    presentPublishers({ owner: owner(), host: el, generation: 6 })
    await flush()

    const releaseCreate = server.hold('POST /api/publishers')
    typeInto(el, '#publishers-page-new-name', 'New Press')
    el.querySelector<HTMLButtonElement>('#publishers-page-add-btn')?.click()
    await flush()
    const createBtn = el.querySelector<HTMLButtonElement>('#publishers-page-add-btn')
    expect(createBtn?.getAttribute('aria-busy')).toBe('true')
    expect(createBtn?.disabled).toBe(true)
    expect(createBtn?.textContent).toContain('Adding…')
    releaseCreate()
    await flush()

    await openAliasDialog(el, 'p1')
    const releaseAdd = server.hold('POST /api/publishers/p1/aliases')
    typeInto(el, '#publishers-page-alias-input', 'Oxford')
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    const addBtn = el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')
    expect(addBtn?.getAttribute('aria-busy')).toBe('true')
    expect(addBtn?.disabled).toBe(true)
    expect(addBtn?.textContent).toContain('Adding…')
    expect(el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')?.disabled).toBe(true)
    releaseAdd()
    await flush()

    const releaseRemove = server.hold('DELETE /api/publishers/p1/aliases')
    el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')?.click()
    await flush()
    const removing = el.querySelector<HTMLButtonElement>('[data-publisher-alias-remove="OUP"]')
    expect(removing?.getAttribute('aria-busy')).toBe('true')
    expect(removing?.disabled).toBe(true)
    expect(removing?.textContent).toContain('Removing…')
    expect(removing?.getAttribute('aria-label')).toBe('Removing…')
    releaseRemove()
    await flush()

    const releaseDelete = server.hold('DELETE /api/publishers/p1')
    el.querySelector<HTMLButtonElement>('#publishers-page-delete-btn')?.click()
    await flush()
    const deleteBtn = el.querySelector<HTMLButtonElement>('#publishers-page-delete-btn')
    expect(deleteBtn?.getAttribute('aria-busy')).toBe('true')
    expect(deleteBtn?.disabled).toBe(true)
    expect(deleteBtn?.textContent).toContain('Deleting…')
    releaseDelete()
    await flush()
    expect(el.querySelector('#publishers-page-delete-btn')).toBeNull()
  })

  it('does not reopen a closed dialog or replace a newer one when a write finishes', async () => {
    const server = fakeServer([OUP, CUP])
    const el = host()
    presentPublishers({ owner: owner(), host: el, generation: 8 })
    await flush()

    await openAliasDialog(el, 'p1')
    const releaseFirst = server.hold('POST /api/publishers/p1/aliases')
    typeInto(el, '#publishers-page-alias-input', 'Oxford')
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    releaseFirst()
    await flush()
    expect(el.querySelector('#publishers-page-alias-modal')).toBeNull()

    await openAliasDialog(el, 'p1')
    const releaseSecond = server.hold('POST /api/publishers/p1/aliases')
    typeInto(el, '#publishers-page-alias-input', 'Oxford Press')
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-add-btn')?.click()
    await flush()
    el.querySelector<HTMLButtonElement>('#publishers-page-alias-modal-close')?.click()
    await nextTick()
    await openAliasDialog(el, 'p2')
    releaseSecond()
    await flush()
    expect(el.querySelector('#publishers-page-alias-canonical')?.textContent).toBe('Cambridge')
    expect(el.querySelector('[data-publishers-alias-add-error]')).toBeNull()
  })
})
