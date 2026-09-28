import { ref } from 'vue'

/** One in-flight Playlist action. The clicked control is busy; siblings disable. */
export function usePlaylistPendingAction() {
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
