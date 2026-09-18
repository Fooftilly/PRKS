/**
 * PDF annotation durable state: pending overlay, effective list, sync handler.
 *
 * Mirrors `backend/pdf_annotation_sync.py`. One annotation = one revisioned
 * aggregate under a Work. PDF bytes are never part of this protocol.
 */
(function (root) {
    'use strict';

    const OPERATIONS = Object.freeze([
        'CREATE_PDF_ANNOTATION', 'SET_PDF_ANNOTATION', 'DELETE_PDF_ANNOTATION',
    ]);

    const ID_KEYS = ['id', 'uuid', 'annotationId', '_id', 'annotation_id', 'ID'];
    const TYPE_KEYS = ['type', 'annotationType', 'subtype', 'subType', 'Subtype'];
    const CONTENT_KEYS = ['contents', 'content', 'comment', 'text', 'body'];
    const PAGE_KEYS = ['pageIndex', 'page', 'pageNumber', 'page_index'];
    const STRIP = new Set(ID_KEYS.concat(TYPE_KEYS, CONTENT_KEYS, PAGE_KEYS, ['color', 'work_id']));
    const FIDELITY_KEYS = ['rect', 'segmentRects', 'strokeColor', 'opacity', 'blendMode', 'custom'];

    function firstText(item, keys) {
        for (let i = 0; i < keys.length; i++) {
            if (!Object.prototype.hasOwnProperty.call(item, keys[i])) continue;
            const value = item[keys[i]];
            if (value == null || typeof value === 'boolean' || typeof value === 'object') continue;
            if (typeof value === 'string' && !value) continue;
            return String(value);
        }
        return '';
    }

    function canonicalId(item) {
        for (let i = 0; i < ID_KEYS.length; i++) {
            if (!Object.prototype.hasOwnProperty.call(item, ID_KEYS[i])) continue;
            const value = item[ID_KEYS[i]];
            if (value == null || typeof value === 'boolean' || typeof value === 'object') continue;
            const text = String(value).trim();
            if (text) return text;
        }
        return '';
    }

    function canonicalType(item) {
        for (let i = 0; i < TYPE_KEYS.length; i++) {
            if (!Object.prototype.hasOwnProperty.call(item, TYPE_KEYS[i])) continue;
            const value = item[TYPE_KEYS[i]];
            if (value == null || typeof value === 'boolean' || typeof value === 'object') continue;
            if (typeof value === 'number' && Number.isFinite(value)) return String(Math.trunc(value));
            const text = String(value).trim();
            if (text) return text;
        }
        return '';
    }

    function canonicalPage(item) {
        for (let i = 0; i < PAGE_KEYS.length; i++) {
            if (!Object.prototype.hasOwnProperty.call(item, PAGE_KEYS[i])) continue;
            const value = item[PAGE_KEYS[i]];
            if (value == null || value === '') return null;
            const n = Number(value);
            if (!Number.isFinite(n) || n < 0 || Math.trunc(n) !== n) return null;
            return n;
        }
        return null;
    }

    function normalizePdfAnnotation(item) {
        if (!item || typeof item !== 'object') return null;
        const id = canonicalId(item);
        if (!id) return null;
        const geometry = {};
        Object.keys(item).forEach(function (key) {
            if (STRIP.has(key)) return;
            geometry[key] = item[key];
        });
        return {
            id: id,
            type: canonicalType(item),
            content: firstText(item, CONTENT_KEYS),
            page_index: canonicalPage(item),
            color: item.color == null ? '' : String(item.color),
            geometry: geometry,
        };
    }

    function semanticView(item) {
        const n = normalizePdfAnnotation(item);
        if (!n) return null;
        const view = {
            id: n.id,
            type: n.type,
            content: n.content,
            page_index: n.page_index,
            color: n.color,
        };
        FIDELITY_KEYS.forEach(function (key) {
            if (Object.prototype.hasOwnProperty.call(n.geometry, key)) {
                view[key] = n.geometry[key];
            }
        });
        return view;
    }

    function annotationsSemanticallyEqual(left, right) {
        return JSON.stringify(semanticView(left)) === JSON.stringify(semanticView(right));
    }

    function roundTripAnnotation(item) {
        const n = normalizePdfAnnotation(item);
        if (!n) return null;
        const out = {
            id: n.id,
            type: /^\d+$/.test(n.type) ? parseInt(n.type, 10) : n.type,
            contents: n.content,
            pageIndex: n.page_index,
            color: n.color,
        };
        Object.keys(n.geometry).forEach(function (key) {
            if (!Object.prototype.hasOwnProperty.call(out, key)) out[key] = n.geometry[key];
        });
        return out;
    }

    function scopeKey(workId, annotationId) {
        return JSON.stringify([workId, annotationId]);
    }

    function pdfAnnotationOperations(rows, workId) {
        return (rows || []).filter(function (op) {
            return op && OPERATIONS.indexOf(op.operation) !== -1 &&
                op.entity_type === 'work' && op.entity_id === workId &&
                op.status !== 'acknowledged';
        });
    }

    let pendingByScope = new Map();
    let pendingGeneration = 0;

    function setPending(rows) {
        const ranked = (rows || []).filter(function (op) {
            return op && OPERATIONS.indexOf(op.operation) !== -1 &&
                op.entity_type === 'work' && op.status !== 'acknowledged';
        }).slice().sort(function (a, b) {
            return (a.sequence || 0) - (b.sequence || 0);
        });
        const next = new Map();
        ranked.forEach(function (op) {
            const annotationId = op.payload && op.payload.annotation_id;
            if (!annotationId) return;
            /* Highest sequence wins when SENT + successor share a scope. */
            next.set(scopeKey(op.entity_id, annotationId), {
                work_id: op.entity_id,
                annotation_id: annotationId,
                operation: op.operation,
                present: op.operation !== 'DELETE_PDF_ANNOTATION',
                annotation: op.operation === 'DELETE_PDF_ANNOTATION'
                    ? null
                    : (op.payload && op.payload.annotation) || null,
                sequence: op.sequence || 0,
                status: op.status,
            });
        });
        pendingByScope = next;
        pendingGeneration += 1;
        return pendingGeneration;
    }

    async function refreshPending() {
        if (!root.prksSync) return [];
        let rows;
        try {
            rows = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        setPending(rows);
        return rows;
    }

    /**
     * Acknowledged list + pending intents = effective annotations.
     * Never mutates the acknowledged array.
     */
    function effectiveWorkAnnotations(list, workId) {
        const base = Array.isArray(list) ? list.slice() : [];
        if (!workId || !pendingByScope.size) return base;
        const byId = new Map();
        base.forEach(function (item) {
            const id = canonicalId(item);
            if (id) byId.set(id, item);
        });
        pendingByScope.forEach(function (entry) {
            if (entry.work_id !== workId) return;
            if (!entry.present) {
                byId.delete(entry.annotation_id);
                return;
            }
            if (entry.annotation) byId.set(entry.annotation_id, entry.annotation);
        });
        return Array.from(byId.values());
    }

    function revisionFromState(state, annotationId) {
        if (!state || typeof state !== 'object') return 0;
        const rows = Array.isArray(state.annotations) ? state.annotations : [];
        for (let i = 0; i < rows.length; i++) {
            if (rows[i] && rows[i].annotation_id === annotationId) {
                const rev = rows[i].revision;
                return Number.isSafeInteger(rev) && rev >= 0 ? rev : 0;
            }
        }
        if (state.known_absent && Number.isSafeInteger(state.known_absent[annotationId])) {
            return state.known_absent[annotationId];
        }
        return 0;
    }

    function acknowledgedBase(list, state, annotationId) {
        const rows = Array.isArray(list) ? list : [];
        let found = null;
        for (let i = 0; i < rows.length; i++) {
            if (canonicalId(rows[i]) === annotationId) {
                found = rows[i];
                break;
            }
        }
        return {
            annotation_id: annotationId,
            present: !!found,
            revision: revisionFromState(state, annotationId),
            annotation: found,
        };
    }

    function pdfAnnotationBaseUnavailable() {
        return {
            code: 'base_unavailable',
            message: 'This annotation has no synchronized base on this device yet.',
        };
    }

    function managedPdfPath(filePath) {
        const raw = String(filePath || '').split('?')[0];
        return raw.indexOf('/api/pdfs/') === 0 ? raw : '';
    }

    async function hasCachedManagedPdf(filePath) {
        const path = managedPdfPath(filePath);
        if (!path) return false;
        const cacheName = root.PRKS_OFFLINE_PDF_CACHE_NAME || 'prks-pdf-v1';
        if (typeof root.caches === 'undefined' || !root.caches || typeof root.caches.open !== 'function') {
            return false;
        }
        try {
            const cache = await root.caches.open(cacheName);
            const match = await cache.match(path);
            return !!match;
        } catch (_e) {
            return false;
        }
    }

    async function durableStoreWritable() {
        if (!root.prksSync || !root.prksSync.store) return false;
        if (typeof root.prksSync.store.isAvailable !== 'function') return false;
        try {
            return !!(await root.prksSync.store.isAvailable());
        } catch (_e) {
            return false;
        }
    }

    function isAnnotationsStateShape(state) {
        return !!(state && typeof state === 'object' && Array.isArray(state.annotations));
    }

    /**
     * Coherent acknowledged snapshot: annotation values + revisions (+ optional
     * materialization gens) from one canonical moment. Never treat a bare list
     * as a safe base.
     */
    function isAnnotationsSnapshotShape(snap) {
        return !!(snap && typeof snap === 'object' &&
            Array.isArray(snap.items) &&
            Array.isArray(snap.annotations) &&
            snap.known_absent && typeof snap.known_absent === 'object');
    }

    function snapshotToState(snap) {
        if (!isAnnotationsSnapshotShape(snap)) return null;
        return {
            work_id: snap.work_id,
            annotations: snap.annotations,
            known_absent: snap.known_absent || {},
        };
    }

    /**
     * Safe offline/online base requires the coherent snapshot (items +
     * revisions together). A list alone must never imply revision 0.
     */
    async function hasAcknowledgedAnnotationBase(workId, runtime) {
        if (!workId) return false;
        if (runtime && runtime.annotationBaseReady === true &&
            runtime.annotationState && isAnnotationsStateShape(runtime.annotationState) &&
            runtime.annotationCache && Array.isArray(runtime.annotationCache.items)) {
            return true;
        }
        if (!root.prksOfflinePeekEntity || typeof root.prksOfflinePeekEntity !== 'function') {
            return false;
        }
        try {
            const snap = await root.prksOfflinePeekEntity('work-annotations-snapshot', workId);
            return isAnnotationsSnapshotShape(snap);
        } catch (_e) {
            return false;
        }
    }

    /**
     * Offline annotation mutation capability (Slice E).
     * Connectivity alone must not decide; need PDF bytes + base + durable store.
     * online_awaiting_base must NOT expose a mutation-capable viewer — hydrate
     * first, install the durable bridge, then enable mutations.
     */
    async function resolvePdfAnnotationMutationCapability(work, runtime) {
        const workId = work && work.id ? String(work.id) : (runtime && runtime.workId) || '';
        const filePath = (work && work.file_path) || (runtime && runtime.filePath) || '';
        const online = typeof root.prksOfflineRuntimeState === 'function'
            ? root.prksOfflineRuntimeState() === 'online'
            : true;

        if (workId && typeof root.prksIsLivePendingWorkDeletion === 'function' &&
            root.prksIsLivePendingWorkDeletion(workId)) {
            return { mode: 'preview', durable: false, reason: 'work_pending_delete' };
        }
        if (workId && typeof root.prksResolveWorkLifecycle === 'function') {
            try {
                const life = await root.prksResolveWorkLifecycle(workId);
                if (life === 'delete') {
                    return { mode: 'preview', durable: false, reason: 'work_pending_delete' };
                }
            } catch (_e) { /* best-effort */ }
        }

        const durableOk = await durableStoreWritable();
        if (online) {
            if (!durableOk) {
                return { mode: 'work', durable: false, reason: 'online_legacy' };
            }
            if (!(await hasAcknowledgedAnnotationBase(workId, runtime))) {
                // Durable store is up, but coherent snapshot is missing — never
                // invent base_revision 0, and never enable mutation yet.
                return { mode: 'preview', durable: false, reason: 'online_awaiting_base' };
            }
            if (runtime && runtime.annotationDurableBridgeReady !== true) {
                return { mode: 'preview', durable: true, reason: 'online_awaiting_bridge' };
            }
            return { mode: 'work', durable: true, reason: 'online_durable' };
        }
        if (!durableOk) {
            return { mode: 'preview', durable: false, reason: 'durable_unavailable' };
        }
        if (!(await hasCachedManagedPdf(filePath))) {
            return { mode: 'preview', durable: false, reason: 'pdf_bytes_unavailable' };
        }
        if (!(await hasAcknowledgedAnnotationBase(workId, runtime))) {
            return { mode: 'preview', durable: false, reason: 'annotation_base_unavailable' };
        }
        if (runtime && runtime.annotationDurableBridgeReady !== true) {
            return { mode: 'preview', durable: true, reason: 'offline_awaiting_bridge' };
        }
        return { mode: 'work', durable: true, reason: 'offline_durable' };
    }

    async function publishAcknowledgedAnnotations(workId, snapshotOrList, maybeState) {
        if (!workId || typeof root.prksOfflineCacheEntity !== 'function') return;
        try {
            let snap = null;
            if (isAnnotationsSnapshotShape(snapshotOrList)) {
                snap = snapshotOrList;
            } else if (Array.isArray(snapshotOrList) && isAnnotationsStateShape(maybeState)) {
                snap = {
                    work_id: workId,
                    items: snapshotOrList,
                    annotations: maybeState.annotations,
                    known_absent: maybeState.known_absent || {},
                    canonical_annotation_set_revision:
                        Number.isSafeInteger(maybeState.canonical_annotation_set_revision)
                            ? maybeState.canonical_annotation_set_revision
                            : undefined,
                    materialized_pdf_annotation_revision:
                        Number.isSafeInteger(maybeState.materialized_pdf_annotation_revision)
                            ? maybeState.materialized_pdf_annotation_revision
                            : undefined,
                };
            }
            if (snap) {
                await root.prksOfflineCacheEntity('work-annotations-snapshot', workId, snap);
            }
        } catch (_e) { /* disposable cache best-effort */ }
    }

    async function loadAcknowledgedAnnotationSnapshot(workId) {
        if (!workId) return null;
        if (typeof root.prksOfflinePeekEntity === 'function') {
            try {
                const snap = await root.prksOfflinePeekEntity('work-annotations-snapshot', workId);
                if (isAnnotationsSnapshotShape(snap)) return snap;
            } catch (_e) { /* fall through */ }
        }
        return null;
    }

    async function loadAcknowledgedAnnotationState(workId) {
        const snap = await loadAcknowledgedAnnotationSnapshot(workId);
        return snapshotToState(snap);
    }

    async function workHasUnresolvedPdfAnnotationOps(workId) {
        if (!workId || !root.prksSync || !root.prksSync.store ||
            typeof root.prksSync.store.listOperations !== 'function') {
            return false;
        }
        try {
            const rows = await root.prksSync.store.listOperations();
            return (rows || []).some(function (op) {
                return op && OPERATIONS.indexOf(op.operation) !== -1 &&
                    op.entity_type === 'work' &&
                    String(op.entity_id) === String(workId) &&
                    op.status !== 'acknowledged';
            });
        } catch (_e) {
            return true;
        }
    }

    async function savePdfAnnotationDurably(workId, desired, observed) {
        if (!root.prksSync || !root.prksSync.store ||
            typeof root.prksSync.store.savePdfAnnotation !== 'function') {
            throw pdfAnnotationBaseUnavailable();
        }
        const row = await root.prksSync.store.savePdfAnnotation(workId, desired, observed);
        await refreshPending();
        // Same wake path as every other durable family: changed() emits + wake().
        // There is no prksSync.kick(); a no-op here strands the row until focus.
        if (root.prksSync && typeof root.prksSync.changed === 'function') {
            try { root.prksSync.changed(); } catch (_e) { /* best-effort */ }
        }
        return row;
    }

    function patchRuntimeAcknowledged(runtime, data) {
        if (!runtime || !data || !data.annotation_id) return false;
        const annotationId = String(data.annotation_id);
        const present = data.present !== false && !!data.annotation;
        const ackList =
            (runtime.annotationCache && Array.isArray(runtime.annotationCache.items))
                ? runtime.annotationCache.items
                : [];
        let nextList = ackList.filter(function (item) {
            if (!item || typeof item !== 'object') return true;
            return canonicalId(item) !== annotationId;
        });
        if (present) nextList = nextList.concat([data.annotation]);
        runtime.annotationCache = {
            allItems: nextList,
            rawItems: nextList,
            items: nextList,
            docId: runtime.annotationCache && runtime.annotationCache.docId,
            workId: String(runtime.workId || data.work_id || ''),
        };

        const prevState = runtime.annotationState && typeof runtime.annotationState === 'object'
            ? runtime.annotationState
            : { work_id: data.work_id, annotations: [], known_absent: {} };
        const annotations = Array.isArray(prevState.annotations)
            ? prevState.annotations.filter(function (row) {
                return !(row && String(row.annotation_id) === annotationId);
            })
            : [];
        const knownAbsent = Object.assign({}, prevState.known_absent || {});
        if (present) {
            annotations.push({
                annotation_id: annotationId,
                revision: data.server_revision,
            });
            delete knownAbsent[annotationId];
        } else if (Number.isSafeInteger(data.server_revision)) {
            knownAbsent[annotationId] = data.server_revision;
        }
        runtime.annotationState = Object.assign({}, prevState, {
            annotations: annotations,
            known_absent: knownAbsent,
        });
        // Incremental ACK patches one annotation. Only advance the claimed
        // coherent set revision when generation continuity proves there were
        // no unseen intermediate set changes (prev + 1 === ack gen) AND the
        // ACK actually changed something (changed:false convergent ACK cannot
        // prove it owns that global generation increment). A gap means another
        // device may have changed a different annotation — keep the prior set
        // label until a full /annotations-snapshot lands.
        if (Number.isSafeInteger(data.canonical_annotation_set_revision)) {
            const nextGen = data.canonical_annotation_set_revision;
            const prevGen = Number.isSafeInteger(runtime.acknowledgedAnnotationSetRevision)
                ? runtime.acknowledgedAnnotationSetRevision
                : null;
            if (prevGen === null || nextGen === prevGen) {
                runtime.acknowledgedAnnotationSetRevision = nextGen;
            } else if (data.changed === true && nextGen === prevGen + 1) {
                runtime.acknowledgedAnnotationSetRevision = nextGen;
            }
        }
        return true;
    }

    /**
     * After ACK: update every mounted Work PDF runtime for this Work so the
     * next mutation uses the new base_revision without reopening the PDF.
     */
    function applyAckToLiveRuntimes(data) {
        if (!data || !data.work_id || !Number.isSafeInteger(data.server_revision)) {
            return [];
        }
        const touched = [];
        function visit(ctx) {
            const runtime = ctx && typeof ctx.getResource === 'function'
                ? ctx.getResource('pdf')
                : null;
            if (!runtime || runtime._destroyed) return;
            if (String(runtime.workId) !== String(data.work_id)) return;
            if (!patchRuntimeAcknowledged(runtime, data)) return;
            touched.push(runtime);
            if (typeof root.prksReconcileViewerAnnotations === 'function' && runtime.viewer) {
                const effective = effectiveWorkAnnotations(
                    (runtime.annotationCache && runtime.annotationCache.items) || [],
                    String(data.work_id)
                );
                void root.prksReconcileViewerAnnotations(runtime.viewer, effective, {
                    isManaged: typeof root.prksIsUserMarkupAnnotation === 'function'
                        ? root.prksIsUserMarkupAnnotation
                        : undefined,
                });
            }
        }
        if (typeof root.prksForEachLiveTabContext === 'function') {
            root.prksForEachLiveTabContext(visit);
        }
        return touched;
    }

    function isResult(data, op) {
        if (!data || data.work_id !== op.entity_id ||
            data.annotation_id !== op.payload.annotation_id) return false;
        switch (data.code) {
            case 'ACKNOWLEDGED':
                if (typeof data.changed !== 'boolean') return false;
                if (!Number.isSafeInteger(data.server_revision) || data.server_revision < 0) {
                    return false;
                }
                if (op.operation === 'DELETE_PDF_ANNOTATION') {
                    return data.present === false;
                }
                return !!(data.annotation && typeof data.annotation === 'object' &&
                    canonicalId(data.annotation) === op.payload.annotation_id);
            case 'REVISION_CONFLICT':
            case 'FUTURE_REVISION':
                return Number.isSafeInteger(data.current_revision);
            case 'ANNOTATION_EXISTS':
            case 'ANNOTATION_ID_REUSED':
            case 'ANNOTATION_ID_CONFLICT':
            case 'ENTITY_NOT_FOUND':
                return true;
            default:
                return false;
        }
    }

    /**
     * While stabilizing: Work gone / id reuse are consumed like Work-deletion
     * outcomes — discard, do not open a user-resolvable annotation conflict.
     * True revision conflicts remain user-resolvable.
     */
    function terminal(data) {
        if (!data || !data.code) return { discard: 'UNKNOWN' };
        if (data.code === 'ENTITY_NOT_FOUND' ||
            data.code === 'ANNOTATION_ID_REUSED' ||
            data.code === 'ANNOTATION_ID_CONFLICT') {
            return { discard: data.code };
        }
        const out = { code: data.code };
        if (Number.isSafeInteger(data.current_revision)) {
            out.current_revision = data.current_revision;
        }
        if (data.current_annotation) out.current_annotation = data.current_annotation;
        if (data.requested_annotation) out.requested_annotation = data.requested_annotation;
        return { conflict: out };
    }

    const handler = {
        isResult: isResult,
        terminal: terminal,
        reconcile: function (data, op) {
            function afterAck() {
                applyAckToLiveRuntimes(data);
                // Re-envelope never-sent successors against the actual ACK
                // server_revision before they can be claimed/SENT. Provisional
                // base+1 is not safe for stale-identical convergent ACKs.
                if (op && op.op_id && Number.isSafeInteger(data.server_revision) &&
                    root.prksSync && root.prksSync.store &&
                    typeof root.prksSync.store.rebasePdfAnnotationDependents === 'function') {
                    return root.prksSync.store.rebasePdfAnnotationDependents(
                        op.op_id, data.server_revision
                    ).then(function () { return true; });
                }
                return Promise.resolve(true);
            }
            if (typeof root.prksOfflineReconcilePdfAnnotation === 'function') {
                return root.prksOfflineReconcilePdfAnnotation(data).then(function (ok) {
                    if (!ok) return false;
                    return afterAck();
                });
            }
            return afterAck();
        },
    };

    Object.assign(root, {
        PRKS_PDF_ANNOTATION_OPERATION_TYPES: OPERATIONS,
        prksPdfAnnotationScopeKey: scopeKey,
        prksPdfAnnotationOperations: pdfAnnotationOperations,
        prksSetPendingPdfAnnotations: setPending,
        prksRefreshPendingPdfAnnotations: refreshPending,
        prksPendingPdfAnnotationGeneration: function () { return pendingGeneration; },
        prksNormalizePdfAnnotation: normalizePdfAnnotation,
        prksPdfAnnotationCanonicalId: canonicalId,
        prksPdfAnnotationSemanticView: semanticView,
        prksPdfAnnotationsSemanticallyEqual: annotationsSemanticallyEqual,
        prksRoundTripPdfAnnotation: roundTripAnnotation,
        prksEffectiveWorkAnnotations: effectiveWorkAnnotations,
        prksAcknowledgedPdfAnnotationBase: acknowledgedBase,
        prksPdfAnnotationBaseUnavailable: pdfAnnotationBaseUnavailable,
        prksSavePdfAnnotationDurably: savePdfAnnotationDurably,
        prksHasCachedManagedPdf: hasCachedManagedPdf,
        prksHasAcknowledgedAnnotationBase: hasAcknowledgedAnnotationBase,
        prksResolvePdfAnnotationMutationCapability: resolvePdfAnnotationMutationCapability,
        prksPublishAcknowledgedPdfAnnotations: publishAcknowledgedAnnotations,
        prksLoadAcknowledgedPdfAnnotationState: loadAcknowledgedAnnotationState,
        prksLoadAcknowledgedPdfAnnotationSnapshot: loadAcknowledgedAnnotationSnapshot,
        prksApplyPdfAnnotationAckToLiveRuntimes: applyAckToLiveRuntimes,
        prksIsPdfAnnotationsStateShape: isAnnotationsStateShape,
        prksIsPdfAnnotationsSnapshotShape: isAnnotationsSnapshotShape,
        prksPdfAnnotationSnapshotToState: snapshotToState,
        prksWorkHasUnresolvedPdfAnnotationOps: workHasUnresolvedPdfAnnotationOps,
        prksPdfAnnotationSyncHandler: handler,
    });
}(typeof window !== 'undefined' ? window : global));
