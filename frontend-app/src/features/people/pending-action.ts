import { ref } from 'vue'

/** One in-flight People action. The clicked control is busy; siblings disable. */
export function usePeoplePendingAction() {
  const pending = ref<string | null>(null)
  let epoch = 0

  function actionBusy(key: string): boolean {
    return pending.value === key
  }

  function actionBlocked(key: string): boolean {
    return pending.value != null && pending.value !== key
  }

  /** Drop a previous person's in-flight key so it cannot block the next one. */
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
