import { usePendingAction } from '../../route-surface/pending-action'

/** One in-flight People action. The clicked control is busy; siblings disable. */
export function usePeoplePendingAction() {
  return usePendingAction()
}
