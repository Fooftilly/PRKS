interface PersonPayload {
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
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
}

function personWindow(): PersonWindow {
  return window as unknown as PersonWindow
}

export async function submitPersonCreate(payload: PersonPayload): Promise<void> {
  const w = personWindow()
  if (typeof w.prksCreatePersonDurably !== 'function') {
    await w.prksAlertMessage?.('Could not save this person locally. Please retry.', 'Could not save')
    return
  }
  let created: CreatedEntity | null
  try {
    created = await w.prksCreatePersonDurably(payload)
  } catch {
    await w.prksAlertMessage?.('Could not save this person locally. Please retry.', 'Could not save')
    return
  }
  w.closeModals?.()
  const createTabId = typeof w.prksTakePersonIndexCreateTabId === 'function' ? w.prksTakePersonIndexCreateTabId() : ''
  if (created && created.entity_id && typeof w.prksNavigate === 'function') {
    const hash = `#/people/${encodeURIComponent(created.entity_id)}`
    if (createTabId) void w.prksNavigate(hash, { tabId: createTabId })
    else void w.prksNavigate(hash)
  }
}

interface GroupDraft {
  name: string
  parentId: string
  parentQuery: string
  description: string
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
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
}

function groupWindow(): GroupWindow {
  return window as unknown as GroupWindow
}

export async function submitGroupCreate(draft: GroupDraft): Promise<void> {
  const w = groupWindow()
  if (typeof w.prksResolvePersonGroupParent !== 'function' || typeof w.prksCreatePersonGroupDurably !== 'function') {
    await w.prksAlertMessage?.('Could not create group.', 'Could not save')
    return
  }
  const parentId = await w.prksResolvePersonGroupParent(draft.parentId, draft.parentQuery, null)
  if (parentId === undefined) return
  try {
    const created = await w.prksCreatePersonGroupDurably({
      name: draft.name.trim(),
      description: draft.description.trim(),
      parent_id: parentId,
    })
    w.closeModals?.()
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
  } catch (error) {
    const message =
      typeof w.prksPersonGroupSaveMessage === 'function'
        ? w.prksPersonGroupSaveMessage(error, 'create this group')
        : 'Could not create group.'
    await w.prksAlertMessage?.(message, 'Could not save')
  }
}

interface PlaylistPending {
  workId?: string
  tabId?: string
  generation?: number
}

interface PlaylistWindow {
  createPlaylist?: (title: string, description: string) => Promise<string | null>
  closeModals?: () => void
  __prksPendingPlaylistAttach?: PlaylistPending | null
  prksCapturedWorkPanelStill?: (pending: PlaylistPending | null | undefined) => boolean
  addWorkToPlaylist?: (playlistId: string, workId: string, still: () => boolean) => Promise<unknown>
  __prksRefreshPlaylistSelects?: (playlistId: string) => Promise<void>
  __prksRefreshAllPlaylistSelects?: (playlistId: string) => Promise<void>
  prksTakePlaylistIndexCreateTabId?: () => string
  __prksReturnToWorkModalAfterPlaylist?: boolean
  prksNavigate?: (hash: string, options?: { tabId?: string }) => void
}

function playlistWindow(): PlaylistWindow {
  return window as unknown as PlaylistWindow
}

export async function submitPlaylistCreate(title: string, description: string): Promise<void> {
  const w = playlistWindow()
  if (typeof w.createPlaylist !== 'function') throw new Error('Could not create playlist.')
  const newId = await w.createPlaylist(title, description)
  if (!newId) throw new Error('Could not create playlist')
  w.closeModals?.()
  const pending = w.__prksPendingPlaylistAttach
  const still = () => typeof w.prksCapturedWorkPanelStill === 'function' && w.prksCapturedWorkPanelStill(pending)
  let attachedWork = false
  if (pending && pending.workId && still()) {
    try {
      await w.addWorkToPlaylist?.(newId, String(pending.workId), still)
      attachedWork = !!still()
    } catch {
      /* The durable layer reports the failure on the next read. */
    }
  }
  if (pending) w.__prksPendingPlaylistAttach = null
  if (attachedWork && typeof w.__prksRefreshPlaylistSelects === 'function') {
    await w.__prksRefreshPlaylistSelects(newId)
  }
  if (attachedWork && typeof w.__prksRefreshAllPlaylistSelects === 'function') {
    await w.__prksRefreshAllPlaylistSelects(newId)
  }
  const createTabId = typeof w.prksTakePlaylistIndexCreateTabId === 'function' ? w.prksTakePlaylistIndexCreateTabId() : ''
  if (w.__prksReturnToWorkModalAfterPlaylist === true) return
  if (!attachedWork && createTabId && typeof w.prksNavigate === 'function') {
    w.prksNavigate(`#/playlists/${encodeURIComponent(newId)}`, { tabId: createTabId })
  }
}

export function playlistFailureMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  return 'Could not create playlist.'
}
