import { usePendingAction } from '../../route-surface/pending-action'

/**
 * One in-flight Argument action. The clicked control is busy; siblings disable.
 * Idle state is restored in finally, including when the request fails.
 */
export function useArgumentPendingAction() {
  const { actionBusy, actionBlocked, withBusy } = usePendingAction()
  return { actionBusy, actionBlocked, withBusy }
}
