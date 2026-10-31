import { ref } from 'vue'
import { activatePerformanceDiagnostics } from '../performance-diagnostics/activation'
import {
  SETTINGS_NARROW_MEDIA_QUERY,
  settingsCategoryId,
  type SettingsCategoryId,
} from './categories'

/**
 * One page-session category. Not per TabContext, not device storage, not
 * the settings API, and not a hash. Main and Secondary share this modal.
 */
export const settingsActiveCategory = ref<SettingsCategoryId>('general')
export const settingsNavOrientation = ref<'vertical' | 'horizontal'>('vertical')

let diagnosticsActivated = false
let orientationMedia: MediaQueryList | null = null
let orientationHandler: (() => void) | null = null

export function readSettingsNavOrientation(): 'vertical' | 'horizontal' {
  try {
    if (typeof window.matchMedia === 'function' && window.matchMedia(SETTINGS_NARROW_MEDIA_QUERY).matches) {
      return 'horizontal'
    }
  } catch {
    /* matchMedia can throw while the viewport is unavailable. */
  }
  return 'vertical'
}

/** Synchronous: opening Settings must correct a stale aria-orientation before it returns. */
export function syncSettingsNavOrientation(): void {
  const next = readSettingsNavOrientation()
  settingsNavOrientation.value = next
  const nav = document.getElementById('prks-settings-nav')
  if (nav) nav.setAttribute('aria-orientation', next)
}

export function bindSettingsNavOrientation(): void {
  if (orientationMedia || typeof window.matchMedia !== 'function') {
    syncSettingsNavOrientation()
    return
  }
  const media = window.matchMedia(SETTINGS_NARROW_MEDIA_QUERY)
  const onChange = () => {
    syncSettingsNavOrientation()
  }
  if (typeof media.addEventListener === 'function') {
    media.addEventListener('change', onChange)
  } else if (typeof media.addListener === 'function') {
    media.addListener(onChange)
  }
  orientationMedia = media
  orientationHandler = onChange
  syncSettingsNavOrientation()
}

export function activateSettingsCategory(
  categoryId: string | null | undefined,
  options?: { focusTab?: boolean },
): void {
  const resolved = settingsCategoryId(categoryId)
  settingsActiveCategory.value = resolved
  const tab = document.getElementById('prks-settings-tab-' + resolved)
  if (tab instanceof HTMLElement) {
    if (options?.focusTab) tab.focus()
    if (typeof tab.scrollIntoView === 'function') {
      try {
        tab.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      } catch {
        /* A detached tab can reject scrolling. The category still changes. */
      }
    }
  }
  if (resolved === 'diagnostics' && !diagnosticsActivated) {
    diagnosticsActivated = true
    window.__prksPerformanceDiagnosticsRequested = true
    activatePerformanceDiagnostics()
    const load = window.prksLoadOfflineCacheStatus
    if (typeof load === 'function') void load()
  }
}

export function openSettingsToLastCategory(): void {
  syncSettingsNavOrientation()
  activateSettingsCategory(settingsActiveCategory.value)
}

export function registerSettingsNavigationBridge(target: Window = window): void {
  target.prksOpenSettingsToLastCategory = openSettingsToLastCategory
  target.prksActivateSettingsCategory = activateSettingsCategory
  target.prksSyncSettingsNavOrientation = syncSettingsNavOrientation
  bindSettingsNavOrientation()
  const pending = target.__prksSettingsOpenRequested === true
  if (pending) target.__prksSettingsOpenRequested = false
  const modal = document.getElementById('settings-modal')
  const alreadyOpen = modal != null && !modal.classList.contains('hidden')
  if (pending || alreadyOpen) openSettingsToLastCategory()
}

export function resetSettingsNavigationForTests(): void {
  if (orientationMedia && orientationHandler) {
    if (typeof orientationMedia.removeEventListener === 'function') {
      orientationMedia.removeEventListener('change', orientationHandler)
    } else if (typeof orientationMedia.removeListener === 'function') {
      orientationMedia.removeListener(orientationHandler)
    }
  }
  orientationMedia = null
  orientationHandler = null
  diagnosticsActivated = false
  settingsActiveCategory.value = 'general'
  settingsNavOrientation.value = 'vertical'
}
