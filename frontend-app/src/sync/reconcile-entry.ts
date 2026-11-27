/**
 * Classic-script entry. The maintainer build emits `frontend/js/sync-reconcile.js`
 * as the global `prksSyncReconcile`, which every durable family's ACK merge uses.
 * No Vue mount, Pinia, or Vue Router.
 */
export {
  acknowledgeEditBase,
  adoptFresh,
  applyOwnerAck,
  carryObservation,
  compareRevisions,
  decideAck,
  entityKey,
  isRevision,
  newerEditBase,
  observe,
  observedAt,
  overlay,
  pendingFor,
  pinEditBase,
  pinnedEdits,
  planAck,
  readBefore,
  timeOf,
} from './reconcile'
