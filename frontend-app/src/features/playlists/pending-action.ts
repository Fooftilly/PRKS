import { usePendingAction } from '../../route-surface/pending-action'

/** One in-flight Playlist action. The clicked control is busy; siblings disable. */
export function usePlaylistPendingAction() {
  return usePendingAction()
}
