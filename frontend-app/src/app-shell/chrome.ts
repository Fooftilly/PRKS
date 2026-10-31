import { inject, type InjectionKey, type Ref } from 'vue'

/**
 * Published shell snapshot. `navigation.js` (`prksNoteShellChrome`) is the
 * writer. App chrome renders it and does not keep a second route or origin.
 */
export interface ShellChromeRoute {
  name?: string
  canonicalHash?: string
  hash?: string
  params?: {
    status?: string
    role?: string
    knownRole?: boolean
  }
}

export interface ShellSyncQueue {
  count: number
  ariaLabel: string
}

export interface ShellChromeSnapshot {
  route: ShellChromeRoute | null
  activeHref: string | null
  progressStatus: string | null
  connectivity: string
  processingCount: number | null
  syncQueue: ShellSyncQueue | null
  sidebarOpen: boolean
  detailsOpen: boolean
  disclosureRevision: number
}

export interface NavTarget {
  href: string
  status?: string
}

export type NavFamily = 'people' | 'research' | 'progress'

export interface CreateAction {
  id: string
  label: string
  icon: string
  modalId: string
}

export const CREATE_ACTIONS: readonly CreateAction[] = [
  { id: 'new-file', label: 'New File', icon: 'file-plus', modalId: 'work-modal' },
  { id: 'new-folder', label: 'New Folder', icon: 'folder', modalId: 'folder-modal' },
  { id: 'new-person', label: 'New Person', icon: 'user', modalId: 'person-modal' },
  { id: 'new-group', label: 'New Group', icon: 'users', modalId: 'group-modal' },
]

export const chromeKey: InjectionKey<Ref<ShellChromeSnapshot>> = Symbol('prks-app-chrome')

export function useAppChrome(): Ref<ShellChromeSnapshot> {
  const value = inject(chromeKey)
  if (!value) throw new Error('App chrome is not provided')
  return value
}

export function emptyShellChrome(): ShellChromeSnapshot {
  return {
    route: null,
    activeHref: null,
    progressStatus: null,
    connectivity: 'online',
    processingCount: null,
    syncQueue: null,
    sidebarOpen: false,
    detailsOpen: false,
    disclosureRevision: 0,
  }
}

export function shellChromeFrom(partial?: Partial<ShellChromeSnapshot> | null): ShellChromeSnapshot {
  return { ...emptyShellChrome(), ...(partial || {}) }
}

/** One current destination. Progress status is only the fallback when the href misses every link. */
export function activeNavHref(links: readonly NavTarget[], snapshot: ShellChromeSnapshot): string | null {
  const href = snapshot.activeHref
  if (!href) return null
  if (links.some((link) => link.href === href)) return href
  const status = snapshot.progressStatus
  if (!status) return null
  const byStatus = links.find((link) => link.status === status)
  return byStatus ? byStatus.href : null
}

export function paletteShortcutHint(): string {
  try {
    const nav = navigator
    const platform = String(nav.platform || nav.userAgent || '')
    return /Mac|iPhone|iPad|iPod/.test(platform) ? '⌘ K' : 'Ctrl K'
  } catch {
    return 'Ctrl K'
  }
}

function disclosureSpec(which: NavFamily): { prefKey: string; show?: string; hide?: string } | null {
  const spec = window.PRKS_NAV_DISCLOSURES?.[which]
  if (!spec || !spec.prefKey) return null
  return spec
}

export function disclosureExpanded(which: NavFamily, route: ShellChromeRoute | null): boolean {
  return window.prksNavDisclosureExpanded?.(which, route) ?? false
}

export function disclosureContainsCurrent(which: NavFamily, route: ShellChromeRoute | null): boolean {
  return window.prksNavFamilyForcesOpen?.(which, route) ?? false
}

export function peopleToggleLabel(expanded: boolean): string {
  const spec = disclosureSpec('people')
  if (spec?.show && spec.hide) return expanded ? spec.hide : spec.show
  return expanded ? 'Hide People shortcuts' : 'Show People shortcuts'
}

/**
 * Explicit preference flip. A collapsed tiled rail treats a full-row family
 * control as "open navigation", which expands rather than toggles.
 */
export function toggleNavDisclosure(which: NavFamily, route: ShellChromeRoute | null): void {
  const spec = disclosureSpec(which)
  if (!spec || typeof window.prksWriteNavExpandedPref !== 'function') return
  const fullRow = which !== 'people'
  const app = document.getElementById('app-container')
  const opensRail =
    fullRow &&
    !!app?.classList.contains('app-container--tiled') &&
    !document.body.classList.contains('prks-sidebar-open')
  if (opensRail) {
    if (typeof window.prksToggleSidebarDrawer === 'function') window.prksToggleSidebarDrawer(true)
    else window.prksOpenSidebarDrawer?.()
    window.prksWriteNavExpandedPref(spec.prefKey, true)
    return
  }
  const expandedNow = disclosureExpanded(which, route)
  window.prksWriteNavExpandedPref(spec.prefKey, !expandedNow)
}
