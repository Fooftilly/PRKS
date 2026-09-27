/**
 * PRKS-owned quick-preview and lazy-thumbnail lifetime (#170).
 * VueUse is not used here — preview ownership stays in legacy helpers.
 */

export function releaseWorkThumbResources(root: ParentNode | null | undefined): void {
  if (!root) return
  if (typeof window.prksHideWorkThumbPreview === 'function') {
    window.prksHideWorkThumbPreview()
  }
  if (typeof window.prksReleaseWorkThumbPreview === 'function') {
    window.prksReleaseWorkThumbPreview(root)
  }
  if (typeof window.prksReleaseLazyWorkThumbs === 'function') {
    window.prksReleaseLazyWorkThumbs(root)
  }
}

export function initLazyWorkThumbs(root: ParentNode | null | undefined): void {
  if (!root) return
  if (typeof window.prksInitLazyWorkThumbs === 'function') {
    window.prksInitLazyWorkThumbs(root)
  }
}
