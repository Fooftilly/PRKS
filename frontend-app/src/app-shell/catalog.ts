import { PEOPLE_ROLES, type PeopleRole } from '../domain/people-roles'
import { WORK_STATUSES, type WorkStatus } from '../domain/work-status'
import { progressCanonicalHash } from '../routing/route-model'
import type { NavTarget } from './chrome'

export interface NavLinkItem extends NavTarget {
  label: string
  icon: string
  /** People role vocabulary. The icon registry owns the glyph. */
  role?: string
}

/**
 * Sidebar lists the domain statuses with the last two swapped.
 * `WORK_STATUSES` ends Completed, Paused; the sidebar lists Paused, then Completed.
 */
function sidebarProgressOrder(): readonly WorkStatus[] {
  const earlier = WORK_STATUSES.slice(0, -2)
  const beforeLast = WORK_STATUSES[WORK_STATUSES.length - 2]
  const last = WORK_STATUSES[WORK_STATUSES.length - 1]
  return [...earlier, last, beforeLast]
}

const PEOPLE_SHORTCUT_LABEL: Record<PeopleRole, string> = {
  Author: 'Authors',
  Editor: 'Editors',
  Reviewer: 'Reviewers',
  Translator: 'Translators',
  Introduction: 'Introduction writers',
  Foreword: 'Foreword writers',
  Afterword: 'Afterword writers',
}

export const LIBRARY_LINKS: readonly NavLinkItem[] = [
  { href: '#/folders', label: 'Folders', icon: 'folder' },
  { href: '#/recent', label: 'Recent', icon: 'clock' },
  { href: '#/views', label: 'Saved Views', icon: 'bookmark' },
  { href: '#/types', label: 'File Types', icon: 'library' },
  { href: '#/playlists', label: 'Playlists', icon: 'clapperboard' },
]

export const RESEARCH_LINKS: readonly NavLinkItem[] = [
  { href: '#/concepts', label: 'Concepts', icon: 'network' },
  { href: '#/positions', label: 'Positions', icon: 'flag' },
  { href: '#/arguments', label: 'Arguments & Stances', icon: 'messages-square' },
  { href: '#/graph', label: 'Graph', icon: 'share-2' },
]

export const ORGANIZE_LINKS: readonly NavLinkItem[] = [
  { href: '#/tags', label: 'Tags', icon: 'tags' },
  { href: '#/publishers', label: 'Publishers', icon: 'building-2' },
]

export const PROCESSING_LINK: NavLinkItem = {
  href: '#/processing-files',
  label: 'Files for Processing',
  icon: 'inbox',
}

export const PEOPLE_INDEX: NavLinkItem = { href: '#/people', label: 'People', icon: 'users' }

export const GROUP_LINK: NavLinkItem = { href: '#/people/groups', label: 'Groups', icon: 'folders' }

export function peopleRoleLinks(): NavLinkItem[] {
  return PEOPLE_ROLES.map((role) => ({
    href: '#/people/role/' + role,
    label: PEOPLE_SHORTCUT_LABEL[role],
    icon: '',
    role,
  }))
}

export function progressLinks(): NavLinkItem[] {
  return sidebarProgressOrder().map((status) => ({
    href: progressCanonicalHash(status),
    label: status,
    icon: '',
    status,
  }))
}

export function allNavTargets(): NavTarget[] {
  return [
    ...LIBRARY_LINKS,
    PEOPLE_INDEX,
    ...peopleRoleLinks(),
    GROUP_LINK,
    ...RESEARCH_LINKS,
    ...ORGANIZE_LINKS,
    ...progressLinks(),
    PROCESSING_LINK,
  ]
}
