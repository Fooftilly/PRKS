/**
 * The classic globals New File still calls (#1036).
 *
 * These are shared services, not New File state: the durable create
 * boundaries, the catalogue fetches, the role credit picker (also used by the
 * role link and Work metadata editors), the doc-type menu and the combobox
 * keyboard model. Each is read at call time, so a missing one fails closed
 * where it is used.
 */

export interface PersonRow {
  id: string
  first_name?: string
  last_name?: string
  aliases?: string
  [key: string]: unknown
}

export interface FolderRow {
  id: string
  title?: string
  parent_id?: string | null
  [key: string]: unknown
}

export interface TagRow {
  id: string
  name?: string
  [key: string]: unknown
}

export interface PlaylistRow {
  id: string
  title?: string
  [key: string]: unknown
}

export interface VideoMeta {
  title?: string
  author_name?: string
  thumbnail_url?: string
  [key: string]: unknown
}

export interface RoleLink {
  person_id: string
  role_type: string
  credit_name: string
}

export interface DurableWorkFields {
  title: string
  status: string
  doc_type: string
  abstract: string
  author_text: string
  year: string
  published_date: string
  urldate: string
  private_notes: string
  thumb_url: string
  source: { kind: 'video'; url: string }
  folder_id: string
  playlist_id: string
  roles: RoleLink[]
}

export interface NewFileWindow {
  // Catalogues
  fetchPersons?: () => Promise<PersonRow[] | null>
  fetchFolders?: () => Promise<FolderRow[] | null>
  fetchTags?: (options?: { used?: boolean }) => Promise<TagRow[] | null>
  fetchPlaylists?: () => Promise<PlaylistRow[] | null>
  allPersons?: PersonRow[]
  prksSetPersonCatalogue?: (persons: PersonRow[]) => void
  // Row labels and matching shared with the other pickers
  prksFolderRowLabel?: (folder: FolderRow, all: FolderRow[]) => string
  personMatchesComboboxQuery?: (person: PersonRow, queryLower: string) => boolean
  formatPersonComboboxSubtitle?: (person: PersonRow) => string
  prksTagMatchesQuery?: (tag: TagRow, queryLower: string) => boolean
  prksTagExactMatch?: (tag: TagRow, queryLower: string) => boolean
  prksTagComboboxLabel?: (tag: TagRow, queryLower: string) => string
  // Durable creates
  createFolder?: (title: string, description: string) => Promise<string>
  createPlaylist?: (title: string, description: string) => Promise<string | null>
  prksCreateTagDurably?: (body: { name: string; color: string }, known: TagRow[]) => Promise<{ entity_id: string }>
  prksQuickCreatePersonForSearchField?: (
    typedName: string,
    searchInputId: string,
    hiddenInputId: string,
    aboutText: string,
    options: { stillApplies: () => boolean },
  ) => Promise<unknown>
  prksCreateWorkDurably?: (
    fields: DurableWorkFields,
    options: { tags: Array<{ id: string; name: string }> },
  ) => Promise<{ create?: { entity_id?: string } } | null>
  // Role credit picker (`upload-role` prefix)
  prksWorkHasRoleLink?: (roles: RoleLink[], personId: string, roleType: string) => boolean
  prksBindRoleCreditPicker?: (prefix: string) => void
  prksRefreshRoleCreditPicker?: (prefix: string, person: PersonRow | null) => void
  prksSetRoleCreditPickerValue?: (prefix: string, credit: string) => void
  prksReadRoleCreditName?: (prefix: string) => string
  prksResolveRoleCreditNameForLink?: (prefix: string, personId: string, searchInputId: string) => string
  prksFindPersonInCache?: (personId: string) => PersonRow | null
  prksPersonCanonicalName?: (person: PersonRow) => string
  // Form widgets
  initPrksDocTypeMenu?: (hiddenId: string, options: { selectedValue: string; disabled: boolean }) => void
  prksParsePublishedDateInput?: (raw: string) => string
  prksExtractYoutubeVideoId?: (url: string) => string
  prksRefreshIcons?: (root: Element) => void
  prksIcon?: (name: string, options?: { size?: string }) => string
  // Dialog, routing and messages
  requestModalClose?: (source: string) => void
  closeModals?: () => void
  prksNavigate?: (hash: string) => void
  prksAlertMessage?: (message: string, title?: string) => Promise<void> | void
  prksTagVocabularyMessage?: (error: unknown, action: string) => string
  prksOfflineWasGuardRefusal?: (error: unknown) => boolean
  prksOfflineGuardMutation?: (message: string) => boolean
  prksFocusedRouteRecord?: () => { name?: string; params?: Record<string, string> } | null
  prksParseRoute?: (hash: string) => { name?: string; params?: Record<string, string> } | null
  // PDF create (plain request) and its cache invalidation
  prksWaitForPeopleOnServer?: (personIds: string[]) => Promise<boolean>
  prksWorkCreateFailureText?: (errText: string) => string
  prksMarkPlaylistsDomainChanged?: () => void
  prksMarkPersonGroupsDomainChanged?: () => void
  prksMarkPeopleDomainChanged?: () => void
  prksMarkWorksBrowseChanged?: () => void
  prksMarkRecentlyAddedChanged?: () => void
  prksMarkFoldersDomainChanged?: () => void
}

export function runtime(): NewFileWindow {
  return window as unknown as NewFileWindow
}

export async function alertMessage(message: string, title: string): Promise<void> {
  const alert = runtime().prksAlertMessage
  if (typeof alert === 'function') await alert(message, title)
}

export async function loadList<T>(load: (() => Promise<T[] | null>) | undefined): Promise<T[]> {
  if (typeof load !== 'function') return []
  try {
    const rows = await load()
    return Array.isArray(rows) ? rows : []
  } catch {
    return []
  }
}
