import { ref } from 'vue'

/**
 * One in-flight Argument action. The clicked control is busy; siblings disable.
 * Idle state is restored in finally, including when the request fails.
 */
export function useArgumentPendingAction() {
  const pending = ref<string | null>(null)

  function actionBusy(key: string): boolean {
    return pending.value === key
  }

  function actionBlocked(key: string): boolean {
    return pending.value != null && pending.value !== key
  }

  async function withBusy(key: string, action: () => Promise<void>): Promise<void> {
    if (pending.value) return
    pending.value = key
    try {
      await action()
    } finally {
      if (pending.value === key) pending.value = null
    }
  }

  return { actionBusy, actionBlocked, withBusy }
}
