/**
 * Keyboard completion for the create forms (#603, #589).
 *
 * Enter in a form's primary name field presses Save, and Ctrl/Cmd+Enter
 * anywhere in the form does too, as in New File. Enter in a textarea keeps
 * its new line, and Enter in a combobox stays with the combobox, which picks
 * a row. A key the IME is composing, or one a field already handled, is left
 * alone.
 */
export function createSubmitKeyHandler(
  primaryFieldIds: readonly string[],
  submit: () => void,
): (event: KeyboardEvent) => void {
  const primary = new Set(primaryFieldIds)
  return (event: KeyboardEvent) => {
    if (event.key !== 'Enter' || event.isComposing || event.defaultPrevented) return
    if (event.altKey || event.shiftKey) return
    const target = event.target
    const chord = event.ctrlKey || event.metaKey
    if (!chord) {
      if (!(target instanceof HTMLInputElement) || !primary.has(target.id)) return
    }
    event.preventDefault()
    submit()
  }
}

/** Moves focus to a form's first field when its dialog opens. */
export function focusField(id: string): void {
  const el = document.getElementById(id)
  if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return
  try {
    el.focus({ preventScroll: true })
  } catch {
    el.focus()
  }
}

/**
 * Binds the shared combobox keyboard model (`frontend/js/combobox-keys.js`):
 * ArrowUp/ArrowDown move the highlight, Enter picks the highlighted row or
 * the first real match, and an open list closes on Escape before the modal.
 */
export function bindComboboxKeys(
  inputId: string,
  resultsId: string,
  options: { onOpen: () => void; onEscape: () => void; enterCreates?: boolean },
): void {
  const input = document.getElementById(inputId)
  const results = document.getElementById(resultsId)
  if (!(input instanceof HTMLInputElement) || !(results instanceof HTMLElement)) return
  if (typeof window.prksBindComboboxKeys !== 'function') return
  window.prksBindComboboxKeys(input, results, options)
}

/**
 * Tells the combobox keyboard model that the list now shows the rows for the
 * current query. Vue may patch nothing when a keystroke leaves the matches
 * unchanged, so the row observer alone cannot clear the stale mark.
 */
export function markComboboxRendered(inputId: string, after: Promise<void>): void {
  const input = document.getElementById(inputId)
  if (!(input instanceof HTMLInputElement)) return
  const epochOf = window.prksComboboxRenderEpoch
  const rendered = window.prksComboboxRendered
  if (typeof epochOf !== 'function' || typeof rendered !== 'function') return
  const epoch = epochOf(input)
  void after.then(() => rendered(input, epoch))
}
