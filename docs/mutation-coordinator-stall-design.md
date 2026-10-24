# Stalled mutation coordinator: uncertain-outcome design gate (#822)

**Status: proposal; NOT approved for implementation.** This document records the open correctness decision after the read-lane fix in [#824](https://github.com/Fooftilly/PRKS/pull/824). It does not change request, server, or user-visible behavior. Before changing the mutation lane, the maintainer must approve the unresolved decisions below.

## 1. Scope and verified current behavior

- `frontend/js/request-coordinator.js` routes first-party ordinary POST/PATCH/PUT/DELETE and the classified special endpoints through one FIFO mutation lane (`activeMutation <= 1`). `runMutationJob()` awaits a fetch without a deadline and clears `activeMutation` only after the request settles. There is no automatic mutation retry.
- The mutation path passes no effective route-abort signal to the underlying transport: canonical writes are intended to survive navigation. Later writes wait behind the in-flight one, with no bounded completion if its header promise never resolves.
- A resolved HTTP response, including 4xx/5xx, establishes transport reachability; response/domain errors do not imply the network is offline. The coordinator clears short-lived read cache before a write and after some outcomes.
- `docs/local-first-sync.md` defines a **different** durable semantic-operation endpoint (`POST /api/sync/operations`) with op IDs, a server ledger, exact replay and explicit acknowledgment. Those guarantees must not be assumed for unrelated ordinary POST/PATCH endpoints.
- PR #824 bounds **ordinary metadata reads only**, not mutations, managed PDF transfers or response-body consumption. A completed read after a timed-out write does not by itself prove whether that write committed.

Evidence baseline: `master@3d34112fdb8d8c298df5046470223bf064151bd7` (`frontend/js/request-coordinator.js`, `frontend/AGENTS.md` §Client request coordinator, and `docs/local-first-sync.md` §Protocol). Audit reproduction and original acceptance criteria: issue #822 (EF-132).

## 2. Invariants

1. **No automatic replay of an ambiguous mutation.** Aborting a browser request does not prove the server rolled back an already accepted write; a POST that timed out can still commit.
2. **FIFO stays meaningful.** Do not execute later dependent mutations while an earlier write can still commit out of order. A generic read-only health check is not an acknowledgment or server-side execution fence.
3. **Every accepted user write has an explicit outcome or an actionable uncertainty state.** Never silently drop queued writes, clear drafts as 'saved', or equate an absent HTTP answer with a definite failure.
4. **Keep the existing durability owners.** The coordinator must not invent a second IndexedDB outbox or reuse the semantic-op ledger as an undocumented generic mutation protocol.
5. **Avoid private telemetry.** Expose only aggregate lane occupancy and duration buckets. Do not log request URLs, entity IDs, bodies, names, notes, tokens or search terms.
6. **Support exceptional long-running operations.** Backups, uploads and operations with an acknowledged asynchronous job may require endpoint-specific timeouts/confirmation; do not blanket-apply the 60-second read bound.

## 3. Failure states that require different handling

| Observation | What is actually known | Safe client behavior |
| --- | --- | --- |
| HTTP response received, any status | Server handled the HTTP exchange; application result is known from that response | Complete ordinary FIFO settlement; do not treat 4xx/5xx as transport failure |
| Caller explicitly canceled before dispatch | Operation never left the local queue | Cancel safely if an explicit supported cancel path exists; preserve user intent ownership |
| Transport error/abort **after dispatch** | Server may have received and committed the request | Mark outcome unknown; no blind replay and no claim of rollback |
| Deadline expires with no response headers | Server outcome and continued execution are unknown | Distinguish from a definite failure; freeze/hold the dependent write sequence until reconciled |
| Definitive server-side idempotency acknowledgment | Exact request ID and payload hash have a recorded terminal outcome | Use that response to reconcile and then resume safely |
| No record found in a status lookup | Not necessarily proof that an in-flight request will not later commit | Do not release ordering barrier unless server protocol supplies an authoritative finality fence |

## 4. Options and preferred direction

**Option A — timeout and immediately pump the next mutation.** Reject. This is a liveness improvement only: the timed-out mutation can still commit later, changing ordering and making a retried POST duplicate its effects.

**Option B — timeout and indefinitely retain the existing slot, but reject the caller.** Not sufficient alone. It eventually informs one caller but leaves the entire queue blocked and risks discarding the user's unsaved intent after an unrecognized error. A manual recovery procedure may be acceptable only with explicit UX and durable ownership.

**Option C — server-confirmed mutation identity plus explicit uncertainty barrier (preferred for evaluation).** Add an opt-in, **server-enforced** request identity and outcome/status contract per supported mutation family. A request identity must be scoped to the proper authenticated library/user, reject same-key/different-payload reuse, and be written atomically with the canonical mutation. A concurrent duplicate must not begin a second execution. The server must allow authoritative recovery of committed/terminal/pending outcomes and define what an absent record means under request races. If a request times out, the client marks its outcome unknown and pauses subsequent dependent writes until a server-authoritative result or a reviewed manual resolution. Only reissue a write if its family has the exact replay contract; never generic retry.

**Option D — no automatic mutation deadline for now, add diagnostics and a manual troubleshooting path.** Safer interim choice than Option A if a server confirmation protocol is not yet available, but does not fully solve the queue stall. Its limitations must be visible and tracked.

**Preferred phased approach:** maintain Option D until Option C's server contract and operation-specific UX are approved. Begin with one narrow family (such as ordinary online Work-create) that has an auditable canonical result; do not apply a global mutation deadline to every endpoint by default. Existing durable semantic ops retain their own ledger and transport owner.

## 5. Proposed implementation phases (not authorized)

1. **Inventory mutation families and ownership.** Enumerate classic `prksRequest()` mutation call sites, special URL classifications (such as image uploads), their backend handlers, transaction boundaries and idempotency/reconciliation capabilities. Separate one-shot operational jobs from canonical data edits and existing durable semantic operations.
2. **Approve server identity/finality contract for one family.** Specify request key/header naming, key scope/retention, canonical payload hashing, duplicate execution serialization, exact result replay, status API, transaction boundaries, and behavior across application restarts/restore. Design PostgreSQL-compatible semantics; do not depend on a per-process in-memory lock.
3. **Add a client `uncertain` state for only that covered family.** Bound time-to-headers without bounding response-body streaming; distinguish deadline from ordinary abort; invalidate/cancel best-effort transport while fencing late results; block dependent FIFO mutations until reconciliation. Queued user intent must remain observable and owned, not silently discarded.
4. **Implement explicit reconciliation.** On a verifiable terminal result, settle the original waiter and resume in order. On unresolved/indeterminate status, do not resume or auto-replay; present a manual resolution path which explains the server may already have applied the operation. Decide how this survives navigation/reload before enabling it.
5. **Expand family-by-family.** Only after proving no duplicates, no out-of-order commits, and preservation of the existing offline/durable operation semantics. Keep unsupported mutation families on the documented legacy policy.

## 6. Required tests and observability

- Inject a POST that never settles and a queued second POST: confirm current FIFO cannot inadvertently dispatch the second; with the future protocol, assert timeout yields **unknown**, not definite failure, and stops further execution until authoritative resolution.
- Simulate a server that commits and then loses the response. A repeated request with the same identity must replay precisely one result without executing twice; a reused ID with changed body must fail closed.
- Simulate timeout racing with a late successful response, late rejection, a second browser tab, client reconnect, server restart, restore/recovery, and an ordinary GET that succeeds before the write's outcome is known.
- Verify neither caller abort nor a stale watchdog falsely reports a successful save or double-settles a waiter, and no private request data appears in logs/diagnostics.
- Verify explicit operation classes (PDF upload, backup/restore, reindex, durable sync) follow their own reviewed semantics. Keep read-lane #824 regression coverage intact.

## 7. Maintainer decisions before implementation

1. Approve or reject Option C (server-confirmed idempotency/outcome + client uncertainty barrier) as the target; otherwise retain Option D and do not add a generic mutation timeout.
2. Name the first supported ordinary mutation family and decide whether broader endpoint coverage is in scope for PRKS 1.0.
3. Decide the user-visible recovery flow for a possibly committed but unconfirmed write, including what happens to queued edits and unsaved drafts on navigation/reload.
4. Agree on the operation-specific deadline policy and server outcome retention period. These should be informed by measurements; no fixed universal write timeout is approved by this proposal.

## 8. Relationship to current work

- [#818](https://github.com/Fooftilly/PRKS/issues/818) / [#821](https://github.com/Fooftilly/PRKS/pull/821): stale reachability-observation ordering — completed.
- [#819](https://github.com/Fooftilly/PRKS/issues/819) / [#823](https://github.com/Fooftilly/PRKS/pull/823): dedicated probe watchdog — completed.
- [#822](https://github.com/Fooftilly/PRKS/issues/822) / [#824](https://github.com/Fooftilly/PRKS/pull/824): read header-wait starvation — read slice completed; **mutation issue remains open**.
- [#559](https://github.com/Fooftilly/PRKS/issues/559): durable sync response-body deadline — different owner and scope.
- `docs/local-first-sync.md`: authoritative durability/replay contract for registered semantic operations; do not broaden it implicitly.
