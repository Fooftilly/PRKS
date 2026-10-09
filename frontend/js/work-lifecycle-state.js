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
 * later conflicts. They are removed once this device sees the deletion
 * confirmed: by its acknowledgement, or by a never-sent creation folding
 * away. The Work is marked in localStorage until nothing of it is left, so a
 * later load retries after a shutdown, or once another tab's draft is
 * released; each retry also needs the server's "Work not found". A Work
 * deleted elsewhere keeps its drafts: a 404 from the library this origin
 * serves now proves nothing about the one they were typed against. A draft
 * whose owner tab is not proven gone stays: a frozen tab still holding the
 * text cannot be told from a crashed one.
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
                    markDeletedWork(event.op.entity_id);
                    void cleanupDeletedWorkRecovery(event.op.entity_id);
                }
                void refresh();
            });
        }
        if (typeof root.addEventListener === 'function') root.addEventListener('storage', onDeletedWorksChanged);
        armRecoveryRetry();
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
    /* Works this device saw confirmed deleted (its own DELETE_WORK
     * acknowledged, or its own creation folded away) whose drafts are not
     * all gone yet, as [{ id, tries }]. Only this provenance authorizes a
     * retry: a bare 404 from whatever library this origin serves now proves
     * nothing about the one a draft was typed against. */
    const DELETED_WORKS_KEY = 'prks.workRecoveryCleanup.v1';
    const DELETED_WORKS_MAX = 64;
    /* Works retried per page load; the rest wait for a later load. */
    const RETRY_MAX_WORKS = 8;
    /* Page loads that retry one Work. A draft whose owner is never proven
     * gone (a LAN tab that crashed) then stays, unreachable until drafts can
     * be reviewed apart from their Work, instead of costing a probe per load. */
    const RETRY_MAX_TRIES = 5;
    let retryStarted = false;

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

    function readDeletedWorks() {
        try {
            const value = JSON.parse(root.localStorage.getItem(DELETED_WORKS_KEY) || '[]');
            return Array.isArray(value) ? value.filter(function (entry) {
                return !!entry && typeof entry.id === 'string' && !!entry.id && typeof entry.tries === 'number';
            }) : [];
        } catch (_e) {
            return [];
        }
    }
    function writeDeletedWorks(entries) {
        try {
            if (entries.length) root.localStorage.setItem(DELETED_WORKS_KEY, JSON.stringify(entries.slice(-DELETED_WORKS_MAX)));
            else root.localStorage.removeItem(DELETED_WORKS_KEY);
        } catch (_e) {
            /* Storage unavailable: this load's cleanup still runs, only a retry is lost. */
        }
    }
    function markDeletedWork(workId) {
        const entries = readDeletedWorks().filter(function (entry) { return entry.id !== workId; });
        entries.push({ id: workId, tries: 0 });
        writeDeletedWorks(entries);
    }
    function unmarkDeletedWork(workId) {
        const entries = readDeletedWorks();
        if (entries.some(function (entry) { return entry.id === workId; })) {
            writeDeletedWorks(entries.filter(function (entry) { return entry.id !== workId; }));
        }
    }

    /**
     * Whether recovery storage may hold anything of `workId`, read without
     * building the recovery runtime: false only when the database is known
     * absent and no emergency key names the Work.
     */
    async function recoveryMayHold(workId) {
        const api = root.prksEditorRecovery;
        if (!api) return false;
        if (await recoveryStorageExists() !== false) return true;
        try {
            return api.readEmergencyKeys(root.localStorage).some(function (stored) {
                return !!stored.payload && stored.payload.entries.some(function (entry) {
                    return entry.entityType === 'work' && entry.entityId === workId;
                });
            });
        } catch (_e) {
            return true;
        }
    }

    /**
     * Removes the recovery drafts of a Work whose deletion is confirmed. This
     * page's note sessions of it that no pane shows give their lineages back
     * first; with `evenShown`, also those a pane of this page still shows.
     * Ownership-checked and generation-safe in the recovery runtime: a live
     * editor's lineage is kept, and so is one whose owner page is not proven
     * gone or that was adopted or written since it was read; one more pass
     * reclassifies those. The Work stays marked until nothing of it is left,
     * so a later load retries. Never throws.
     */
    async function cleanupDeletedWorkRecovery(workId, options) {
        if (!workId) return null;
        const entity = { entityType: 'work', entityId: workId };
        try {
            /* Before anything else: a released writer may still commit its last
             * generation, which the check below must then see. */
            if (typeof root.prksForgetDeletedWorkNotes === 'function') {
                await root.prksForgetDeletedWorkNotes(workId, { evenShown: !!(options && options.evenShown) });
            }
            /* No recovery database and no emergency key naming this Work: no
             * drafts, and no runtime, database or channel is created. */
            if (!await recoveryMayHold(workId)) {
                unmarkDeletedWork(workId);
                return { removed: [], live: [], unknown: [], changed: [], suppressed: [] };
            }
            const rt = recoveryRuntime();
            if (!rt) return null;
            let report = await rt.cleanupDeletedEntity(entity, RECOVERY_KINDS);
            if (report.changed.length) report = await rt.cleanupDeletedEntity(entity, RECOVERY_KINDS);
            if (!report.live.length && !report.unknown.length && !report.changed.length) unmarkDeletedWork(workId);
            return report;
        } catch (_e) {
            /* Left in place and still marked: a later load retries. */
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

    /**
     * 'gone' only on the server's own "Work not found"; 'present' when it
     * answers for the Work; 'unknown' on anything else (offline, another 404).
     */
    async function workOnServer(workId) {
        if (typeof root.prksRequest !== 'function') return 'unknown';
        try {
            const response = await root.prksRequest(
                '/api/works/' + encodeURIComponent(workId) + '/notes-state', {},
                { priority: 'background' });
            if (response && response.status >= 200 && response.status < 300) return 'present';
            if (!response || response.status !== 404) return 'unknown';
            const body = await response.json();
            return !!body && body.error === 'Work not found' ? 'gone' : 'unknown';
        } catch (_e) {
            return 'unknown';
        }
    }

    /**
     * Retries the cleanup of Works this device saw confirmed deleted: a
     * shutdown between the confirmation and the cleanup, a draft another tab
     * still held, or an owner not yet proven gone. Each retry also needs the
     * server's "Work not found"; a Work the server holds again (restored)
     * keeps its drafts and loses its mark, and so does one already retried
     * on RETRY_MAX_TRIES loads. Drafts of a Work this device never saw
     * deleted are never removed here, whatever the server answers. Returns
     * the Work ids it ran a cleanup for.
     */
    async function retryDeletedWorkRecovery() {
        const cleaned = [];
        for (const entry of readDeletedWorks().slice(0, RETRY_MAX_WORKS)) {
            if (entry.tries >= RETRY_MAX_TRIES) {
                unmarkDeletedWork(entry.id);
                continue;
            }
            writeDeletedWorks(readDeletedWorks().map(function (current) {
                return current.id === entry.id ? { id: current.id, tries: current.tries + 1 } : current;
            }));
            const answer = await workOnServer(entry.id);
            if (answer === 'present') unmarkDeletedWork(entry.id);
            if (answer !== 'gone') continue;
            if (await cleanupDeletedWorkRecovery(entry.id)) cleaned.push(entry.id);
        }
        return cleaned;
    }

    /* Only the tab holding the sync lock hears an acknowledgement; its mark
     * reaches every other tab of this browser, which then gives back its own
     * sessions of that Work and cleans what it may. */
    function onDeletedWorksChanged(event) {
        if (!event || event.key !== DELETED_WORKS_KEY || !event.newValue) return;
        let before = [];
        try {
            before = (JSON.parse(event.oldValue || '[]') || []).map(function (entry) { return entry && entry.id; });
        } catch (_e) { /* treat every id as new */ }
        for (const entry of readDeletedWorks()) {
            if (entry.tries === 0 && before.indexOf(entry.id) === -1) void cleanupDeletedWorkRecovery(entry.id);
        }
    }

    /* Once per page load, after the server has first been observed reachable,
     * and only when a confirmed deletion is still marked. */
    function armRecoveryRetry() {
        if (retryStarted || !readDeletedWorks().length || typeof root.prksOfflineRuntimeSubscribe !== 'function' ||
            typeof root.prksOfflineRuntimeState !== 'function') return;
        let unsubscribe = null;
        const check = function () {
            if (retryStarted || root.prksOfflineRuntimeState() !== 'online') return;
            retryStarted = true;
            if (typeof unsubscribe === 'function') unsubscribe();
            void retryDeletedWorkRecovery();
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
            markDeletedWork(workId);
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
        prksRetryDeletedWorkRecovery: retryDeletedWorkRecovery,
    });

    /* sync-runtime.js loads after this module; arm once it publishes prksSync. */
    if (!armLiveHydration() && root.document) {
        root.document.addEventListener('DOMContentLoaded', function () {
            armLiveHydration();
        });
    }
})(typeof window === 'undefined' ? globalThis : window);
