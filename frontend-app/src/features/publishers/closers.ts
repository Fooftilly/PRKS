/**
 * Escape and overlay dismissal call `prksClosePublishersAliasModal`.
 * That name closes whichever owner's dialog is open. It does not keep a
 * window singleton of the selected publisher.
 */
const aliasClosers = new Set<() => void>()

export function registerPublishersAliasCloser(close: () => void): () => void {
  aliasClosers.add(close)
  return () => {
    aliasClosers.delete(close)
  }
}

export function closePublishersAliasModals(): void {
  for (const close of [...aliasClosers]) close()
}
