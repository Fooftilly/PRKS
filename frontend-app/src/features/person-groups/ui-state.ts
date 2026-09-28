/**
 * Ephemeral Person Groups index chrome.
 * Keyed by TabContext so Main and Secondary do not share search or collapse.
 * Default tree nodes stay collapsed; the set records the ones this pane expanded.
 */

const searchByOwner = new Map<string, string>()
const expandedByOwner = new Map<string, Set<string>>()

export function readGroupSearch(ownerKey: string): string {
  if (!ownerKey) return ''
  return searchByOwner.get(ownerKey) || ''
}

export function writeGroupSearch(ownerKey: string, query: string): void {
  if (!ownerKey) return
  const value = String(query || '')
  if (value) searchByOwner.set(ownerKey, value)
  else searchByOwner.delete(ownerKey)
}

export function isGroupExpanded(ownerKey: string, groupId: string): boolean {
  const expanded = expandedByOwner.get(ownerKey)
  return !!expanded && expanded.has(String(groupId))
}

export function toggleGroupExpanded(ownerKey: string, groupId: string): void {
  if (!ownerKey || !groupId) return
  const expanded = new Set(expandedByOwner.get(ownerKey) || [])
  const id = String(groupId)
  if (expanded.has(id)) expanded.delete(id)
  else expanded.add(id)
  if (expanded.size) expandedByOwner.set(ownerKey, expanded)
  else expandedByOwner.delete(ownerKey)
}

export function setGroupsExpanded(ownerKey: string, groupIds: readonly string[], expanded: boolean): void {
  if (!ownerKey) return
  if (!expanded) {
    expandedByOwner.delete(ownerKey)
    return
  }
  expandedByOwner.set(ownerKey, new Set(groupIds.map((id) => String(id))))
}

export function resetPersonGroupUiStateForTests(): void {
  searchByOwner.clear()
  expandedByOwner.clear()
}
