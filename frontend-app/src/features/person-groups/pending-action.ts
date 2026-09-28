import { usePendingAction } from '../../route-surface/pending-action'

/** One in-flight Person Group action. The clicked control is busy; siblings disable. */
export function usePersonGroupPendingAction() {
  return usePendingAction()
}
