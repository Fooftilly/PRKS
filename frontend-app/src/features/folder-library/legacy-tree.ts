/**
 * Temporary bridge to shared Folder tree HTML helpers in folders.js.
 * Vue owns when the tree paints and when expand-all runs; markup generation
 * stays with the shared hierarchy helpers used by folder-detail.
 */

import type { FolderLibraryItem } from './types'

export function legacyFolderTreeHtml(
  folders: readonly FolderLibraryItem[],
  filterQuery: string,
): string {
  const fn = window.prksFolderLibraryTreeInnerHtml
  if (typeof fn === 'function') return fn(folders, filterQuery)
  if (!folders.length) {
    return (
      '<div class="prks-folder-tree__empty-state">' +
      '<p class="prks-inline-message prks-folder-tree__empty">No folders yet.</p>' +
      '<button type="button" class="prks-btn prks-btn--primary prks-folder-tree__create-btn" data-prks-create-folder-query="">New folder</button>' +
      '</div>'
    )
  }
  return '<div class="prks-folder-tree" role="tree"></div>'
}

export function folderTreeHasCollapsibleNodes(folders: readonly FolderLibraryItem[]): boolean {
  const fn = window.prksFolderTreeHasCollapsibleNodes
  if (typeof fn === 'function') return !!fn(folders)
  return folders.some((f) => Number(f.child_count || 0) > 0)
}

export function folderLibraryExpandToggleLabel(folders: readonly FolderLibraryItem[]): string {
  const fn = window.prksFolderLibraryExpandToggleLabel
  if (typeof fn === 'function') return fn(folders)
  return 'Expand all'
}

export function folderLibraryExpandToggleInnerHtml(): string {
  const fn = window.prksFolderLibraryExpandToggleInnerHtml
  if (typeof fn === 'function') return fn()
  return '<span class="ribbon-btn__icon">▾</span>'
}

export function folderTreeAllCollapsed(folders: readonly FolderLibraryItem[]): boolean {
  const fn = window.prksFolderTreeAllCollapsed
  if (typeof fn === 'function') return !!fn(folders)
  return true
}

export function legacyWorkCardHtml(
  work: {
    id?: unknown
    title?: unknown
    status?: unknown
    abstract_excerpt?: unknown
    abstract?: unknown
  },
  options: { subtitle?: string; suppressThumbnail?: boolean },
): string {
  const fn = window.prksWorkCardHtml
  if (typeof fn !== 'function') return ''
  return fn(work, options)
}

export function workBrowseCollectionClass(extraClass?: string): string {
  const fn = window.prksWorkBrowseCollectionClass
  return typeof fn === 'function' ? fn(extraClass || 'prks-folder-library__grid') : 'prks-folder-library__grid card-grid'
}

export function workBrowseModeToggleHtml(hiddenId: string): string {
  const fn = window.prksWorkBrowseModeToggleHtml
  return typeof fn === 'function' ? fn(hiddenId) : ''
}

export function pageSummaryHtml(parts: Array<string | { text: string; href?: string } | null>): string {
  const fn = window.prksPageSummaryHtml
  if (typeof fn !== 'function') return ''
  return fn({
    parts: parts.filter((p) => p != null) as Array<string | { text: string; href?: string }>,
    ariaLabel: 'Library at a glance',
  })
}

export function tagSearchIconHtml(): string {
  const fn = window.prksTagSearchIconHtml
  return typeof fn === 'function' ? fn() : ''
}
