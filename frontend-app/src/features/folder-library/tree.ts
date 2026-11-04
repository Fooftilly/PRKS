import type { FolderRow } from './types'

/** One visible folder-tree row. Children are indexed once per build. */
export interface FolderTreeNode {
  readonly id: string
  readonly title: string
  readonly depth: number
  readonly meta: string
  readonly match: boolean
  readonly selected: boolean
  readonly hasChildren: boolean
  readonly collapsed: boolean
  readonly childCount: number
  readonly workCount: number
  readonly children: readonly FolderTreeNode[]
}

export interface FolderTreeView {
  readonly nodes: readonly FolderTreeNode[]
  readonly empty: boolean
  readonly emptySearch: boolean
  readonly hasCollapsible: boolean
  /** Stored expand set, independent of a filter that forces branches open. */
  readonly allCollapsed: boolean
  readonly collapsibleIds: readonly string[]
}

export interface FolderTreeBuildOptions {
  readonly selectedId?: string | null
}

export interface GlancePart {
  readonly text: string
  readonly href?: string
}

const TREE_COLLATOR = { sensitivity: 'base' } as const

function compareTitle(a: FolderRow, b: FolderRow): number {
  return String(a.title || '').localeCompare(String(b.title || ''), undefined, TREE_COLLATOR)
}

export function folderTreeMetaLabel(workCount: number, childCount: number): string {
  const bits: string[] = []
  if (workCount) bits.push(`${workCount} file${workCount === 1 ? '' : 's'}`)
  if (childCount) bits.push(`${childCount} subfolder${childCount === 1 ? '' : 's'}`)
  return bits.join(' · ')
}

function indexChildren(folders: readonly FolderRow[]): Map<string, FolderRow[]> {
  const byParent = new Map<string, FolderRow[]>()
  for (const folder of folders) {
    const key = folder.parent_id ? String(folder.parent_id) : ''
    const list = byParent.get(key)
    if (list) list.push(folder)
    else byParent.set(key, [folder])
  }
  for (const list of byParent.values()) list.sort(compareTitle)
  return byParent
}

/**
 * Ids visible for a title filter: each match, its descendants, and its
 * ancestors. Descendants walk the parent index, not the whole list per node.
 * Null means no filter.
 */
function visibleIds(
  folders: readonly FolderRow[],
  query: string,
  byParent: Map<string, FolderRow[]>,
  byId: Map<string, FolderRow>,
): Set<string> | null {
  const q = query.trim().toLowerCase()
  if (!q) return null
  const matches = folders.filter((folder) => String(folder.title || '').toLowerCase().includes(q))
  if (!matches.length) return new Set()
  const visible = new Set<string>()
  const addDescendants = (id: string) => {
    const children = byParent.get(id)
    if (!children) return
    for (const child of children) {
      if (visible.has(child.id)) continue
      visible.add(child.id)
      addDescendants(child.id)
    }
  }
  for (const match of matches) {
    visible.add(match.id)
    addDescendants(match.id)
    let current: FolderRow | undefined = match
    const guard = new Set<string>()
    while (current?.parent_id && !guard.has(current.id)) {
      guard.add(current.id)
      visible.add(current.parent_id)
      current = byId.get(current.parent_id)
    }
  }
  return visible
}

export function collapsibleFolderIds(folders: readonly FolderRow[]): string[] {
  return folders.filter((folder) => Number(folder.child_count) > 0).map((folder) => folder.id)
}

export function buildFolderTree(
  folders: readonly FolderRow[],
  query: string,
  expandedIds: ReadonlySet<string>,
  options: FolderTreeBuildOptions = {},
): FolderTreeView {
  const byParent = indexChildren(folders)
  const byId = new Map(folders.map((folder) => [folder.id, folder]))
  const visible = visibleIds(folders, query, byParent, byId)
  const filtering = visible !== null
  const collapsibleIds = collapsibleFolderIds(folders)
  const hasCollapsible = collapsibleIds.length > 0
  const allCollapsed = hasCollapsible && collapsibleIds.every((id) => !expandedIds.has(id))
  const selectedId = options.selectedId == null || options.selectedId === '' ? null : String(options.selectedId)
  if (!folders.length) {
    return { nodes: [], empty: true, emptySearch: false, hasCollapsible, allCollapsed, collapsibleIds }
  }
  if (filtering && visible.size === 0) {
    return { nodes: [], empty: false, emptySearch: true, hasCollapsible, allCollapsed, collapsibleIds }
  }
  const q = query.trim().toLowerCase()
  const matchIds = new Set(
    filtering ? folders.filter((folder) => String(folder.title || '').toLowerCase().includes(q)).map((folder) => folder.id) : [],
  )

  const renderNode = (node: FolderRow, depth: number): FolderTreeNode => {
    const childCount = Number(node.child_count) || 0
    const workCount = Number(node.work_count) || 0
    const hasChildren = childCount > 0
    const childRows = (byParent.get(node.id) || []).filter((child) => !filtering || visible.has(child.id))
    const collapsed = filtering ? false : hasChildren && !expandedIds.has(node.id)
    return {
      id: node.id,
      title: node.title || 'Folder',
      depth,
      meta: folderTreeMetaLabel(workCount, childCount),
      match: filtering && matchIds.has(node.id),
      selected: selectedId !== null && selectedId === node.id,
      hasChildren,
      collapsed,
      childCount,
      workCount,
      children: hasChildren ? childRows.map((child) => renderNode(child, depth + 1)) : [],
    }
  }

  const roots = (byParent.get('') || []).filter((folder) => !filtering || visible.has(folder.id))
  return {
    nodes: roots.map((folder) => renderNode(folder, 0)),
    empty: false,
    emptySearch: false,
    hasCollapsible,
    allCollapsed,
    collapsibleIds,
  }
}

/** Parents of `folderId`, plus itself when it has children. One parent-index walk. */
export function folderDetailExpandIds(folderId: string, rows: readonly FolderRow[]): string[] {
  const id = String(folderId || '')
  if (!id) return []
  const byId = new Map(rows.map((row) => [row.id, row]))
  const ids: string[] = []
  let current = byId.get(id)
  const guard = new Set<string>()
  while (current?.parent_id && !guard.has(current.id)) {
    guard.add(current.id)
    ids.push(String(current.parent_id))
    current = byId.get(String(current.parent_id))
  }
  const self = byId.get(id)
  if (self && Number(self.child_count) > 0) ids.push(id)
  return ids
}

/** Flat subfolder rows. Same meta as the tree, never expandable. */
export function folderSubfolderNodes(children: readonly unknown[]): FolderTreeNode[] {
  const rows = children
    .map((row) => {
      if (!row || typeof row !== 'object') return null
      const record = row as { id?: unknown; title?: unknown; work_count?: unknown; child_count?: unknown }
      const id = record.id == null ? '' : String(record.id).trim()
      if (!id) return null
      const workCount = Number(record.work_count) || 0
      const childCount = Number(record.child_count) || 0
      return {
        id,
        title: record.title == null || String(record.title).trim() === '' ? 'Folder' : String(record.title),
        workCount,
        childCount,
      }
    })
    .filter((row): row is { id: string; title: string; workCount: number; childCount: number } => !!row)
    .sort((a, b) => a.title.localeCompare(b.title, undefined, TREE_COLLATOR))
  return rows.map((row) => ({
    id: row.id,
    title: row.title,
    depth: 0,
    meta: folderTreeMetaLabel(row.workCount, row.childCount),
    match: false,
    selected: false,
    hasChildren: false,
    collapsed: false,
    childCount: row.childCount,
    workCount: row.workCount,
    children: [],
  }))
}

function isSafeInternalHref(href: string): boolean {
  const value = href.trim()
  if (!value || /[\\\u0000-\u001f\u007f]/.test(value)) return false
  if (value.startsWith('#')) return !value.includes('javascript:') && !value.includes('data:')
  if (value.startsWith('/') && !value.startsWith('//')) {
    return !value.includes('javascript:') && !value.includes('data:')
  }
  return false
}

/** Page-summary parts. Unknown and empty parts are omitted. Links stay same-app. */
export function normalizeGlanceParts(parts: readonly unknown[] | null | undefined): GlancePart[] {
  if (!Array.isArray(parts)) return []
  const out: GlancePart[] = []
  for (const part of parts) {
    if (part == null || part === false) continue
    if (typeof part === 'number') {
      if (!Number.isFinite(part)) continue
      out.push({ text: String(part) })
      continue
    }
    if (typeof part === 'string') {
      const text = part.trim()
      if (text) out.push({ text })
      continue
    }
    if (typeof part !== 'object') continue
    const record = part as { text?: unknown; href?: unknown; unknown?: unknown; html?: unknown }
    if (record.unknown === true || Object.prototype.hasOwnProperty.call(record, 'html')) continue
    const text = record.text == null ? '' : String(record.text).trim()
    if (!text) continue
    const href = record.href == null ? '' : String(record.href)
    if (href && isSafeInternalHref(href)) out.push({ text, href: href.trim() })
    else out.push({ text })
  }
  return out
}

export interface FolderDetailTreeSnapshot {
  readonly status: 'ready' | 'error'
  readonly rows: readonly unknown[]
  readonly selectedId: string
  readonly selectionOnly: boolean
}

export function readFolderDetailTreeSnapshot(value: unknown): FolderDetailTreeSnapshot | null {
  if (!value || typeof value !== 'object') return null
  const record = value as {
    status?: unknown
    rows?: unknown
    selectedId?: unknown
    selectionOnly?: unknown
  }
  if (record.status !== 'ready' && record.status !== 'error') return null
  return {
    status: record.status,
    rows: Array.isArray(record.rows) ? record.rows : [],
    selectedId: record.selectedId == null ? '' : String(record.selectedId),
    selectionOnly: record.selectionOnly === true,
  }
}
