export interface PersonPayload {
  first_name: string
  last_name: string
  aliases: string
  about: string
  image_url: string
  link_wikipedia: string
  link_stanford_encyclopedia: string
  link_iep: string
  links_other: string
  birth_date: string
  death_date: string
}

interface CreatedEntity {
  entity_id?: string
}

interface PersonWindow {
  prksCreatePersonDurably?: (payload: PersonPayload) => Promise<CreatedEntity | null>
  closeModals?: () => void
  prksTakePersonIndexCreateTabId?: () => string
  prksNavigate?: (hash: string, options?: { tabId?: string }) => void
}

function personWindow(): PersonWindow {
  return window as unknown as PersonWindow
}

export async function createPersonDurably(payload: PersonPayload): Promise<CreatedEntity | null> {
  const w = personWindow()
  if (typeof w.prksCreatePersonDurably !== 'function') {
    throw new Error('Could not save this person locally. Please retry.')
  }
  return w.prksCreatePersonDurably(payload)
}

/** Close and navigate only while the Save's modal opening is still current. */
export async function finishPersonCreate(
  created: CreatedEntity | null,
  stillOpening: () => boolean,
): Promise<void> {
  if (!stillOpening()) return
  const w = personWindow()
  w.closeModals?.()
  if (!stillOpening()) return
  const createTabId = typeof w.prksTakePersonIndexCreateTabId === 'function' ? w.prksTakePersonIndexCreateTabId() : ''
  if (created && created.entity_id && typeof w.prksNavigate === 'function') {
    const hash = `#/people/${encodeURIComponent(created.entity_id)}`
    if (createTabId) void w.prksNavigate(hash, { tabId: createTabId })
    else void w.prksNavigate(hash)
  }
}

interface GroupNav {
  mode?: string
  tabId?: string
}

interface GroupWindow {
  prksResolvePersonGroupParent?: (
    parentId: string,
    parentName: string,
    selfId: null,
  ) => Promise<string | undefined>
  prksCreatePersonGroupDurably?: (body: {
    name: string
    description: string
    parent_id: string
  }) => Promise<CreatedEntity | null>
  closeModals?: () => void
  prksTakePersonGroupCreateNavigation?: () => GroupNav
  prksNavigate?: (hash: string, options?: { tabId?: string }) => void
  prksPersonGroupSaveMessage?: (error: unknown, action: string) => string
}

function groupWindow(): GroupWindow {
  return window as unknown as GroupWindow
}

export function groupCreateFailureMessage(error: unknown): string {
  const w = groupWindow()
  if (typeof w.prksPersonGroupSaveMessage === 'function') {
    return w.prksPersonGroupSaveMessage(error, 'create this group')
  }
  return 'Could not create group.'
}

export async function resolveGroupParent(
  parentId: string,
  parentQuery: string,
): Promise<string | undefined> {
  const w = groupWindow()
  if (typeof w.prksResolvePersonGroupParent !== 'function') {
    throw new Error('Could not create group.')
  }
  return w.prksResolvePersonGroupParent(parentId, parentQuery, null)
}

export async function createGroupDurably(body: {
  name: string
  description: string
  parent_id: string
}): Promise<CreatedEntity | null> {
  const w = groupWindow()
  if (typeof w.prksCreatePersonGroupDurably !== 'function') {
    throw new Error('Could not create group.')
  }
  return w.prksCreatePersonGroupDurably(body)
}

export async function finishGroupCreate(
  created: CreatedEntity | null,
  stillOpening: () => boolean,
): Promise<void> {
  if (!stillOpening()) return
  const w = groupWindow()
  w.closeModals?.()
  if (!stillOpening()) return
  const groupNav = w.prksTakePersonGroupCreateNavigation?.() ?? { mode: 'unscoped' }
  if (groupNav && groupNav.mode === 'stale') return
  if (created && created.entity_id && typeof w.prksNavigate === 'function') {
    const hash = `#/people/groups/${encodeURIComponent(created.entity_id)}`
    if (groupNav && groupNav.mode === 'owner' && groupNav.tabId) {
      w.prksNavigate(hash, { tabId: groupNav.tabId })
    } else {
      w.prksNavigate(hash)
    }
  }
}

export interface PlaylistPending {
  workId?: string
  tabId?: string
  generation?: number
}

interface PlaylistWindow {
  __prksPendingPlaylistAttach?: PlaylistPending | null
}

function playlistWindow(): PlaylistWindow {
  return window as unknown as PlaylistWindow
}

export function readPendingPlaylistAttach(): PlaylistPending | null {
  return playlistWindow().__prksPendingPlaylistAttach ?? null
}

export function clearPendingPlaylistAttachIfSame(captured: PlaylistPending | null | undefined): void {
  const w = playlistWindow()
  if (captured && w.__prksPendingPlaylistAttach === captured) {
    w.__prksPendingPlaylistAttach = null
  }
}

export function playlistFailureMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  return 'Could not create playlist.'
}
