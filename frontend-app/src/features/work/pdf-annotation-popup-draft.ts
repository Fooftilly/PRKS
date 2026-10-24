export interface PopupDraftIdentity {
  annId: string
  epoch: number
}

export interface PopupDraftSession {
  open: boolean
  annId: string
  epoch: number
  comment: string
}

/**
 * A new annotation session takes that annotation's comment.
 * The same open session keeps the in-progress draft.
 */
export function popupDraftForSession(
  previous: PopupDraftIdentity | null,
  next: PopupDraftSession,
  draft: string,
): string {
  if (!next.open) return ''
  if (previous && previous.annId === next.annId && previous.epoch === next.epoch) return draft
  return next.comment
}
