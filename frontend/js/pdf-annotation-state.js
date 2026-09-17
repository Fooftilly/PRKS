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
        const next = new Map();
        (rows || []).filter(function (op) {
            return op && OPERATIONS.indexOf(op.operation) !== -1 &&
                op.entity_type === 'work' && op.status !== 'acknowledged';
        }).forEach(function (op) {
            const annotationId = op.payload && op.payload.annotation_id;
            if (!annotationId) return;
            next.set(scopeKey(op.entity_id, annotationId), {
                work_id: op.entity_id,
                annotation_id: annotationId,
                operation: op.operation,
                present: op.operation !== 'DELETE_PDF_ANNOTATION',
                annotation: op.operation === 'DELETE_PDF_ANNOTATION'
                    ? null
                    : (op.payload && op.payload.annotation) || null,
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

    async function hasAcknowledgedAnnotationBase(workId, runtime) {
        if (!workId) return false;
        // Only a completed hydrate marks the in-memory cache as a real base.
        // An empty pre-hydrate annotationCache must not count — that would let
        // offline edits start before GET /annotations (or its cache peek) finished.
        if (runtime && runtime.annotationBaseReady === true) return true;
        if (!root.prksOfflinePeekEntity || typeof root.prksOfflinePeekEntity !== 'function') {
            return false;
        }
        try {
            const snap = await root.prksOfflinePeekEntity('work-annotations', workId);
            return Array.isArray(snap);
        } catch (_e) {
            return false;
        }
    }

    /**
     * Offline annotation mutation capability (Slice E).
     * Connectivity alone must not decide; need PDF bytes + base + durable store.
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
            return {
                mode: 'work',
                durable: durableOk,
                reason: durableOk ? 'online_durable' : 'online_legacy',
            };
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
        return { mode: 'work', durable: true, reason: 'offline_durable' };
    }

    async function publishAcknowledgedAnnotations(workId, list, state) {
        if (!workId || typeof root.prksOfflineCacheEntity !== 'function') return;
        try {
            if (Array.isArray(list)) {
                await root.prksOfflineCacheEntity('work-annotations', workId, list);
            }
            if (state && typeof state === 'object') {
                await root.prksOfflineCacheEntity('work-annotations-state', workId, state);
            }
        } catch (_e) { /* disposable cache best-effort */ }
    }

    async function loadAcknowledgedAnnotationState(workId) {
        if (!workId) return null;
        if (typeof root.prksOfflinePeekEntity === 'function') {
            try {
                const snap = await root.prksOfflinePeekEntity('work-annotations-state', workId);
                if (snap && typeof snap === 'object') return snap;
            } catch (_e) { /* fall through */ }
        }
        return null;
    }

    async function savePdfAnnotationDurably(workId, desired, observed) {
        if (!root.prksSync || !root.prksSync.store ||
            typeof root.prksSync.store.savePdfAnnotation !== 'function') {
            throw pdfAnnotationBaseUnavailable();
        }
        const row = await root.prksSync.store.savePdfAnnotation(workId, desired, observed);
        await refreshPending();
        if (root.prksSync && typeof root.prksSync.kick === 'function') {
            try { root.prksSync.kick(); } catch (_e) { /* best-effort */ }
        }
        return row;
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

    function terminal(data) {
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
        reconcile: function (data) {
            if (typeof root.prksOfflineReconcilePdfAnnotation === 'function') {
                return root.prksOfflineReconcilePdfAnnotation(data);
            }
            return Promise.resolve(true);
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
        prksResolvePdfAnnotationMutationCapability: resolvePdfAnnotationMutationCapability,
        prksPublishAcknowledgedPdfAnnotations: publishAcknowledgedAnnotations,
        prksLoadAcknowledgedPdfAnnotationState: loadAcknowledgedAnnotationState,
        prksPdfAnnotationSyncHandler: handler,
    });
}(typeof window !== 'undefined' ? window : global));
