import type {
  PersonGroupChildItem,
  PersonGroupDetail,
  PersonGroupIndexItem,
  PersonGroupMemberItem,
  PersonGroupParentItem,
  PersonGroupsAvailability,
} from './types'

export interface PersonGroupsIndexProjection {
  readonly availability: PersonGroupsAvailability
  readonly groups: readonly PersonGroupIndexItem[]
  readonly generation: number
}

export interface PersonGroupDetailProjection {
  readonly availability: PersonGroupsAvailability
  readonly group: PersonGroupDetail | null
  readonly groupId: string
  readonly editing: boolean
  readonly membersEditing: boolean
  readonly editorActive: boolean
  readonly generation: number
}

export interface GroupTreeNode {
  readonly id: string
  readonly name: string
  readonly depth: number
  readonly meta: string
  readonly match: boolean
  readonly hasChildren: boolean
  readonly collapsed: boolean
  readonly children: readonly GroupTreeNode[]
}

export interface GroupTreeView {
  readonly nodes: readonly GroupTreeNode[]
  readonly emptySearch: boolean
  readonly hasCollapsible: boolean
  readonly allCollapsed: boolean
}

function text(value: unknown): string {
  return value == null ? '' : String(value)
}

function count(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

export function personGroupIndexItem(row: unknown): PersonGroupIndexItem | null {
  if (!row || typeof row !== 'object') return null
  const record = row as { id?: unknown; name?: unknown; parent_id?: unknown; member_count?: unknown; child_count?: unknown }
  const id = text(record.id)
  if (!id) return null
  return {
    id,
    name: text(record.name) || 'Group',
    parentId: text(record.parent_id),
    memberCount: count(record.member_count),
    childCount: count(record.child_count),
  }
}

function parentOf(value: unknown): PersonGroupParentItem | null {
  if (!value || typeof value !== 'object') return null
  const record = value as { id?: unknown; name?: unknown }
  const id = text(record.id)
  if (!id) return null
  return { id, name: text(record.name) || 'Group' }
}

function childrenOf(value: unknown): PersonGroupChildItem[] {
  if (!Array.isArray(value)) return []
  return value
    .map((row) => {
      if (!row || typeof row !== 'object') return null
      const record = row as { id?: unknown; name?: unknown }
      const id = text(record.id)
      if (!id) return null
      return { id, name: text(record.name) || 'Group' }
    })
    .filter((row): row is PersonGroupChildItem => !!row)
}

function membersOf(value: unknown): PersonGroupMemberItem[] {
  if (!Array.isArray(value)) return []
  return value
    .map((row) => {
      if (!row || typeof row !== 'object') return null
      const record = row as { id?: unknown; first_name?: unknown; last_name?: unknown }
      const id = text(record.id)
      if (!id) return null
      const lifespan =
        typeof window.personLifespanDisplay === 'function'
          ? text(window.personLifespanDisplay(record as { birth_date?: string; death_date?: string }))
          : ''
      return {
        id,
        firstName: text(record.first_name),
        lastName: text(record.last_name),
        lifespan,
      }
    })
    .filter((row): row is PersonGroupMemberItem => !!row)
}

export function personGroupDetailFromRow(row: unknown, groupId = ''): PersonGroupDetail | null {
  if (!row || typeof row !== 'object') return null
  const record = row as {
    id?: unknown
    name?: unknown
    description?: unknown
    parent?: unknown
    children?: unknown
    members?: unknown
  }
  const id = text(record.id) || groupId
  if (!id) return null
  return {
    id,
    name: text(record.name) || 'Group',
    description: text(record.description),
    parent: parentOf(record.parent),
    children: childrenOf(record.children),
    members: membersOf(record.members),
  }
}

export function buildPersonGroupsIndexProjection(input: {
  availability?: PersonGroupsAvailability
  items?: unknown
  generation?: number
}): PersonGroupsIndexProjection {
  const availability = input.availability === 'unavailable' ? 'unavailable' : 'ready'
  const groups = Array.isArray(input.items)
    ? input.items.map(personGroupIndexItem).filter((row): row is PersonGroupIndexItem => !!row)
    : []
  return {
    availability,
    groups,
    generation: typeof input.generation === 'number' ? input.generation : 0,
  }
}

export function buildPersonGroupDetailProjection(input: {
  availability?: PersonGroupsAvailability
  group?: unknown
  groupId?: string
  editing?: boolean
  membersEditing?: boolean
  editorActive?: boolean
  generation?: number
}): PersonGroupDetailProjection {
  const availability: PersonGroupsAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found' ? input.availability : 'ready'
  const groupId = text(input.groupId)
  const group = availability === 'ready' ? personGroupDetailFromRow(input.group, groupId) : null
  return {
    availability: group || availability !== 'ready' ? availability : 'not-found',
    group,
    groupId: group?.id || groupId,
    editing: input.editing === true && !!group,
    membersEditing: input.membersEditing === true && !!group && input.editing !== true,
    editorActive: input.editorActive !== false,
    generation: typeof input.generation === 'number' ? input.generation : 0,
  }
}

function compareName(a: { name: string; id: string }, b: { name: string; id: string }): number {
  const name = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
  return name || a.id.localeCompare(b.id)
}

function metaLabel(node: PersonGroupIndexItem): string {
  const bits: string[] = []
  if (node.memberCount) bits.push(`${node.memberCount} member${node.memberCount === 1 ? '' : 's'}`)
  if (node.childCount) bits.push(`${node.childCount} subgroup${node.childCount === 1 ? '' : 's'}`)
  return bits.join(' · ')
}

function visibleIds(groups: readonly PersonGroupIndexItem[], query: string): Set<string> | null {
  const q = query.trim().toLowerCase()
  if (!q) return null
  const byId = new Map(groups.map((group) => [group.id, group]))
  const matches = groups.filter((group) => group.name.toLowerCase().includes(q)).map((group) => group.id)
  if (!matches.length) return new Set()
  const visible = new Set<string>()
  const descendants = (id: string) => {
    groups
      .filter((group) => group.parentId === id)
      .forEach((child) => {
        visible.add(child.id)
        descendants(child.id)
      })
  }
  matches.forEach((id) => {
    visible.add(id)
    descendants(id)
    let current = byId.get(id)
    const guard = new Set<string>()
    while (current && current.parentId && !guard.has(current.id)) {
      guard.add(current.id)
      visible.add(current.parentId)
      current = byId.get(current.parentId)
    }
  })
  return visible
}

export function collapsibleGroupIds(groups: readonly PersonGroupIndexItem[]): string[] {
  return groups
    .filter((group) => group.childCount > 0 || groups.some((row) => row.parentId === group.id))
    .map((group) => group.id)
}

export function buildGroupTree(
  groups: readonly PersonGroupIndexItem[],
  query: string,
  expandedIds: ReadonlySet<string>,
): GroupTreeView {
  const visible = visibleIds(groups, query)
  const filtering = visible !== null
  const collapsible = collapsibleGroupIds(groups)
  const hasCollapsible = collapsible.length > 0
  const allCollapsed = hasCollapsible && collapsible.every((id) => !expandedIds.has(id))
  if (filtering && visible.size === 0) {
    return { nodes: [], emptySearch: true, hasCollapsible, allCollapsed }
  }
  const matchIds = new Set(
    filtering ? groups.filter((group) => group.name.toLowerCase().includes(query.trim().toLowerCase())).map((group) => group.id) : [],
  )
  const childrenOf = (parentId: string) =>
    groups
      .filter((group) => group.parentId === parentId && (!filtering || visible.has(group.id)))
      .slice()
      .sort(compareName)

  const renderNode = (node: PersonGroupIndexItem, depth: number): GroupTreeNode => {
    const children = childrenOf(node.id)
    const hasChildren = children.length > 0
    const collapsed = filtering ? false : hasChildren && !expandedIds.has(node.id)
    return {
      id: node.id,
      name: node.name,
      depth,
      meta: metaLabel(node),
      match: filtering && matchIds.has(node.id),
      hasChildren,
      collapsed,
      children: hasChildren ? children.map((child) => renderNode(child, depth + 1)) : [],
    }
  }

  const roots = groups
    .filter((group) => !group.parentId && (!filtering || visible.has(group.id)))
    .slice()
    .sort(compareName)
    .map((group) => renderNode(group, 0))
  return { nodes: roots, emptySearch: false, hasCollapsible, allCollapsed }
}
