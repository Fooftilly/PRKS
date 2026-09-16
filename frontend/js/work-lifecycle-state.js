/**
 * Work lifecycle: CREATE_WORK (video) and DELETE_WORK.
 *
 * Construction is video / YouTube only. PDF binary stays on POST /api/works.
 * Pending creations overlay Work detail; ACK fences the same coherence domains
 * the online create path published.
 */
(function (root) {
    'use strict';

    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

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

    async function createWorkDurably(fields, options) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.createWork !== 'function') {
            throw new Error('File creation is not available.');
        }
        /* One local transaction: CREATE_WORK plus any selected ADD_WORK_TAG
         * rows. Wake sync only after that batch commits so create cannot retire
         * before its tag dependents exist. */
        const batch = await sync.store.createWork(fields, options);
        if (typeof sync.changed === 'function') sync.changed();
        return batch;
    }

    async function deleteWorkDurably(workId) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.deleteWork !== 'function') {
            throw new Error('File deletion is not available.');
        }
        const op = await sync.store.deleteWork(workId);
        if (typeof sync.changed === 'function') sync.changed();
        return op;
    }

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.work_id !== op.entity_id) return false;
            if (data.code === 'ACKNOWLEDGED') {
                return typeof data.changed === 'boolean' &&
                    typeof data.folder_id === 'string' &&
                    typeof data.playlist_id === 'string' &&
                    Number.isSafeInteger(data.role_count) && data.role_count >= 0;
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
    });
})(typeof window === 'undefined' ? globalThis : window);
