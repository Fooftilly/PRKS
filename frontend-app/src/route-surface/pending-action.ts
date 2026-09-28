import { ref } from 'vue'

/**
 * One in-flight route action. The clicked control is busy; siblings disable.
 * `resetPending` drops a previous record's key so it cannot block the next one.
 * Idle state is restored in `finally`, including when the request fails.
 * The epoch keeps that reset from clearing a newer action that reused the key.
 */
export function usePendingAction() {
  const pending = ref<string | null>(null)
  let epoch = 0

  function actionBusy(key: string): boolean {
    return pending.value === key
  }

  function actionBlocked(key: string): boolean {
    return pending.value != null && pending.value !== key
  }

  function resetPending(): void {
    epoch += 1
    pending.value = null
  }

  async function withBusy(key: string, action: () => Promise<void>): Promise<void> {
    if (pending.value) return
    const ticket = epoch
    pending.value = key
    try {
      await action()
    } finally {
      if (epoch === ticket && pending.value === key) pending.value = null
    }
  }

  return { actionBusy, actionBlocked, resetPending, withBusy }
}
