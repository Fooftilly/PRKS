/**
 * Classic-script entry. The maintainer build emits `frontend/js/sync-reconcile.js`
 * as the global `prksSyncReconcile`, which every durable family's ACK merge uses.
 * No Vue mount, Pinia, or Vue Router.
 */
export {
  adoptFresh,
  applyOwnerAck,
  carryObservation,
  compareRevisions,
  decideAck,
  entityKey,
  isRevision,
  observe,
  observedAt,
  overlay,
  pendingFor,
  planAck,
  readBefore,
  timeOf,
} from './reconcile'
