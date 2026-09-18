/**
 * Viewer projection of effective PDF annotation state.
 *
 * Canonical / durable state is the authority. EmbedPDF is a projection:
 * create / update / delete PRKS-managed user annotations by ID only.
 * Never touch Links, widgets, watermarks, or other non-user artifacts.
 *
 * Reconciliation feedback must not look like user mutations (suppress depth).
 */
(function (root) {
    'use strict';

    const ID_KEYS = ['id', 'uuid', 'annotationId', '_id', 'annotation_id', 'ID'];
    const RECONCILE_DEPTH = '__prksAnnotationReconcileDepth';
    const MANAGED_IDS = '__prksManagedAnnotationIds';

    function annotationId(item) {
        if (!item || typeof item !== 'object') return '';
        if (typeof root.prksPdfAnnotationCanonicalId === 'function') {
            return root.prksPdfAnnotationCanonicalId(item) || '';
        }
        for (let i = 0; i < ID_KEYS.length; i++) {
            if (!Object.prototype.hasOwnProperty.call(item, ID_KEYS[i])) continue;
            const value = item[ID_KEYS[i]];
            if (value == null || typeof value === 'boolean' || typeof value === 'object') continue;
            const text = String(value).trim();
            if (text) return text;
        }
        return '';
    }

    function pageIndexOf(item) {
        if (!item || typeof item !== 'object') return 0;
        const raw = item.pageIndex != null ? item.pageIndex
            : (item.page_index != null ? item.page_index
                : (item.page != null ? item.page : item.pageNumber));
        const n = Number(raw);
        if (Number.isFinite(n) && n >= 0 && Math.trunc(n) === n) return n;
        return 0;
    }

    function viewerRawObjects(viewer) {
        if (!viewer || typeof viewer.getAnnotations !== 'function') return [];
        const out = [];
        for (const a of viewer.getAnnotations() || []) {
            const obj = a && a.raw && typeof a.raw === 'object' ? a.raw : a;
            if (obj && typeof obj === 'object') out.push(obj);
        }
        return out;
    }

    /**
     * Conservative PRKS-managed user-markup classifier for reconcile.
     * Prefer a caller-supplied isManaged when works-pdf's richer filter is available.
     */
    function defaultIsManaged(item) {
        if (!item || typeof item !== 'object') return false;
        if (!annotationId(item)) return false;
        const typeNum = Number(item.type ?? item.annotationType);
        if (typeNum === 9 || typeNum === 10) return true;
        const typ = String(item.type || item.annotationType || item.subtype || '').toLowerCase();
        if (typ === 'highlight' || typ === 'underline') return true;
        const custom = item.custom && typeof item.custom === 'object' ? item.custom : null;
        if (custom && typeof custom.prksComment === 'string') return true;
        if (Array.isArray(item.segmentRects) && item.segmentRects.length > 0) return true;
        return false;
    }

    function isLinkLike(item) {
        if (!item || typeof item !== 'object') return false;
        const typeNum = Number(item.type ?? item.annotationType);
        if (typeNum === 2) {
            // EmbedPDF type 2 is overloaded; URI / GoTo links are not user markup.
            const action = item.action || item.A || item.dest || item.uri || item.url;
            if (action) return true;
        }
        const blob = [item.type, item.annotationType, item.subtype, item.subType]
            .filter(Boolean).map(String).join(' ').toLowerCase();
        return blob.includes('link') || blob.includes('uri') || blob.includes('goto');
    }

    function beginViewerAnnotationReconcile(viewer) {
        if (!viewer || typeof viewer !== 'object') return;
        const depth = Number(viewer[RECONCILE_DEPTH]) || 0;
        viewer[RECONCILE_DEPTH] = depth + 1;
    }

    function endViewerAnnotationReconcile(viewer) {
        if (!viewer || typeof viewer !== 'object') return;
        const depth = Number(viewer[RECONCILE_DEPTH]) || 0;
        viewer[RECONCILE_DEPTH] = Math.max(0, depth - 1);
    }

    function viewerIsReconcilingAnnotations(viewer) {
        return !!(viewer && (Number(viewer[RECONCILE_DEPTH]) || 0) > 0);
    }

    function managedIdSet(viewer) {
        const raw = viewer && viewer[MANAGED_IDS];
        if (raw instanceof Set) return raw;
        return new Set();
    }

    function roundTrip(item) {
        if (typeof root.prksRoundTripPdfAnnotation === 'function') {
            return root.prksRoundTripPdfAnnotation(item);
        }
        return item && typeof item === 'object' ? Object.assign({}, item) : null;
    }

    function semanticallyEqual(left, right) {
        if (typeof root.prksPdfAnnotationsSemanticallyEqual === 'function') {
            return root.prksPdfAnnotationsSemanticallyEqual(left, right);
        }
        return JSON.stringify(left) === JSON.stringify(right);
    }

    /**
     * Align the viewer to effectiveAnnotations (present PRKS-managed set).
     *
     * @param {object} viewer EmbedPDF / PrksPdfViewer handle
     * @param {Array} effectiveAnnotations present annotations (ack + pending overlay)
     * @param {object} [options]
     * @param {function} [options.isManaged] user-markup predicate
     * @param {Iterable<string>} [options.seedManagedIds] ids known managed before this pass
     *   (include known-absent tombstone ids so stale deleted markup is removed
     *   when the viewer opens a lagging PDF with an empty managed-ID set)
     * @param {object} [options.knownAbsent] map of tombstone annotation id → revision;
     *   keys are merged into the managed seed (same role as seedManagedIds)
     * @returns {Promise<{created:number,updated:number,deleted:number,skipped:number}>}
     */
    async function reconcileViewerAnnotations(viewer, effectiveAnnotations, options) {
        const opts = options || {};
        const stats = { created: 0, updated: 0, deleted: 0, skipped: 0 };
        if (!viewer || typeof viewer.getAnnotations !== 'function') return stats;

        const isManaged = typeof opts.isManaged === 'function' ? opts.isManaged : defaultIsManaged;
        const effectiveList = Array.isArray(effectiveAnnotations) ? effectiveAnnotations : [];
        const effectiveById = new Map();
        for (let i = 0; i < effectiveList.length; i++) {
            const rt = roundTrip(effectiveList[i]);
            if (!rt) continue;
            const id = annotationId(rt);
            if (!id) continue;
            effectiveById.set(id, rt);
        }

        const previously = managedIdSet(viewer);
        const seeded = opts.seedManagedIds;
        if (seeded) {
            for (const id of seeded) {
                if (id) previously.add(String(id));
            }
        }
        const knownAbsent = opts.knownAbsent;
        if (knownAbsent && typeof knownAbsent === 'object' && !Array.isArray(knownAbsent)) {
            Object.keys(knownAbsent).forEach(function (id) {
                if (id) previously.add(String(id));
            });
        }
        const nextManaged = new Set(previously);

        beginViewerAnnotationReconcile(viewer);
        if (typeof viewer.beginProgrammaticAnnotationMutation === 'function') {
            viewer.beginProgrammaticAnnotationMutation();
        }
        try {
            const current = viewerRawObjects(viewer);
            const currentById = new Map();
            for (let i = 0; i < current.length; i++) {
                const item = current[i];
                const id = annotationId(item);
                if (!id) continue;
                if (isLinkLike(item)) continue;
                if (!isManaged(item) && !previously.has(id) && !effectiveById.has(id)) continue;
                currentById.set(id, item);
            }

            // Deletes: only previously-known managed IDs absent from effective.
            for (const id of previously) {
                if (effectiveById.has(id)) continue;
                if (!currentById.has(id)) {
                    nextManaged.delete(id);
                    continue;
                }
                const live = currentById.get(id);
                if (isLinkLike(live)) continue;
                if (!isManaged(live) && !previously.has(id)) continue;
                if (typeof viewer.deleteAnnotation === 'function') {
                    await Promise.resolve(viewer.deleteAnnotation(id));
                    stats.deleted += 1;
                }
                nextManaged.delete(id);
                currentById.delete(id);
            }

            for (const [id, desired] of effectiveById) {
                nextManaged.add(id);
                const live = currentById.get(id);
                if (!live) {
                    if (typeof viewer.createAnnotation === 'function') {
                        const page = pageIndexOf(desired);
                        viewer.createAnnotation(page, desired);
                        stats.created += 1;
                    }
                    continue;
                }
                if (semanticallyEqual(live, desired)) {
                    stats.skipped += 1;
                    continue;
                }
                // Prefer update; fall back to delete+create when update is absent.
                if (typeof viewer.updateAnnotation === 'function') {
                    const patch = Object.assign({}, desired);
                    delete patch.id;
                    viewer.updateAnnotation(id, patch);
                    stats.updated += 1;
                } else if (typeof viewer.deleteAnnotation === 'function' &&
                           typeof viewer.createAnnotation === 'function') {
                    await Promise.resolve(viewer.deleteAnnotation(id));
                    viewer.createAnnotation(pageIndexOf(desired), desired);
                    stats.updated += 1;
                }
            }

            viewer[MANAGED_IDS] = nextManaged;
        } finally {
            if (typeof viewer.endProgrammaticAnnotationMutation === 'function') {
                viewer.endProgrammaticAnnotationMutation();
            }
            endViewerAnnotationReconcile(viewer);
        }
        return stats;
    }

    root.prksBeginViewerAnnotationReconcile = beginViewerAnnotationReconcile;
    root.prksEndViewerAnnotationReconcile = endViewerAnnotationReconcile;
    root.prksViewerIsReconcilingAnnotations = viewerIsReconcilingAnnotations;
    root.prksReconcileViewerAnnotations = reconcileViewerAnnotations;
    root.prksDefaultIsManagedPdfAnnotation = defaultIsManaged;
})(typeof window !== 'undefined' ? window : globalThis);
