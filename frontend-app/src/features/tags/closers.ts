/**
 * Escape passes the dialog element and closes only the pane that owns it.
 * Calling either closer with no element is the deliberate global cleanup
 * (overlay dismissal, opening a shared modal). There is no selected-tag singleton.
 */
type PaneCloser = (modal?: Element | null) => boolean

const aliasClosers = new Set<PaneCloser>()
const mergeClosers = new Set<PaneCloser>()

export function registerTagsAliasCloser(close: PaneCloser): () => void {
  aliasClosers.add(close)
  return () => {
    aliasClosers.delete(close)
  }
}

export function registerTagsMergeCloser(close: PaneCloser): () => void {
  mergeClosers.add(close)
  return () => {
    mergeClosers.delete(close)
  }
}

function closeOne(closers: Set<PaneCloser>, modal?: Element | null): void {
  if (modal instanceof Element) {
    for (const close of closers) {
      if (close(modal)) return
    }
    return
  }
  for (const close of [...closers]) close(null)
}

export function closeTagsAliasModals(modal?: Element | null): void {
  closeOne(aliasClosers, modal)
}

export function closeTagsMergeModals(modal?: Element | null): void {
  closeOne(mergeClosers, modal)
}
