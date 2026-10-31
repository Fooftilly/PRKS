import { onBeforeUnmount, ref } from 'vue'

/** Matches `PRKS_INLINE_COMBOBOX_COLLAPSE_MS` in `ui.js`. */
export const COMBOBOX_COLLAPSE_MS = 220

/**
 * Open/closed classes for a `.combobox-results--tag-panel`.
 * Vue owns the class list so a re-render cannot wipe `is-open`.
 */
export function useComboboxPanel() {
  const hidden = ref(true)
  const open = ref(false)
  let timer: number | null = null

  function clearTimer(): void {
    if (timer == null) return
    window.clearTimeout(timer)
    timer = null
  }

  function show(): void {
    clearTimer()
    hidden.value = false
    open.value = true
  }

  function hide(): void {
    open.value = false
    clearTimer()
    timer = window.setTimeout(() => {
      hidden.value = true
      timer = null
    }, COMBOBOX_COLLAPSE_MS)
  }

  function hideNow(): void {
    clearTimer()
    open.value = false
    hidden.value = true
  }

  onBeforeUnmount(clearTimer)

  return { hidden, open, show, hide, hideNow }
}
