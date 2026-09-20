# Offline and Sync

PRKS is progressively becoming local-first. The core rule is that durable user intent and offline read caching are separate systems.

## Three different mechanisms

### Disposable read cache/projection

Selected routes can render useful data after the PRKS server becomes temporarily unreachable. These snapshots/projections are replaceable and are not the source of truth.

### Durable local store and operation log

For supported mutation families, PRKS records the change in durable browser storage before reporting it as saved. The UI can project that intent immediately, the operation survives reload/restart, and synchronization sends/reconciles it when the server responds again.

### Request coordinator cache

Normal online \`/api\` requests pass through a short-lived in-memory request coordinator. This reduces duplicate/nearby request work but is not offline storage and is not durable.

Keeping these mechanisms conceptually separate prevents a common architecture error: treating a cache hit as proof that a mutation is durable.

## Reachability

PRKS judges server reachability from actual request/probe results rather than trusting \`navigator.onLine\`. Any real HTTP response means the server was reachable; transport failure means it was not.

## Operation families

Durability is implemented by domain-specific operation families rather than one generic "offline mode." Examples include Work metadata/source/lifecycle, Work↔Person relationships, notes, tags, folders, playlists, people/groups, and structured research entities.

The operation set changes as local-first rollout advances. Therefore this wiki intentionally does not freeze a complete list.

The definitive current score is:

[docs/local-first-rollout-status.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-rollout-status.md)

The detailed protocol/design record is:

[docs/local-first-sync.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-sync.md)

## Reconciliation principles

A durable operation represents user intent at a domain boundary, not an arbitrary SQL column update. Some related fields must change as one aggregate to preserve invariants—for example Work source/video identity.

Conflicts and revisions therefore belong to meaningful scopes, not necessarily individual database columns.

## Offline UI

A page can be available offline without every action on that page being durable. Conversely, a supported durable change should not be blocked merely because the browser's network hint says "offline."

Contributors should use the rollout-status document and domain state modules rather than adding connectivity guards from intuition.

## Multi-user scope

Current local-first work is about one user's browser/server continuity, not a general multi-user collaborative synchronization protocol. Do not infer multi-user semantics from the existence of revisioned operations.

## Debugging

When investigating offline problems, determine which layer failed:

1. read projection/cache;
2. local durable operation creation;
3. UI projection of pending intent;
4. sync transport;
5. canonical server apply;
6. acknowledgement/revision observation;
7. conflict/reconciliation;
8. derived-cache invalidation.

The sync diagnostics modules and relevant unit/E2E tests are usually more informative than simply checking the network icon.
