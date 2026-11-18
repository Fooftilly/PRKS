import { nextTick, onBeforeUnmount, onMounted } from 'vue'
import { useComboboxPanel } from '../combobox-panel'
import { bindComboboxKeys, markComboboxRendered } from '../keyboard'

/**
 * One New File inline result list (Folder, People, Tags, Playlist).
 *
 * Vue owns the rows and the open/closed classes; the shared keyboard model
 * (`combobox-keys.js`) owns the highlight. Escape closes an open list before
 * the dialog, through `close()`.
 */
export function useInlineCombobox(
  inputId: string,
  resultsId: string,
  options: { onOpen: () => void; enterCreates?: boolean },
) {
  const panel = useComboboxPanel()
  let blurTimer: number | null = null
  let scrollTimer: number | null = null

  function cancelBlur(): void {
    if (blurTimer == null) return
    window.clearTimeout(blurTimer)
    blurTimer = null
  }

  function input(): HTMLInputElement | null {
    const el = document.getElementById(inputId)
    return el instanceof HTMLInputElement ? el : null
  }

  /** The rows were just set for the current query: show them. */
  function shown(): void {
    cancelBlur()
    panel.show()
    markComboboxRendered(inputId, nextTick())
    const el = input()
    if (scrollTimer != null) window.clearTimeout(scrollTimer)
    scrollTimer = window.setTimeout(() => {
      scrollTimer = null
      // Closed, or focus moved on, meanwhile: do not scroll a list no one is using.
      if (!el || !panel.open.value || document.activeElement !== el) return
      if (typeof el.scrollIntoView !== 'function') return
      try {
        el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      } catch {
        el.scrollIntoView({ block: 'nearest' })
      }
    }, 40)
  }

  /** Blur waits for a row's mousedown; a refocus meanwhile keeps the list. */
  function onBlur(): void {
    cancelBlur()
    blurTimer = window.setTimeout(() => {
      blurTimer = null
      if (document.activeElement === input()) return
      panel.hide()
    }, 200)
  }

  function close(): boolean {
    if (!panel.open.value) return false
    cancelBlur()
    panel.hideNow()
    return true
  }

  onMounted(() => {
    bindComboboxKeys(inputId, resultsId, {
      onOpen: options.onOpen,
      onEscape: () => panel.hideNow(),
      enterCreates: options.enterCreates,
    })
  })

  onBeforeUnmount(() => {
    cancelBlur()
    if (scrollTimer != null) window.clearTimeout(scrollTimer)
  })

  return {
    hidden: panel.hidden,
    open: panel.open,
    shown,
    hide: panel.hide,
    hideNow: panel.hideNow,
    onBlur,
    cancelBlur,
    close,
  }
}

export type InlineCombobox = ReturnType<typeof useInlineCombobox>
