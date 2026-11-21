/**
 * One setting saved in order: at most one request in flight, and the last
 * value chosen is the last one sent (Citation V1, #41).
 *
 * Choosing a value while a save is running only records it; when the
 * running save settles, the newest value is sent next. So an older request
 * can never finish after a newer one and leave the server on the older
 * choice. A failure reverts to the last saved value only when nothing newer
 * was chosen meanwhile; otherwise the newer choice is still sent.
 */
export interface SerialSave {
  /** Record `value` and save it, after any save already running. Resolves when the queue is idle. */
  choose(value: string): Promise<void>
  /** The last value the server accepted (or the starting value). */
  saved(): string
  /** Set the accepted value without a request, e.g. after the first settings read. */
  reset(value: string): void
}

export interface SerialSaveHooks {
  save(value: string): Promise<unknown>
  /** The latest chosen value failed to save; `saved` is what to show again. */
  onFailed(saved: string): void
}

export function createSerialSave(initial: string, hooks: SerialSaveHooks): SerialSave {
  let saved = initial
  let wanted = initial
  let running: Promise<void> | null = null

  async function drain(): Promise<void> {
    while (wanted !== saved) {
      const value = wanted
      try {
        await hooks.save(value)
        saved = value
      } catch {
        if (wanted === value) {
          wanted = saved
          hooks.onFailed(saved)
          return
        }
      }
    }
  }

  return {
    choose(value: string): Promise<void> {
      wanted = value
      if (!running) {
        running = drain().finally(() => {
          running = null
        })
      }
      return running
    },
    saved: () => saved,
    reset(value: string): void {
      if (!running) {
        saved = value
        wanted = value
      }
    },
  }
}
