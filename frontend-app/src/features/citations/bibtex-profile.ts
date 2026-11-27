/**
 * BibTeX export-profile writes not yet answered. PRKS builds BibTeX from the
 * saved profile, so a BibTeX export waits for them.
 */
let flushDebounced: (() => void) | null = null
const outstanding = new Set<Promise<boolean>>()
/* A rejected toggle stays shown until Settings rolls it back, after its later writes. */
let unreconciled = false

/** `run` resolves true when PRKS saved the write. */
export function trackBibtexProfileWrite(run: Promise<boolean>): void {
  outstanding.add(run)
  void run.finally(() => outstanding.delete(run))
}

/** Set when a write fails, cleared when Settings shows what PRKS saved again. */
export function setBibtexProfileUnreconciled(value: boolean): void {
  unreconciled = value
}

/** Sends a toggle still waiting for its debounce. */
export function setBibtexProfileFlush(flush: (() => void) | null): void {
  flushDebounced = flush
}

/** False when a pending write failed: an export now would use other toggles than Settings shows. */
export async function settleBibtexProfile(): Promise<boolean> {
  flushDebounced?.()
  const failedBefore = unreconciled
  const results = await Promise.all([...outstanding])
  return !failedBefore && results.every(Boolean)
}
