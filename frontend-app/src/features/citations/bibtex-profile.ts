/**
 * BibTeX export-profile writes not yet answered. PRKS builds BibTeX from the
 * saved profile, so a BibTeX export waits for them.
 */
let flushDebounced: (() => void) | null = null
const outstanding = new Set<Promise<boolean>>()

/** `run` resolves true when PRKS saved the write. */
export function trackBibtexProfileWrite(run: Promise<boolean>): void {
  outstanding.add(run)
  void run.finally(() => outstanding.delete(run))
}

/** Sends a toggle still waiting for its debounce. */
export function setBibtexProfileFlush(flush: (() => void) | null): void {
  flushDebounced = flush
}

/** False when a pending write failed: an export now would use other toggles than Settings shows. */
export async function settleBibtexProfile(): Promise<boolean> {
  flushDebounced?.()
  const results = await Promise.all([...outstanding])
  return results.every(Boolean)
}
