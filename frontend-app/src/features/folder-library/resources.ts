/**
 * Folder Library ownership of shared Work-card browser resources (#170).
 * Preview and lazy-thumb observation must not outlive this surface's DOM.
 * Product semantics stay on window helpers; this module only owns when to
 * release/re-init relative to Vue mount, tab switch, and in-place paint.
 */

export function releaseFolderLibraryBrowseResources(root: ParentNode | null | undefined): void {
  if (!root) return
  const releasePreview = window.prksReleaseWorkThumbPreview
  if (typeof releasePreview === 'function') releasePreview(root)
  const releaseThumbs = window.prksReleaseLazyWorkThumbs
  if (typeof releaseThumbs === 'function') releaseThumbs(root)
}

export function initFolderLibraryLazyThumbs(root: ParentNode | null | undefined): void {
  if (!root) return
  const init = window.prksInitLazyWorkThumbs
  if (typeof init === 'function') init(root)
}

/**
 * Replace browse-card HTML: release observers/preview for the pane first,
 * paint, then re-init lazy thumbs when allowed (not cache-suppressed).
 */
export function replaceFolderLibraryBrowseHtml(
  pane: HTMLElement | null | undefined,
  html: string,
  options?: { initLazyThumbs?: boolean },
): void {
  if (!pane) return
  releaseFolderLibraryBrowseResources(pane)
  pane.innerHTML = html
  if (options?.initLazyThumbs !== false) {
    initFolderLibraryLazyThumbs(pane)
  }
}
