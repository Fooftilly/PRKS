import { nextTick } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPrksQueryClient } from '../../query/client'
import { resetPerformanceDiagnosticsActivationForTests } from '../performance-diagnostics/activation'
import { SETTINGS_NARROW_MEDIA_QUERY } from './categories'
import {
  activateSettingsCategory,
  openSettingsToLastCategory,
  resetSettingsNavigationForTests,
  settingsActiveCategory,
  syncSettingsNavOrientation,
} from './navigation'
import SettingsSurface from './SettingsSurface.vue'

const mounted: VueWrapper[] = []

afterEach(() => {
  for (const wrapper of mounted.splice(0)) wrapper.unmount()
  document.body.innerHTML = ''
  resetSettingsNavigationForTests()
  resetPerformanceDiagnosticsActivationForTests()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function installMatchMedia(matches: boolean): { listeners: Array<() => void>; queries: string[] } {
  const listeners: Array<() => void> = []
  const queries: string[] = []
  vi.stubGlobal('matchMedia', (query: string) => {
    queries.push(query)
    return {
      matches,
      media: query,
      addEventListener: (_type: string, listener: () => void) => {
        listeners.push(listener)
      },
      removeEventListener: () => {},
      addListener: (listener: () => void) => {
        listeners.push(listener)
      },
      removeListener: () => {},
      dispatchEvent: () => true,
      onchange: null,
    }
  })
  return { listeners, queries }
}

function mountSurface(): VueWrapper {
  const wrapper = mount(SettingsSurface, {
    attachTo: document.body,
    global: { plugins: [[VueQueryPlugin, { queryClient: createPrksQueryClient() }]] },
  })
  mounted.push(wrapper)
  return wrapper
}

describe('Settings navigation', () => {
  it('keeps one in-memory category and does not store it', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const focused = vi.fn()
    const page = window as Window & { prksGetFocusedTabContext?: () => unknown }
    page.prksGetFocusedTabContext = focused
    activateSettingsCategory('backup')
    expect(settingsActiveCategory.value).toBe('backup')
    activateSettingsCategory('not-a-category')
    expect(settingsActiveCategory.value).toBe('general')
    expect(setItem).not.toHaveBeenCalled()
    expect(focused).not.toHaveBeenCalled()
    delete page.prksGetFocusedTabContext
  })

  it('corrects aria-orientation synchronously from the shared breakpoint', () => {
    const { queries } = installMatchMedia(false)
    const nav = document.createElement('nav')
    nav.id = 'prks-settings-nav'
    nav.setAttribute('aria-orientation', 'horizontal')
    document.body.appendChild(nav)
    syncSettingsNavOrientation()
    expect(queries).toContain(SETTINGS_NARROW_MEDIA_QUERY)
    expect(nav.getAttribute('aria-orientation')).toBe('vertical')
    openSettingsToLastCategory()
    expect(nav.getAttribute('aria-orientation')).toBe('vertical')
  })

  it('asks for diagnostics and the offline cache only on the first Diagnostics activation', () => {
    const load = vi.fn()
    window.prksLoadOfflineCacheStatus = load
    activateSettingsCategory('diagnostics')
    activateSettingsCategory('general')
    activateSettingsCategory('diagnostics')
    expect(load).toHaveBeenCalledTimes(1)
    expect(window.__prksPerformanceDiagnosticsRequested).toBe(true)
  })
})

describe('Settings surface', () => {
  it('switches categories from the keyboard without removing inactive panels', async () => {
    installMatchMedia(false)
    window.prksLoadAppSettings = vi.fn(async () => ({}))
    mountSurface()
    await nextTick()
    const backup = document.getElementById('prks-backup-download-btn')
    expect(backup).not.toBeNull()
    const general = document.getElementById('prks-settings-tab-general')
    expect(general).toBeInstanceOf(HTMLButtonElement)
    general?.focus()
    general?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
    expect(document.activeElement?.id).toBe('prks-settings-tab-reading')
    await nextTick()
    const readingPanel = document.getElementById('prks-settings-panel-reading')
    expect(readingPanel?.hasAttribute('hidden')).toBe(false)
    expect(readingPanel?.hasAttribute('inert')).toBe(false)
    expect(document.getElementById('prks-settings-panel-general')?.hasAttribute('hidden')).toBe(true)
    expect(document.getElementById('prks-settings-panel-general')?.hasAttribute('inert')).toBe(true)
    expect(document.getElementById('prks-backup-download-btn')).toBe(backup)
    const reading = document.getElementById('prks-settings-tab-reading')
    reading?.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }))
    expect(document.activeElement?.id).toBe('prks-settings-tab-diagnostics')
    reading?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true }))
    expect(document.activeElement?.id).toBe('prks-settings-tab-general')
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    document.getElementById('prks-settings-nav')?.dispatchEvent(escape)
    expect(escape.defaultPrevented).toBe(false)
  })

  it('keeps a typed annotation author when the category changes and patches through the settings API', async () => {
    vi.useFakeTimers()
    installMatchMedia(false)
    window.prksLoadAppSettings = vi.fn(async () => ({}))
    const cache = vi.fn()
    const patch = vi.fn(async () => ({}))
    window.prksSetAnnotationAuthorCache = cache
    window.prksPatchAppSettings = patch
    mountSurface()
    await nextTick()
    const input = document.getElementById('annotation-author-input')
    expect(input).toBeInstanceOf(HTMLInputElement)
    if (!(input instanceof HTMLInputElement)) return
    input.value = 'Persisted Author'
    input.dispatchEvent(new Event('input'))
    expect(cache).toHaveBeenCalledWith('Persisted Author')
    expect(patch).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(450)
    expect(patch).toHaveBeenCalledWith({ annotation_author: 'Persisted Author' })
    document.getElementById('prks-settings-tab-reading')?.click()
    await nextTick()
    document.getElementById('prks-settings-tab-general')?.click()
    await nextTick()
    expect((document.getElementById('annotation-author-input') as HTMLInputElement).value).toBe('Persisted Author')
    vi.useRealTimers()
  })

  it('writes device toggles through the existing preference functions', async () => {
    installMatchMedia(false)
    window.prksLoadAppSettings = vi.fn(async () => ({}))
    window.prksGetHintsEnabled = () => false
    const setHints = vi.fn()
    const applyHints = vi.fn()
    const closeHints = vi.fn()
    const applyMobile = vi.fn()
    window.prksSetHintsEnabled = setHints
    window.prksApplyHintsPreferenceToDocument = applyHints
    window.prksCloseHintPopover = closeHints
    window.prksGetForceMobileEnabled = () => false
    window.prksApplyForceMobileEnabled = applyMobile
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    mountSurface()
    await nextTick()
    document.getElementById('prks-setting-ui-hints')?.click()
    expect(setHints).toHaveBeenCalledWith(true)
    expect(applyHints).toHaveBeenCalled()
    expect(closeHints).toHaveBeenCalled()
    document.getElementById('prks-settings-tab-reading')?.click()
    await nextTick()
    document.getElementById('prks-setting-force-mobile')?.click()
    expect(applyMobile).toHaveBeenCalledWith(true)
    expect(setItem).not.toHaveBeenCalled()
  })

  it('derives the BibTeX summary from the same flags as the toggles', async () => {
    installMatchMedia(false)
    window.PRKS_BIBTEX_EXPORT_FIELD_DEFS = [
      ['author', 'Author'],
      ['year', 'Year'],
    ]
    window.prksLoadAppSettings = vi.fn(async () => ({}))
    window.__prksBibtexExportFields = { author: true, year: true }
    const patch = vi.fn(async () => ({}))
    window.prksPatchAppSettings = patch
    window.prksSetBibtexExportFieldsCache = vi.fn()
    mountSurface()
    document.getElementById('prks-settings-tab-export')?.click()
    await nextTick()
    await nextTick()
    const summary = document.getElementById('prks-bibtex-export-summary')
    expect(summary?.textContent).toBe('2 of 2 fields included')
    const first = document.querySelector<HTMLButtonElement>('#prks-bibtex-export-fields button.prks-toggle')
    expect(first?.getAttribute('aria-checked')).toBe('true')
    first?.click()
    await nextTick()
    expect(first?.getAttribute('aria-checked')).toBe('false')
    expect(summary?.textContent).toBe('1 of 2 fields included')
  })

  it('does not restore a backup until the separate confirmation is submitted', async () => {
    installMatchMedia(false)
    window.prksLoadAppSettings = vi.fn(async () => ({}))
    const open = vi.fn()
    const restore = vi.fn()
    window.openModal = open
    window.prksRestoreBackup = restore
    mountSurface()
    await nextTick()
    document.getElementById('prks-backup-restore-btn')?.click()
    document.getElementById('prks-backup-verify-btn')?.click()
    await nextTick()
    expect(document.getElementById('prks-backup-restore-status')?.textContent).toBe('Choose a backup file first.')
    expect(open).not.toHaveBeenCalled()
    expect(restore).not.toHaveBeenCalled()
  })
})
