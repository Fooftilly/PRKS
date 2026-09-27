import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  initFolderLibraryLazyThumbs,
  releaseFolderLibraryBrowseResources,
  replaceFolderLibraryBrowseHtml,
} from './resources'

afterEach(() => {
  delete window.prksReleaseWorkThumbPreview
  delete window.prksReleaseLazyWorkThumbs
  delete window.prksInitLazyWorkThumbs
  document.body.innerHTML = ''
})

describe('Folder Library browse resource ownership (#170)', () => {
  it('releases preview and lazy thumbs for the owning root', () => {
    const releasePreview = vi.fn()
    const releaseThumbs = vi.fn()
    window.prksReleaseWorkThumbPreview = releasePreview
    window.prksReleaseLazyWorkThumbs = releaseThumbs
    const root = document.createElement('div')
    releaseFolderLibraryBrowseResources(root)
    expect(releasePreview).toHaveBeenCalledWith(root)
    expect(releaseThumbs).toHaveBeenCalledWith(root)
  })

  it('replaces browse HTML only after releasing prior observers', () => {
    const order: string[] = []
    window.prksReleaseWorkThumbPreview = () => {
      order.push('preview')
    }
    window.prksReleaseLazyWorkThumbs = () => {
      order.push('thumbs')
    }
    window.prksInitLazyWorkThumbs = () => {
      order.push('init')
    }
    const pane = document.createElement('div')
    document.body.appendChild(pane)
    pane.innerHTML = '<img data-old="1">'
    replaceFolderLibraryBrowseHtml(pane, '<div data-new="1"></div>', { initLazyThumbs: true })
    expect(order).toEqual(['preview', 'thumbs', 'init'])
    expect(pane.querySelector('[data-new]')).not.toBeNull()
    expect(pane.querySelector('[data-old]')).toBeNull()
  })

  it('skips lazy re-init when cache-suppressed', () => {
    const init = vi.fn()
    window.prksReleaseWorkThumbPreview = () => {}
    window.prksReleaseLazyWorkThumbs = () => {}
    window.prksInitLazyWorkThumbs = init
    const pane = document.createElement('div')
    replaceFolderLibraryBrowseHtml(pane, '<div></div>', { initLazyThumbs: false })
    expect(init).not.toHaveBeenCalled()
  })

  it('init helper is a no-op without the shared API', () => {
    const pane = document.createElement('div')
    expect(() => initFolderLibraryLazyThumbs(pane)).not.toThrow()
  })
})
