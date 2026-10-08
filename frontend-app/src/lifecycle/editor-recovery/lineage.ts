/**
 * Per-lineage liveness classification.
 *
 * A live runtime does not mean a live editor for a lineage: after a reload the
 * same runtime has a new page, and shared workspace persistence can remap or
 * drop the old pane id. So liveness is classified per `draftId`, first match:
 *
 * - `self-live`: a writer in this page owns it for the asking session.
 * - `other-live`: a writer in this page owns it for another session, or
 *   another page answers that it has a live writer for it.
 * - `same-runtime-orphan`: this page wrote it with no live writer now, or its
 *   owner runtime is this page's verified runtime (a previous load of this
 *   tab) and, for a channel claim, that earlier load recorded its close in
 *   this tab's sessionStorage and no page answers for a live writer.
 *   Adoptable. Never hidden just because its runtime is alive.
 * - `dead-runtime`: the owner page and runtime both do not answer, and the
 *   page is proven gone: its page lock is no longer held, or it recorded its
 *   final `pagehide` in localStorage. A missed BroadcastChannel answer alone
 *   is never proof: a frozen, busy or crashed-and-unrecorded page misses it
 *   too. Adoptable.
 * - `unknown`: anything that cannot be established. Offered for review only.
 *
 * The pane id is never used: it is a hint, not an identity.
 */

import type { PageIdentity } from './identity'
import type { DraftRecord } from './schema'

export type LineageClass = 'self-live' | 'other-live' | 'same-runtime-orphan' | 'dead-runtime' | 'unknown'

export interface LineageProbe {
  identity: Pick<PageIdentity, 'pageInstanceId' | 'current' | 'isPageAlive' | 'isRuntimeAlive' | 'isLineageLiveElsewhere' | 'wasClosedInThisTab'> &
    Partial<Pick<PageIdentity, 'isPageGone' | 'wasPageClosed'>>
  /** Session key of the writer in this page that owns `draftId`, or null. */
  localOwner(draftId: string): string | null
}

export function isAdoptable(lineageClass: LineageClass): boolean {
  return lineageClass === 'same-runtime-orphan' || lineageClass === 'dead-runtime'
}

export async function classifyLineage(
  record: Pick<DraftRecord, 'draftId' | 'owner'>,
  probe: LineageProbe,
  askingSession: string | null = null,
): Promise<LineageClass> {
  const local = probe.localOwner(record.draftId)
  if (local !== null) return local === askingSession ? 'self-live' : 'other-live'

  const { identity } = probe
  const owner = record.owner
  if (owner.pageInstanceId === identity.pageInstanceId) return 'same-runtime-orphan'

  const claim = identity.current()
  if (claim && owner.runtimeId && owner.runtimeId === claim.runtimeId) {
    if (claim.verified === 'lock') return 'same-runtime-orphan'
    // An unverified id may be shared with a duplicated tab, and a channel
    // claim is only the absence of an answer, as is a missed `lineage?`. A
    // channel-claimed lineage is adoptable only with positive evidence that
    // its page was an earlier load of this tab that closed.
    if ((await identity.isLineageLiveElsewhere(record.draftId)) === true) return 'other-live'
    return claim.verified === 'channel' && identity.wasClosedInThisTab(owner.pageInstanceId) ? 'same-runtime-orphan' : 'unknown'
  }

  if ((await identity.isLineageLiveElsewhere(record.draftId)) === true) return 'other-live'
  const pageAlive = await identity.isPageAlive(owner.pageInstanceId)
  const runtimeAlive = owner.runtimeId ? await identity.isRuntimeAlive(owner.runtimeId) : false
  if (pageAlive !== false || runtimeAlive !== false) return 'unknown'
  const gone =
    (identity.wasPageClosed ? identity.wasPageClosed(owner.pageInstanceId) : false) ||
    (identity.isPageGone ? await identity.isPageGone(owner.pageInstanceId) : false)
  return gone ? 'dead-runtime' : 'unknown'
}
