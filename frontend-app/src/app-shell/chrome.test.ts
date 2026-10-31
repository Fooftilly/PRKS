import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import routeSource from '../../../frontend/js/route-model.js?raw'
import navSource from '../../../frontend/js/navigation.js?raw'
import AppChrome from './AppChrome.vue'
import { activeNavHref, emptyShellChrome } from './chrome'

function installNavigation(): void {
  const loadRoute = new Function(`${routeSource}\nreturn prksRouteModel;`) as () => unknown
  ;(window as Window & { prksRouteModel?: unknown }).prksRouteModel = loadRoute()
  const loadNav = new Function(navSource) as () => void
  loadNav()
}

function hosts(): void {
  document.body.innerHTML = `
    <div id="app-container">
      <nav id="sidebar"></nav>
      <main id="main-content">
        <div class="top-ribbon" id="prks-top-ribbon"></div>
      </main>
    </div>`
}

function clearPrefs(): void {
  for (const key of ['prks.nav.peopleExpanded', 'prks.nav.researchExpanded', 'prks.nav.progressExpanded']) {
    localStorage.removeItem(key)
  }
}

describe('app chrome presentation', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    installNavigation()
    clearPrefs()
    delete window.__prksShellChrome
    document.body.className = ''
    hosts()
    window.prksIcon = (name) => `<i data-lucide="${name}"></i>`
    window.prksRefreshIcons = () => {}
    window.prksProgressStatusIconHtml = (status) => `<i data-lucide="${status}"></i>`
    window.prksRoleTypeIconHtml = (role) => `<i data-lucide="${role}"></i>`
    window.prksNavAttentionBadgeHtml = ({ count, ariaLabel }) =>
      `<span class="prks-nav-attention" aria-label="${ariaLabel ?? ''}">${count}</span>`
    window.openModal = () => {}
    window.prksOpenCommandPalette = () => {}
    window.prksToggleSidebarDrawer = (forceOpen) => {
      if (forceOpen === true) document.body.classList.add('prks-sidebar-open')
      else if (forceOpen === false) document.body.classList.remove('prks-sidebar-open')
      else document.body.classList.toggle('prks-sidebar-open')
      window.prksNoteShellChrome?.({ sidebarOpen: document.body.classList.contains('prks-sidebar-open') })
    }
    window.prksToggleRightPanelOverlay = (forceOpen) => {
      const open = forceOpen === undefined ? !document.body.classList.contains('prks-right-panel-open') : !!forceOpen
      document.body.classList.toggle('prks-right-panel-open', open)
      window.prksNoteShellChrome?.({ detailsOpen: open })
    }
    wrapper = mount(AppChrome, { attachTo: document.body })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
    clearPrefs()
    delete window.__prksShellChrome
    delete window.prksVueApplyShellChrome
  })

  function publish(hash: string): void {
    window.prksSyncSidebarActive?.(window.prksParseRoute?.(hash) ?? null)
  }

  function currentLinks(): HTMLAnchorElement[] {
    return Array.from(document.querySelectorAll<HTMLAnchorElement>('#sidebar .nav-link[aria-current="page"]'))
  }

  async function click(selector: string): Promise<void> {
    const el = document.querySelector(selector)
    if (!(el instanceof HTMLElement)) throw new Error('missing ' + selector)
    el.click()
    await nextTick()
  }

  async function keydown(selector: string, key: string): Promise<void> {
    const el = document.querySelector(selector)
    if (!(el instanceof HTMLElement)) throw new Error('missing ' + selector)
    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    await nextTick()
  }

  it('marks one sidebar destination from the published Main route', async () => {
    publish('#/folders/F-1')
    await nextTick()
    expect(currentLinks().map((link) => link.getAttribute('href'))).toEqual(['#/folders'])

    publish('#/recent')
    await nextTick()
    expect(currentLinks().map((link) => link.getAttribute('href'))).toEqual(['#/recent'])

    publish('#/search?q=x')
    await nextTick()
    expect(currentLinks()).toHaveLength(0)

    publish('#/people/role/Author')
    await nextTick()
    expect(currentLinks().map((link) => link.getAttribute('href'))).toEqual(['#/people/role/Author'])
    expect(document.querySelector('#sidebar a[href="#/people"]')?.getAttribute('aria-current')).toBeNull()
    expect(document.getElementById('prks-nav-people-children')?.hasAttribute('hidden')).toBe(false)

    publish('#/progress?status=Paused')
    await nextTick()
    const paused = document.querySelector('#sidebar a[data-status="Paused"]')
    expect(paused?.getAttribute('aria-current')).toBe('page')
    expect(currentLinks()).toHaveLength(1)
  })

  it('keeps an explicit disclosure choice across route publishes', async () => {
    publish('#/people/role/Reviewer')
    await nextTick()
    const people = document.querySelector('[data-nav-disclosure="people"]')
    const list = document.getElementById('prks-nav-people-children')
    expect(people?.classList.contains('nav-disclosure--open')).toBe(true)
    expect(list?.hasAttribute('hidden')).toBe(false)

    await click('[data-nav-disclosure-toggle="people"]')
    expect(localStorage.getItem('prks.nav.peopleExpanded')).toBe('0')
    expect(people?.classList.contains('nav-disclosure--open')).toBe(false)
    expect(list?.hasAttribute('hidden')).toBe(true)
    expect(people?.classList.contains('nav-disclosure--contains-current')).toBe(true)

    publish('#/people/P-1')
    await nextTick()
    expect(people?.classList.contains('nav-disclosure--open')).toBe(false)
    expect(people?.classList.contains('nav-disclosure--contains-current')).toBe(true)
    expect(document.querySelector('#sidebar a[href="#/people"]')?.getAttribute('aria-current')).toBe('page')

    window.prksWriteNavExpandedPref?.('prks.nav.peopleExpanded', true)
    window.prksSyncNavDisclosures?.()
    await nextTick()
    publish('#/folders')
    await nextTick()
    expect(people?.classList.contains('nav-disclosure--open')).toBe(true)
    expect(people?.classList.contains('nav-disclosure--contains-current')).toBe(false)
  })

  it('opens the tiled rail from a full-row disclosure without toggling it shut', async () => {
    document.getElementById('app-container')?.classList.add('app-container--tiled')
    localStorage.setItem('prks.nav.researchExpanded', '1')
    window.prksSyncNavDisclosures?.()
    await nextTick()
    const research = document.querySelector('[data-nav-disclosure="research"]')
    const list = document.getElementById('prks-nav-research-children')
    expect(research?.classList.contains('nav-disclosure--open')).toBe(true)
    expect(list?.hasAttribute('hidden')).toBe(false)

    let opens = 0
    window.prksToggleSidebarDrawer = (forceOpen) => {
      if (forceOpen === true) opens += 1
      document.body.classList.add('prks-sidebar-open')
    }
    await click('[data-nav-disclosure-toggle="research"]')
    expect(opens).toBe(1)
    expect(document.body.classList.contains('prks-sidebar-open')).toBe(true)
    expect(localStorage.getItem('prks.nav.researchExpanded')).toBe('1')
    expect(document.querySelector('[data-nav-disclosure-toggle="research"]')?.getAttribute('aria-expanded')).toBe('true')
    expect(list?.hasAttribute('hidden')).toBe(false)

    const progress = document.querySelector('[data-nav-disclosure-toggle="progress"]')
    expect(progress?.classList.contains('nav-disclosure__toggle--full')).toBe(true)
    expect(progress?.getAttribute('aria-controls')).toBe('prks-nav-progress-children')
    expect(progress?.textContent).toContain('Progress')
  })

  it('runs create actions from the ribbon and restores focus when the menu closes', async () => {
    const modals: string[] = []
    window.openModal = (id) => {
      modals.push(id)
    }
    await click('#prks-ribbon-new-file')
    expect(modals).toEqual(['work-modal'])

    const chevron = document.getElementById('prks-ribbon-new-more') as HTMLButtonElement
    chevron.focus()
    await keydown('#prks-ribbon-new-more', 'ArrowDown')
    await nextTick()
    const menu = document.getElementById('prks-create-menu')
    expect(menu?.hidden).toBe(false)
    expect(menu?.getAttribute('role')).toBe('menu')
    expect(menu?.querySelectorAll('[role="menuitem"]')).toHaveLength(4)
    expect(chevron.getAttribute('aria-expanded')).toBe('true')
    expect(document.body.contains(menu)).toBe(true)
    expect(document.getElementById('prks-top-ribbon')?.contains(menu)).toBe(false)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    await nextTick()
    expect(modals).toEqual(['work-modal', 'folder-modal'])
    expect(menu?.hidden).toBe(true)
    expect(document.activeElement).toBe(chevron)

    await click('#prks-ribbon-new-more')
    await nextTick()
    expect(document.getElementById('prks-create-menu')?.hidden).toBe(false)
    document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    await nextTick()
    expect(document.getElementById('prks-create-menu')?.hidden).toBe(true)
  })

  it('drops create-menu key listeners when the shell unmounts', async () => {
    const modals: string[] = []
    window.openModal = (id) => {
      modals.push(id)
    }
    await click('#prks-ribbon-new-more')
    await nextTick()
    wrapper!.unmount()
    wrapper = null
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    expect(modals).toEqual([])
    expect(window.prksVueApplyShellChrome).toBeUndefined()
  })

  it('keeps palette, drawer, connectivity, and attention cues on the classic owners', async () => {
    const scopes: Array<{ scope?: string } | undefined> = []
    window.prksOpenCommandPalette = (options) => {
      scopes.push(options)
    }
    await click('#prks-command-palette-launch')
    expect(scopes).toEqual([{ scope: 'all' }])
    expect(document.getElementById('prks-command-palette-launch')?.getAttribute('data-palette-bound')).toBe('1')
    expect(document.querySelector('[data-palette-shortcut-hint]')?.textContent).toMatch(/Ctrl K|⌘ K/)

    const nav = document.getElementById('prks-mobile-nav-btn')
    expect(nav?.getAttribute('aria-controls')).toBe('sidebar')
    expect(nav?.getAttribute('aria-expanded')).toBe('false')
    await click('#prks-mobile-nav-btn')
    await nextTick()
    expect(document.body.classList.contains('prks-sidebar-open')).toBe(true)
    expect(nav?.getAttribute('aria-expanded')).toBe('true')

    await click('#prks-mobile-details-btn')
    await nextTick()
    expect(document.getElementById('prks-mobile-details-btn')?.getAttribute('aria-expanded')).toBe('true')
    expect(document.getElementById('prks-mobile-details-btn')?.getAttribute('aria-controls')).toBe('right-panel')

    const indicator = document.getElementById('prks-connectivity-indicator')
    expect(indicator?.hidden).toBe(true)
    window.prksNoteShellChrome?.({ connectivity: 'offline' })
    await nextTick()
    expect(indicator?.hidden).toBe(false)
    expect(indicator?.textContent).toContain('Offline')
    window.prksNoteShellChrome?.({ connectivity: 'reconnecting' })
    await nextTick()
    expect(indicator?.textContent).toContain('Reconnecting…')
    expect(indicator?.classList.contains('prks-connectivity-indicator--reconnecting')).toBe(true)
    window.prksNoteShellChrome?.({ connectivity: 'online' })
    await nextTick()
    expect(indicator?.hidden).toBe(true)

    const cue = document.getElementById('prks-sync-queue-cue')
    expect(cue?.hidden).toBe(true)
    window.prksNoteShellChrome?.({ syncQueue: { count: 2, ariaLabel: '2 changes queued' } })
    await nextTick()
    expect(cue?.hidden).toBe(false)
    expect(cue?.getAttribute('aria-label')).toBe('2 changes queued')
    expect(cue?.querySelector('.prks-nav-attention')?.textContent).toBe('2')

    const opened: string[] = []
    window.openModal = (id) => {
      opened.push(id)
    }
    const diag = document.createElement('button')
    diag.setAttribute('data-prks-settings-category', 'diagnostics')
    let diagClicks = 0
    diag.addEventListener('click', () => {
      diagClicks += 1
    })
    document.body.appendChild(diag)
    await click('#prks-sync-queue-cue')
    expect(opened).toEqual(['settings-modal'])
    expect(diagClicks).toBe(1)

    window.prksNoteShellChrome?.({ processingCount: 3 })
    await nextTick()
    const badge = document.querySelector('[data-prks-role="nav-processing-badge"] .prks-nav-attention')
    expect(badge?.textContent).toBe('3')
    expect(badge?.getAttribute('aria-label')).toBe('3 files for processing')

    const folders = document.querySelector('#sidebar a.nav-link[href="#/folders"]')
    expect(folders?.tagName).toBe('A')
  })
})

describe('activeNavHref', () => {
  it('uses the progress status only when the href misses every link', () => {
    const links = [
      { href: '#/progress?status=Paused', status: 'Paused' },
      { href: '#/folders' },
    ]
    const base = emptyShellChrome()
    expect(activeNavHref(links, { ...base, activeHref: '#/folders' })).toBe('#/folders')
    expect(activeNavHref(links, { ...base, activeHref: '#/progress?status=Other', progressStatus: 'Paused' })).toBe(
      '#/progress?status=Paused',
    )
    expect(activeNavHref(links, { ...base, activeHref: null, progressStatus: 'Paused' })).toBeNull()
  })
})
