/**
 * `prksIcon` writes a `data-lucide` placeholder. Refresh only the owning tree
 * host, and only while a placeholder is still in that host, so a later paint
 * cannot leave an empty icon or rewrite icons outside this tree.
 */
export function refreshFolderIcons(root: ParentNode | null | undefined): void {
  if (!root || typeof window.prksRefreshIcons !== 'function') return
  if (root instanceof Element && !root.querySelector('[data-lucide]')) return
  window.prksRefreshIcons(root)
}

export function folderTreeIconHost(node: HTMLElement | null | undefined): HTMLElement | null {
  if (!node) return null
  return node.closest('[data-prks-folder-tree-host]') ?? node
}
