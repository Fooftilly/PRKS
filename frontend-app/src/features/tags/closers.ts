/**
 * Escape and overlay dismissal call the classic `prksCloseTags*Modal` names.
 * Those names close whichever owner's dialog is open. They do not keep a
 * window singleton of the selected tag.
 */
const aliasClosers = new Set<() => void>()
const mergeClosers = new Set<() => void>()

export function registerTagsAliasCloser(close: () => void): () => void {
  aliasClosers.add(close)
  return () => {
    aliasClosers.delete(close)
  }
}

export function registerTagsMergeCloser(close: () => void): () => void {
  mergeClosers.add(close)
  return () => {
    mergeClosers.delete(close)
  }
}

export function closeTagsAliasModals(): void {
  for (const close of [...aliasClosers]) close()
}

export function closeTagsMergeModals(): void {
  for (const close of [...mergeClosers]) close()
}
