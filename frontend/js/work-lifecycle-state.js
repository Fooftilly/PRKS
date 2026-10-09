/**
 * Work lifecycle: CREATE_WORK (video) and DELETE_WORK.
 *
 * Construction is video / YouTube only. PDF binary stays on POST /api/works.
 * Pending creations overlay Work detail; ACK fences the same coherence domains
 * the online create path published.
 *
 * CREATE/DELETE classification for the Work route uses:
 *   1. an in-memory live set updated at enqueue (same-session, synchronous)
 *   2. a persisted per-Work metadata marker written atomically with the
 *      CREATE_WORK / DELETE_WORK row as `{ kind, op_id }` (survives reload;
 *      one-key read — never a full listOperations scan on every cached open;
 *      retirement/conflict clears only when that op owns the current marker)
 *
 * Empty-ops Promise.race against the durable queue is forbidden: DELETE_WORK
 * intentionally retains the disposable cache until ACK.
 *
 * Browser-local note recovery drafts (#533) follow the same rule: a delete
 * that is only requested keeps them, since the request cancels never-sent
 * note rows and a draft may then be the only copy of that text if the delete
 * later conflicts. They are removed once the deletion is confirmed: by its
 * acknowledgement, by a never-sent creation folding away, or by the server
 * answering "Work not found" for a Work this device holds drafts of (another
 * device deleted it, or this device stopped between acknowledgement and
 * cleanup).
 */
(function (root) {
    'use strict';

    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    let livePendingDeletes = new Set();
    let livePendingCreates = new Set();
    let liveHydrationArmed = false;

    function pendingDeletions(operations) {
        return new Set((operations || []).filter(function (op) {
            return op && op.operation === 'DELETE_WORK' && deletionAwaitsServer(op);
        }).map(function (op) { return op.entity_id; }));
    }

    function pendingCreates(operations) {
        return (operations || []).filter(function (op) {
            return op && op.operation === 'CREATE_WORK' && deletionAwaitsServer(op);
        });
    }

    function applyLiveFromOperations(operations) {
        livePendingDeletes = pendingDeletions(operations);
        livePendingCreates = new Set(pendingCreates(operations).map(function (op) {
            return op.entity_id;
        }));
    }

    function noteLiveDelete(workId) {
        if (!workId) return;
        livePendingDeletes.add(workId);
        livePendingCreates.delete(workId);
    }

    function noteLiveCreate(workId) {
        if (!workId) return;
        livePendingCreates.add(workId);
        livePendingDeletes.delete(workId);
    }

    function clearLiveLifecycle(workId) {
        if (!workId) return;
        livePendingDeletes.delete(workId);
        livePendingCreates.delete(workId);
    }

    function isLivePendingDeletion(workId) {
        return !!workId && livePendingDeletes.has(workId);
    }

    function isLivePendingCreation(workId) {
        return !!workId && livePendingCreates.has(workId);
    }

    /**
     * Resolve CREATE/DELETE for one Work without scanning the durable queue.
     * Live memory first; otherwise the targeted metadata marker.
     * Returns `'create'`, `'delete'`, or `null`.
     */
    async function resolveWorkLifecycle(workId) {
        if (!workId) return null;
        if (isLivePendingDeletion(workId)) return 'delete';
        if (isLivePendingCreation(workId)) return 'create';
        try {
            if (root.prksSync && root.prksSync.store &&
                typeof root.prksSync.store.getWorkLifecycle === 'function') {
                const kind = await root.prksSync.store.getWorkLifecycle(workId);
                if (kind === 'delete') noteLiveDelete(workId);
                else if (kind === 'create') noteLiveCreate(workId);
                return kind || null;
            }
        } catch (_e) {
            /* Marker unreadable: fail open to prior ops-based paths. */
        }
        return null;
    }

    function armLiveHydration() {
        if (liveHydrationArmed) return true;
        if (!root.prksSync || !root.prksSync.store ||
            typeof root.prksSync.store.listOperations !== 'function') {
            return false;
        }
        liveHydrationArmed = true;
        const refresh = function () {
            return root.prksSync.store.listOperations().then(function (ops) {
                applyLiveFromOperations(ops);
            }).catch(function () { /* live set stays last-known */ });
        };
        void refresh();
        if (typeof root.prksSync.subscribe === 'function') {
            root.prksSync.subscribe(function (event) {
                /* Only the acknowledgement confirms the deletion; the tab
                 * that sends it hears it, whichever tab asked. */
                if (event && event.acknowledged && event.operation === 'DELETE_WORK' &&
                    event.op && event.op.entity_id) {
                    void cleanupDeletedWorkRecovery(event.op.entity_id);
                }
                void refresh();
            });
        }
        armRecoverySweep();
        return true;
    }

    /** Drop Works this device is waiting to delete from a list of rows. */
    function withoutPendingDeletedWorks(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const gone = pendingDeletions(operations);
        if (!gone.size) return rows;
        return rows.filter(function (row) { return row && !gone.has(row.id); });
    }

    /**
     * A Work detail synthesised from a pending CREATE_WORK envelope.
     *
     * Never written into the disposable cache. Enough for the video detail
     * route to render before the server has heard of the id.
     */
    function pendingWorkDetail(op) {
        if (!op || op.operation !== 'CREATE_WORK' || !op.payload) return null;
        const p = op.payload;
        const url = p.source && p.source.url ? String(p.source.url) : '';
        let provider = 'youtube';
        let providerId = '';
        if (typeof root.prksYoutubeVideoId === 'function') {
            providerId = root.prksYoutubeVideoId(url) || '';
        } else if (typeof root.prksExtractYoutubeVideoId === 'function') {
            providerId = root.prksExtractYoutubeVideoId(url) || '';
        }
        const folderId = (p.folder_id || '').trim() || null;
        const playlistId = (p.playlist_id || '').trim() || null;
        return {
            id: op.entity_id,
            title: (p.title || '').trim() || 'Untitled',
            status: p.status || 'Not Started',
            doc_type: 'online',
            abstract: p.abstract || '',
            author_text: p.author_text || '',
            year: p.year || '',
            published_date: p.published_date || null,
            urldate: p.urldate || null,
            private_notes: p.private_notes || '',
            text_content: '',
            file_path: null,
            file_size_bytes: 0,
            thumb_url: p.thumb_url || '',
            thumb_page: null,
            source_kind: 'video',
            source_url: url,
            provider: provider,
            provider_id: providerId,
            folder_id: folderId,
            folder_title: null,
            playlist_id: playlistId,
            playlist_title: null,
            tags: [],
            roles: Array.isArray(p.roles) ? p.roles.map(function (r) {
                return {
                    person_id: r.person_id,
                    role_type: r.role_type,
                    credit_name: r.credit_name || '',
                };
            }) : [],
            linked_authors: [],
            primary_author: null,
            primary_editor: null,
        };
    }

    const RECOVERY_KINDS = ['work-research-note', 'work-private-note'];
    /* Works probed per page load; drafts of a Work that still exists are
     * probed again on a later load. */
    const SWEEP_MAX_WORKS = 8;
    let sweepStarted = false;

    function recoveryRuntime() {
        const api = root.prksEditorRecovery;
        if (!api || typeof api.runtime !== 'function') return null;
        try {
            const rt = api.runtime();
            return rt && typeof rt.cleanupDeletedEntity === 'function' ? rt : null;
        } catch (_e) {
            return null;
        }
    }

    /**
     * Removes the recovery drafts of a Work whose deletion is confirmed. This
     * page's note sessions of it that no pane shows give their lineages back
     * first; with `evenShown`, also those a pane of this page still shows.
     * Ownership-checked and generation-safe in the recovery runtime: a live
     * editor's lineage is kept, and so is one adopted or written since it was
     * read; one more pass reclassifies those. Never throws.
     */
    async function cleanupDeletedWorkRecovery(workId, options) {
        /* No recovery database on this origin: no drafts, and none is created. */
        if (!workId || await recoveryStorageExists() === false) return null;
        const rt = recoveryRuntime();
        if (!rt) return null;
        const entity = { entityType: 'work', entityId: workId };
        try {
            if (typeof root.prksForgetDeletedWorkNotes === 'function') {
                await root.prksForgetDeletedWorkNotes(workId, { evenShown: !!(options && options.evenShown) });
            }
            const report = await rt.cleanupDeletedEntity(entity, RECOVERY_KINDS);
            return report.changed.length ? await rt.cleanupDeletedEntity(entity, RECOVERY_KINDS) : report;
        } catch (_e) {
            /* Left in place: the next page load's sweep retries. */
            return null;
        }
    }

    /** Whether the recovery database exists; null where the browser cannot list databases. */
    async function recoveryStorageExists() {
        const api = root.prksEditorRecovery;
        const factory = root.indexedDB;
        if (!api || !factory || typeof factory.databases !== 'function') return null;
        try {
            return (await factory.databases()).some(function (db) {
                return db && db.name === api.RECOVERY_DB_NAME;
            });
        } catch (_e) {
            return null;
        }
    }

    /** The server's own "Work not found" answer, never a transport failure or another 404. */
    async function serverSaysWorkGone(workId) {
        if (typeof root.prksRequest !== 'function') return false;
        try {
            const response = await root.prksRequest(
                '/api/works/' + encodeURIComponent(workId) + '/notes-state', {},
                { priority: 'background' });
            if (!response || response.status !== 404) return false;
            const body = await response.json();
            return !!body && body.error === 'Work not found';
        } catch (_e) {
            return false;
        }
    }

    /**
     * Work ids whose drafts carry a base read for that Work, or were typed on
     * a creation of this device, excluding Works whose creation is still
     * queued. A creation's draft is asked about once its row is gone: then the
     * server either holds the Work or the creation folded away.
     */
    function sweepCandidates(records, operations) {
        const creating = new Set(pendingCreates(operations).concat((operations || []).filter(function (op) {
            return op && op.operation === 'CREATE_WORK' && op.status === 'conflict';
        })).map(function (op) { return op.entity_id; }));
        const ids = [];
        for (const record of records) {
            if (!record || record.entityType !== 'work' || record.status === 'discarded' ||
                RECOVERY_KINDS.indexOf(record.kind) === -1 || typeof record.entityId !== 'string') continue;
            /* Only a draft typed on a note this device read for that Work, or
             * on its own creation, says the Work is this server's to answer for. */
            const source = record.base && record.base.source;
            if (source !== 'server' && source !== 'cache' && source !== 'pending-create') continue;
            if (creating.has(record.entityId) || ids.indexOf(record.entityId) !== -1) continue;
            ids.push(record.entityId);
        }
        return ids;
    }

    /**
     * Cleans the drafts of Works the server no longer has: deleted on another
     * device, or deleted here with cleanup interrupted. A Work still waiting
     * for its creation to reach the server is never probed, nor one whose
     * drafts carry no base read for it; an unreadable queue probes nothing.
     * Where the browser cannot list its databases, nothing is swept and only
     * acknowledgements clean up. Returns the Work ids it cleaned.
     */
    async function sweepDeletedWorkRecovery() {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.listOperations !== 'function') return [];
        /* A page that never had a draft opens nothing: no database, no runtime. */
        if (await recoveryStorageExists() !== true) return [];
        const rt = recoveryRuntime();
        if (!rt) return [];
        let records;
        let operations;
        try {
            records = await rt.store.listAll();
            if (!records.length) return [];
            operations = await sync.store.listOperations();
        } catch (_e) {
            return [];
        }
        const ids = sweepCandidates(records, operations);
        /* Random order, so Works that still exist never starve the rest. */
        for (let i = ids.length - 1; i > 0; i -= 1) {
            const j = Math.floor(Math.random() * (i + 1));
            const t = ids[i]; ids[i] = ids[j]; ids[j] = t;
        }
        const cleaned = [];
        for (const workId of ids.slice(0, SWEEP_MAX_WORKS)) {
            if (!await serverSaysWorkGone(workId)) continue;
            if (await cleanupDeletedWorkRecovery(workId)) cleaned.push(workId);
        }
        return cleaned;
    }

    /* Once per page load, after the server has first been observed reachable. */
    function armRecoverySweep() {
        if (sweepStarted || typeof root.prksOfflineRuntimeSubscribe !== 'function' ||
            typeof root.prksOfflineRuntimeState !== 'function') return;
        let unsubscribe = null;
        const check = function () {
            if (sweepStarted || root.prksOfflineRuntimeState() !== 'online') return;
            sweepStarted = true;
            if (typeof unsubscribe === 'function') unsubscribe();
            void sweepDeletedWorkRecovery();
        };
        unsubscribe = root.prksOfflineRuntimeSubscribe(check);
    }

    async function createWorkDurably(fields, options) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.createWork !== 'function') {
            throw new Error('File creation is not available.');
        }
        /* One local transaction: CREATE_WORK plus any selected ADD_WORK_TAG
         * rows. Wake sync only after that batch commits so create cannot retire
         * before its tag dependents exist. */
        const batch = await sync.store.createWork(fields, options);
        if (batch && batch.create && batch.create.entity_id) {
            noteLiveCreate(batch.create.entity_id);
        }
        if (typeof sync.changed === 'function') sync.changed();
        return batch;
    }

    async function deleteWorkDurably(workId) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.deleteWork !== 'function') {
            throw new Error('File deletion is not available.');
        }
        const op = await sync.store.deleteWork(workId);
        if (op && op.entity_id) noteLiveDelete(op.entity_id);
        else {
            clearLiveLifecycle(workId);
            /* A creation that never left this device folded away: nothing
             * can refuse this deletion, and no pane can ever save that Work,
             * including the one that is deleting it and still shows it. */
            void cleanupDeletedWorkRecovery(workId, { evenShown: true });
        }
        if (typeof sync.changed === 'function') sync.changed();
        return op;
    }

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.work_id !== op.entity_id) return false;
            if (data.code === 'ACKNOWLEDGED') {
                if (!(typeof data.changed === 'boolean' &&
                    typeof data.folder_id === 'string' &&
                    typeof data.playlist_id === 'string' &&
                    Number.isSafeInteger(data.role_count) && data.role_count >= 0)) {
                    return false;
                }
                const revisions = data.aliases_revisions;
                if (revisions === undefined) return true;
                if (!revisions || typeof revisions !== 'object' || Array.isArray(revisions)) {
                    return false;
                }
                return Object.keys(revisions).every(function (personId) {
                    return typeof personId === 'string' && personId &&
                        Number.isSafeInteger(revisions[personId]) &&
                        revisions[personId] >= 0;
                });
            }
            return data.code === 'FOLDER_NOT_FOUND' || data.code === 'PLAYLIST_NOT_FOUND' ||
                data.code === 'PERSON_NOT_FOUND';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedWork(data),
    };

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.work_id !== op.entity_id) return false;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedWork(data),
    };

    Object.assign(root, {
        prksPendingWorkDeletions: pendingDeletions,
        prksPendingWorkCreates: pendingCreates,
        prksPendingWorkDetail: pendingWorkDetail,
        prksWithoutPendingDeletedWorks: withoutPendingDeletedWorks,
        prksCreateWorkDurably: createWorkDurably,
        prksDeleteWorkDurably: deleteWorkDurably,
        prksWorkCreateSyncHandler: createHandler,
        prksWorkDeleteSyncHandler: deleteHandler,
        prksIsLivePendingWorkDeletion: isLivePendingDeletion,
        prksIsLivePendingWorkCreation: isLivePendingCreation,
        prksResolveWorkLifecycle: resolveWorkLifecycle,
        prksApplyLiveWorkLifecycleFromOperations: applyLiveFromOperations,
        prksArmLiveWorkLifecycleHydration: armLiveHydration,
        prksCleanupDeletedWorkRecovery: cleanupDeletedWorkRecovery,
        prksSweepDeletedWorkRecovery: sweepDeletedWorkRecovery,
    });

    /* sync-runtime.js loads after this module; arm once it publishes prksSync. */
    if (!armLiveHydration() && root.document) {
        root.document.addEventListener('DOMContentLoaded', function () {
            armLiveHydration();
        });
    }
})(typeof window === 'undefined' ? globalThis : window);
