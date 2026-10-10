/**
 * Browser-local recovery for Work note editors (#466, #474).
 *
 * One adapter per note kind, built by `prksCreateWorkNoteRecovery(kind)` over
 * the shared `prksEditorRecovery` runtime: Research Notes (`works.js`) and
 * Work Reminders (`ui.js`). The adapter owns everything that is the same for
 * both: the recovery writer of each editor session, the restore plan on
 * mount, acknowledgement and conflict handling, the pane notice view and the
 * Review actions. The kind supplies its editor sessions and how text gets
 * into its editor.
 *
 * Every edit reports the session's newest generation and body to one
 * recovery writer per session entry (`entry.recovery`), independently of the
 * ordinary save debounce. The writer belongs to `prksEditorRecovery`'s page
 * registry, not to TabContext timers, so a warm park, cold release or
 * remount keeps the lineage; it is released only when the session entry
 * itself is dropped. A recovery commit means "recoverable on this device",
 * never "saved": no status here reads as saved because of it.
 *
 * `entry.recoveryBase` is the acknowledged body this session's text was typed
 * on. It is set when a lineage starts, from the kind's `editBase(owner, entry)`
 * when it has one (a Folder session's pinned base) or else from the observed
 * base, and advanced
 * only by the acknowledgement of this session's own queued operation, never
 * by another pane's or tab's, so a foreign edit is never silently treated as
 * the base. The record is cleared only by the exact acknowledged generation
 * and body, or when a save proves the body equals the acknowledged note.
 *
 * A session entry carries: key, workId, ownerTabId, editGeneration, state
 * (committed | drafting | saving | blocked | error), promise, and the
 * `recovery*` fields this module keeps. Its text is `kind.text(entry)`.
 *
 * The kind descriptor:
 * - kind, operation: the draft kind and its queued operation;
 * - bodyField, revisionField: the note on the Work record and its revision in
 *   notes-state, used to prove a base is one server snapshot;
 * - reportSlot: the `ctx.ui` field that holds the pane's review report;
 * - entries(): every session entry of the page; entry(ctx, id): the pane's;
 *   text(entry); hasSession(ctx, id): the pane's session is the authority for
 *   its text (a restore only reports);
 * - paintable(ctx, id, generation): the pane still shows that Work;
 * - target(ctx, id): the live editor Review acts on (holds `recoveryToken`),
 *   shown(target): its text;
 * - install(api, ctx, id, writer, restore, base): a restore on mount;
 * - takeOver(api, ctx, id, target, writer, restore, base): a restore from Review;
 * - replace(ctx, id, target, text): put chosen text in as an edit and save it,
 *   returning the session entry that now holds it;
 * - publish(ctx): repaint the pane's notice; owners(fn): fn(ctx, workId) for
 *   every pane that mounts this editor;
 * - forget(entry): drop a session entry of a deleted Work and release its writer.
 *
 * A kind on another entity (Folder Reminders, #534) also supplies what the
 * Work defaults below assume; each is optional for a Work kind:
 * - entityType: 'work' by default; idOf(entry): the entity id of an entry;
 * - observed(owner): the pane's observed acknowledged base;
 * - ack(event): `{opId, entityId, text, stored, revision}` for an acknowledged
 *   row of this kind, else null (`text` is the row's exact body, `stored` what
 *   the server now holds);
 * - queueRows(rows, id): the unsettled rows of this kind for one entity, as
 *   `{opId, text}`; readRows(): every unsettled row, or null when unreadable;
 *   refreshRows(): the same, for conflict marking;
 * - verifyBase(id, base): whether the server holds `base` exactly;
 * - unchanged(result): a save that proved the body equals the acknowledged note;
 * - stored(text): what the server stores for a saved body (identity for a Work
 *   note; a Folder field is stored trimmed), which is what a base holds.
 */
(function (root) {
    'use strict';

    /* A restore that has not finished by then is abandoned and the editor opens
     * as it would without recovery; nothing it reads later is applied. */
    const RESTORE_MS = 5000;
    const CLOSED_RESCAN_MS = [1000, 4000];
    /* A note body is fingerprinted once per base or own save, never per keystroke. */
    const prints = [];
    let tokenSeq = 0;

    function recovery() {
        const api = root.prksEditorRecovery;
        if (!api || typeof api.runtime !== 'function' || typeof api.planResearchNotesRestore !== 'function') return null;
        try {
            return { api: api, rt: api.runtime() };
        } catch (_e) {
            return null;
        }
    }

    function print(api, text) {
        for (const item of prints) {
            if (item.text === text) return item.print;
        }
        const value = api.fingerprintText(text);
        prints.unshift({ text: text, print: value });
        if (prints.length > 4) prints.pop();
        return value;
    }

    function identity(api, base) {
        if (!base || typeof base.value !== 'string' || !Number.isSafeInteger(base.revision)) return null;
        return { revision: base.revision, length: base.value.length, fingerprint: print(api, base.value) };
    }

    function draftBase(api, base) {
        const id = identity(api, base);
        if (!id) return Object.assign({}, api.UNKNOWN_BASE);
        const source = base.source === 'cache' || base.source === 'pending-create' ? base.source : 'server';
        return Object.assign(id, { source: source });
    }

    function sameOwner(a, b) {
        return !!(a && b && a.generation === b.generation && a.status === b.status && a.owner && b.owner &&
            a.owner.pageInstanceId === b.owner.pageInstanceId && a.owner.paneId === b.owner.paneId &&
            a.owner.claimedAt === b.owner.claimedAt && a.owner.runtimeId === b.owner.runtimeId);
    }

    function expectation(record) {
        return { draftId: record.draftId, pageInstanceId: record.owner.pageInstanceId, generation: record.generation, status: record.status };
    }

    function paneOf(ctx) {
        return String(ctx && ctx.tabId != null ? ctx.tabId : '');
    }

    /* Every adapter built on this page, so a confirmed Work deletion reaches each kind. */
    const adapters = [];

    function workAck(K, event) {
        const op = event && event.op;
        if (!event || !event.acknowledged || !op || op.operation !== K.operation) return null;
        const text = op.payload && typeof op.payload.text === 'string' ? op.payload.text : null;
        const rev = event.acknowledged.server_revision;
        if (text === null || !Number.isSafeInteger(rev)) return null;
        return { opId: op.op_id, entityId: op.entity_id, text: text, stored: text, revision: rev };
    }

    function workQueueRows(K, rows, id) {
        return typeof root.prksWorkNoteOperations === 'function'
            ? root.prksWorkNoteOperations(rows, id, K.kind).map(function (row) {
                return { opId: row.op_id, text: row.payload && typeof row.payload.text === 'string' ? row.payload.text : '' };
            })
            : null;
    }

    /**
     * Whether the server holds `base.value` at `base.revision` for a Work note.
     * The revision was read after the body, so the body is read again and then
     * the revision: when that revision still equals `base.revision`, the body
     * read between the two is the note at that revision. Any failed or cached
     * read is unverified.
     */
    async function verifyWorkBase(K, id, base) {
        if (typeof root.prksOfflineReadEntity !== 'function' || typeof root.prksReadWorkNotesState !== 'function') {
            return false;
        }
        try {
            const work = await root.prksOfflineReadEntity('work', id, '/api/works/' + encodeURIComponent(id), {});
            if (!work || work.source !== 'server' || !work.value) return false;
            const raw = work.value[K.bodyField];
            const body = typeof raw === 'string' ? raw : '';
            if (body !== base.value) return false;
            const state = await root.prksReadWorkNotesState(id);
            return !!(state && state.source === 'server' && state.value &&
                state.value[K.revisionField] === base.revision);
        } catch (_e) {
            return false;
        }
    }

    /* The Work defaults for what a kind on another entity supplies itself. */
    function withDefaults(kind) {
        const K = Object.assign({}, kind);
        if (!K.entityType) K.entityType = 'work';
        if (!K.idOf) K.idOf = function (entry) { return entry ? entry.workId : undefined; };
        if (!K.observed) {
            K.observed = function (owner) {
                return typeof root.prksWorkNoteObserved === 'function' ? root.prksWorkNoteObserved(owner, K.kind) : null;
            };
        }
        if (!K.ack) K.ack = function (event) { return workAck(K, event); };
        if (!K.queueRows) K.queueRows = function (rows, id) { return workQueueRows(K, rows, id); };
        if (!K.readRows) {
            K.readRows = function () {
                return typeof root.prksReadPendingWorkNotesSnapshot === 'function'
                    ? root.prksReadPendingWorkNotesSnapshot() : Promise.resolve(null);
            };
        }
        if (!K.refreshRows) {
            K.refreshRows = function () {
                return typeof root.prksRefreshPendingWorkNotes === 'function'
                    ? root.prksRefreshPendingWorkNotes() : null;
            };
        }
        if (!K.verifyBase) K.verifyBase = function (id, base) { return verifyWorkBase(K, id, base); };
        if (!K.unchanged) K.unchanged = function (result) { return !!result && result.code === 'saved' && !result.opId; };
        if (!K.stored) K.stored = function (text) { return text; };
        return K;
    }

    function create(kind) {
        const K = withDefaults(kind);
        /* Represented drafts (already the exact body of a queued row): op id -> list,
         * cleared on that row's acknowledgement. One from this pane before reload is
         * adopted (`writer`, `key`): the pane's next edit continues that lineage, so
         * a later save that replaces the row still ends in an exact clear. */
        const ackWatch = new Map();
        let restoreMs = RESTORE_MS;
        let stopSync = null;
        let stopWriterEvents = null;
        let stopClosedPages = null;
        let chain = Promise.resolve();

        function entriesArray() {
            return Array.from(K.entries());
        }

        function observedBase(owner) {
            const slot = K.observed(owner);
            if (!slot || typeof slot.value !== 'string' || !Number.isSafeInteger(slot.revision)) return null;
            return { value: slot.value, revision: slot.revision, source: slot.source || 'server' };
        }

        /* Pipeline state for the stored record: informational, never authoritative. */
        function pipeline(entry, patch) {
            const writer = entry && entry.recovery;
            if (!writer) return;
            const current = entry.recoveryPipeline || {
                state: 'drafting', queuedOpId: null, queuedGeneration: 0, blockedBase: null, ownQueued: null,
            };
            const next = Object.assign({}, current, patch);
            const same = Object.keys(next).every(function (k) {
                return JSON.stringify(next[k]) === JSON.stringify(current[k]);
            });
            entry.recoveryPipeline = next;
            if (!same || !entry.recoveryPipelineStored) {
                entry.recoveryPipelineStored = true;
                try {
                    writer.setPipeline(next);
                } catch (_e) { /* recovery is best-effort beside the save path */ }
            }
        }

        function stateOf(entry) {
            if (!entry) return 'drafting';
            if (entry.state === 'committed') {
                if (!entry.recoveryQueued) return 'drafting';
                return entry.recoveryConflictOpId === entry.recoveryQueued.opId ? 'conflict' : 'queued';
            }
            return entry.state === 'saving' || entry.state === 'blocked' || entry.state === 'error' ? entry.state : 'drafting';
        }

        /** Reports the session's newest body to its recovery writer (one lineage per session). */
        function edit(owner, entry) {
            const r = recovery();
            if (!r || !entry) return;
            try {
                void r.rt.start().catch(function () {});
                listen();
                let writer = entry.recovery;
                if (!writer) writer = takeWatched(entry);
                if (!writer) {
                    writer = r.rt.writers.openWriter({
                        kind: K.kind,
                        entityType: K.entityType,
                        entityId: K.idOf(entry),
                        paneId: entry.ownerTabId,
                    });
                    entry.recovery = writer;
                }
                if (writer.state() === 'clean') {
                    /* A fresh lineage: typed on the base the session pinned for this
                     * text, or on the acknowledged body this pane observes now. */
                    entry.recoveryBase = K.editBase ? K.editBase(owner, entry) : observedBase(owner);
                    entry.recoveryQueued = null;
                    entry.recoveryPipeline = null;
                    entry.recoveryPipelineStored = false;
                    writer.setBase(draftBase(r.api, entry.recoveryBase));
                }
                writer.edit(entry.editGeneration, K.text(entry));
                pipeline(entry, { state: 'drafting' });
            } catch (_e) { /* recovery is best-effort beside the save path */ }
        }

        /** The represented lineage this pane adopted on mount becomes the session's lineage. */
        function takeWatched(entry) {
            let taken = null;
            ackWatch.forEach(function (watches, opId) {
                const item = taken ? null : watches.find(function (w) { return w.writer && w.key === entry.key; });
                if (!item) return;
                watches.splice(watches.indexOf(item), 1);
                if (!watches.length) ackWatch.delete(opId);
                taken = item.writer;
                entry.recovery = taken;
                /* Continue the adopted lineage past its stored generation. */
                entry.editGeneration = Math.max(entry.editGeneration, item.generation + 1);
                entry.recoveryBase = item.base;
                entry.recoveryQueued = { opId: opId, generation: item.generation, text: item.text };
                entry.recoveryPipeline = item.pipeline;
                entry.recoveryPipelineStored = true;
            });
            return taken;
        }

        /** The session entry is dropped: finish the last write; the lineage stays for recovery. */
        function release(entry) {
            const writer = entry && entry.recovery;
            if (!writer) return;
            entry.recovery = null;
            void writer.release().catch(function () {});
        }

        /**
         * The Work's deletion is confirmed (#533). A session of it that no pane
         * shows any more can never be saved, and its row was cancelled with the
         * delete request, so no acknowledgement will ever release it: its lineage
         * is given back for the deletion's cleanup. A pane still showing the Work
         * keeps its editor and lineage, unless `evenShown`: a creation that never
         * left this device folded away, so no pane of this page can ever save it.
         * Resolves once those last writes finished.
         */
        function forgetDeleted(workId, evenShown) {
            const id = String(workId);
            const shown = new Set();
            if (!evenShown) {
                K.owners(function (ctx, owned) {
                    if (ctx && String(owned) === id) shown.add(String(ctx.tabId));
                });
            }
            const done = [];
            entriesArray().forEach(function (entry) {
                if (!entry || String(K.idOf(entry)) !== id || !entry.recovery || shown.has(String(entry.ownerTabId))) return;
                const writer = entry.recovery;
                K.forget(entry);
                done.push(writer.release().catch(function () {}));
            });
            ackWatch.forEach(function (watches, opId) {
                const kept = watches.filter(function (watch) {
                    if (watch.workId !== id || shown.has(watch.tabId)) return true;
                    if (watch.writer) done.push(watch.writer.release().catch(function () {}));
                    return false;
                });
                if (kept.length) ackWatch.set(opId, kept);
                else ackWatch.delete(opId);
            });
            /* Resolves to the panes that still mount this editor for the Work,
             * typed in or not: each may yet commit an edit. */
            return Promise.all(done).then(function () { return shown.size; });
        }

        /** Starts the recovery write before an ordinary save is queued. */
        function flush(entry) {
            const writer = entry && entry.recovery;
            if (writer) void writer.flush().catch(function () {});
        }

        /**
         * This session queued `text` (generation `generation`) as row `opId`, from
         * `base`. Recorded with the #475 provenance, whether or not a newer save has
         * taken over the session since: it is what a newer body was typed on.
         */
        function queued(entry, opId, generation, text, base) {
            const r = recovery();
            if (!r || !entry || !entry.recovery || !opId) return;
            try {
                entry.recoveryQueued = { opId: opId, generation: generation, text: text };
                const own = {
                    opId: opId,
                    textLength: text.length,
                    textFingerprint: print(r.api, text),
                    base: identity(r.api, base) || { revision: null, length: null, fingerprint: null },
                };
                /* Its acknowledgement makes the base what the server stores for it. */
                const kept = K.stored(text);
                if (kept !== text) {
                    own.storedLength = kept.length;
                    own.storedFingerprint = print(r.api, kept);
                }
                pipeline(entry, { queuedOpId: opId, queuedGeneration: generation, ownQueued: own });
            } catch (_e) { /* recovery is best-effort beside the save path */ }
        }

        /** A save settled: record its pipeline outcome; clear only what the server provably holds. */
        function settled(owner, id, entry, result, saved) {
            const r = recovery();
            if (!r || !entry || !entry.recovery) return;
            try {
                const code = result && result.code;
                const patch = { state: stateOf(entry) };
                if (entry.state === 'blocked') {
                    patch.blockedBase = identity(r.api, entry.blockedBase);
                }
                pipeline(entry, patch);
                /* Nothing queued and the body is the acknowledged note (A -> B -> A,
                 * or a save of an unchanged body): proven equal, so clear it. */
                if (K.unchanged(result) && saved && entry.state === 'committed' &&
                    entry.editGeneration === saved.generation && K.paintable(owner, id)) {
                    const slot = observedBase(owner);
                    if (slot && slot.source === 'server' && slot.value === K.stored(saved.text)) {
                        /* No queued row will acknowledge it, so repaint here: a warning
                         * shown while recovery storage failed goes once the text is clean. */
                        void entry.recovery.acknowledged(saved.generation, saved.text).then(function () {
                            if (entry.recovery && entry.recovery.state() !== 'unprotected') entry.recoveryUnprotectedCode = null;
                            publishFor(entry);
                        }).catch(function () {});
                    }
                }
            } catch (_e) { /* recovery is best-effort beside the save path */ }
        }

        /*
         * Another tab closed: a draft it left on a Work already open here can now be
         * reviewed, so every pane that mounts this editor re-plans for review only.
         */
        function watchClosedPages(r) {
            /* Another tab's final pagehide writes one key per page under this prefix (editor-recovery/schema.ts). */
            const prefix = r.api.CLOSED_PAGE_KEY_PREFIX;
            if (stopClosedPages || typeof prefix !== 'string' || typeof root.addEventListener !== 'function') return;
            let timers = [];
            const refreshAll = function () {
                K.owners(function (ctx, workId) {
                    if (workId != null) void refresh(ctx, workId);
                });
            };
            const onStorage = function (event) {
                if (!event || typeof event.key !== 'string' || event.key.indexOf(prefix) !== 0 || event.newValue === null) return;
                /*
                 * The record is written during the closing page's final pagehide, while
                 * it may still answer pings; until it stops, its draft reads as live.
                 * Re-plan once it has had time to go, and once more for a slow close.
                 */
                timers.forEach(clearTimeout);
                timers = CLOSED_RESCAN_MS.map(function (ms) {
                    return setTimeout(refreshAll, ms);
                });
            };
            root.addEventListener('storage', onStorage);
            stopClosedPages = function () {
                root.removeEventListener('storage', onStorage);
                timers.forEach(clearTimeout);
                timers = [];
            };
        }

        function listen() {
            const r = recovery();
            if (r) watchWriters(r);
            if (r) watchClosedPages(r);
            if (stopSync || !root.prksSync || typeof root.prksSync.subscribe !== 'function') return;
            stopSync = root.prksSync.subscribe(onSync);
        }

        /** One page-level subscription: acknowledgements and conflicts of this kind's queued rows. */
        function onSync(event) {
            const r = recovery();
            if (!r) return;
            const ack = K.ack(event);
            if (ack) {
                const text = ack.text;
                const rev = ack.revision;
                const watches = ackWatch.get(ack.opId) || [];
                ackWatch.delete(ack.opId);
                watches.forEach(function (watched) {
                    watched.acknowledged = true;
                    /* An adoption still in flight settles first, so the writer it
                     * yields clears the lineage it now owns. */
                    void Promise.resolve(watched.adopting).then(function () {
                        const writer = watched.writer;
                        const clear = watched.text !== text ? Promise.resolve()
                            : writer ? writer.acknowledged(watched.generation, text)
                                /* Only while the page it observed still owns it: never under a writer that adopted it since. */
                                : r.rt.store.deleteIfAcknowledged(watched.draftId, watched.generation, text, watched.pageInstanceId);
                        return clear.catch(function () {}).then(function () {
                            if (writer) return writer.release();
                            return undefined;
                        });
                    }).catch(function () {});
                });
                entriesArray().forEach(function (entry) {
                    const q = entry.recoveryQueued;
                    if (!entry.recovery || String(K.idOf(entry)) !== String(ack.entityId) || !q) return;
                    if (q.opId !== ack.opId || q.text !== text) return;
                    /* This session's own operation: what the server stores for it is
                     * now the base the session's newer text was typed on. */
                    entry.recoveryQueued = null;
                    entry.recoveryBase = { value: ack.stored, revision: rev, source: 'server' };
                    try {
                        entry.recovery.setBase(draftBase(r.api, entry.recoveryBase));
                        pipeline(entry, { state: stateOf(entry), queuedOpId: null, queuedGeneration: 0 });
                        const writer = entry.recovery;
                        void writer.acknowledged(q.generation, text).catch(function () {}).then(function () {
                            /* A copy storage refused is now saved: the warning ends. */
                            publishFor(entry);
                            if (K.acknowledged) K.acknowledged(entry, writer);
                        });
                    } catch (_e) { /* best-effort */ }
                });
                return;
            }
            if (!entriesArray().some(function (entry) { return entry.recovery && entry.recoveryQueued; })) return;
            const refreshing = K.refreshRows();
            if (!refreshing) return;
            void Promise.resolve(refreshing).then(function (rows) {
                const conflicted = new Set((rows || []).filter(function (row) {
                    return row && row.status === 'conflict';
                }).map(function (row) { return row.op_id; }));
                entriesArray().forEach(function (entry) {
                    if (entry.recovery && entry.recoveryQueued && conflicted.has(entry.recoveryQueued.opId)) {
                        entry.recoveryConflictOpId = entry.recoveryQueued.opId;
                        pipeline(entry, { state: stateOf(entry) });
                    }
                });
            }).catch(function () {});
        }

        /**
         * Restore on mount, between `ensureBase` and the first read of the session
         * text. Applies `planResearchNotesRestore`: restores the one draft that
         * cannot overwrite anything (this tab before reload, another pane of this
         * tab, or a tab proven closed); drafts proven equal to the server note are
         * cleared; everything else is kept untouched, never enqueued, and reported
         * on `ctx.ui[kind.reportSlot]`, which the pane shows as a notice with a
         * Review action. A pane whose session is already the authority for its
         * text, or a refresh after Review, only recomputes that report. Restores
         * run one at a time.
         */
        function restore(ctx, work, options) {
            const attempt = { abandoned: false };
            let timer = null;
            const timeout = new Promise(function (resolve) {
                timer = setTimeout(function () {
                    attempt.abandoned = true;
                    /* Too slow to plan: nothing is restored, but the drafts stay reachable. */
                    void reportUnchecked(ctx, work, ctx ? ctx.generation : null);
                    resolve(null);
                }, restoreMs);
            });
            const run = chain.then(function () {
                return attempt.abandoned ? null : restoreNow(ctx, work, attempt, options);
            }).catch(function () { return null; });
            /* An abandoned run that never settles must not hold up later restores. */
            const settledRun = Promise.race([run, timeout]);
            chain = settledRun;
            return settledRun.then(function (result) {
                clearTimeout(timer);
                return result;
            });
        }

        /*
         * A restore that ran out of time lists every remaining draft as unchecked so
         * the notice and Review stay reachable; Review classifies them itself.
         */
        async function reportUnchecked(ctx, work, generation) {
            const r = recovery();
            if (!r || !ctx || !work || work.id == null) return;
            const id = String(work.id);
            try {
                const records = await r.rt.store.listByEntity(K.kind, id);
                if (!K.paintable(ctx, id, generation)) return;
                const review = records.filter(function (record) { return record.status !== 'discarded'; }).map(function (record) {
                    return {
                        draftId: record.draftId,
                        reason: 'ownership-unknown',
                        lineage: 'unknown',
                        generation: record.generation,
                        bodyLength: record.bodyLength,
                        paneId: record.owner.paneId,
                        updatedAt: record.updatedAt,
                        status: record.status,
                        action: null,
                    };
                });
                if (review.length) report(ctx, id, review);
            } catch (_e) {
                /* Recovery storage unreadable: nothing to list. */
            }
        }

        /* Unsettled rows of this kind for one Work; null when the queue could not be read. */
        async function readQueue(id) {
            const rows = await K.readRows();
            /* An unread queue is unknown, never empty. */
            return rows ? K.queueRows(rows, id) : null;
        }

        function otherDirty(id, key) {
            return entriesArray().some(function (entry) {
                return String(K.idOf(entry)) === id && entry.key !== key && entry.state !== 'committed';
            });
        }

        async function candidateOf(rt, record, askingSession) {
            const lineage = await rt.classify(record, askingSession);
            let body = null;
            if (lineage !== 'self-live' && lineage !== 'other-live') {
                const row = await rt.store.getBody(record.draftId);
                body = row && row.generation === record.generation ? row.body : null;
                /* Classified before the body read: if the record moved meanwhile (another
                 * tab or pane adopted it), its text is not shown on the old answer. */
                const now = body === null ? record : await rt.store.get(record.draftId);
                if (!sameOwner(record, now)) {
                    return { record: now || record, body: null, lineage: now ? await rt.classify(now, askingSession) : lineage };
                }
            }
            return { record: record, body: body, lineage: lineage };
        }

        async function restoreNow(ctx, work, attempt, options) {
            const r = recovery();
            if (!r || !ctx || !work || work.id == null) return null;
            const id = String(work.id);
            const generation = ctx.generation;
            const key = K.key(ctx, id);
            /* A live session in this pane is the authority for its text: report only. */
            const refreshing = !!(options && options.reviewOnly);
            const reviewOnly = refreshing || !!(options && options.typed) || K.hasSession(ctx, id);
            const current = function () {
                return !(attempt && attempt.abandoned) && K.paintable(ctx, id, refreshing ? undefined : generation) &&
                    (reviewOnly || !K.hasSession(ctx, id));
            };
            /* Typing in this pane while the restore waited only rules out applying a
             * draft: the drafts are planned again for review, so none goes unlisted. */
            const bail = function () {
                const typed = !reviewOnly && !(attempt && attempt.abandoned) && K.paintable(ctx, id, generation) &&
                    K.hasSession(ctx, id);
                return typed ? restoreNow(ctx, work, attempt, Object.assign({}, options, { typed: true })) : null;
            };
            if (!current()) return null;
            const rt = r.rt;
            listen();
            await rt.scanEmergency();
            const records = await rt.store.listByEntity(K.kind, id);
            if (!current()) return bail();
            if (!records.length) {
                report(ctx, id, []);
                return null;
            }
            const session = K.entry(ctx, id);
            const asking = session && session.recovery ? session.recovery.sessionKey : null;
            /* Classified together: each owner check waits on silence, so serial checks add up. */
            const candidates = await Promise.all(records.map(function (record) {
                return candidateOf(rt, record, asking);
            }));
            const queue = await readQueue(id);
            if (!current()) return bail();
            const base = observedBase(ctx);
            const dirtyElsewhere = otherDirty(id, key);
            const planWith = function (planBase) {
                return r.api.planResearchNotesRestore({
                    paneId: paneOf(ctx),
                    candidates: candidates,
                    base: planBase,
                    queue: queue,
                    otherDirtySession: dirtyElsewhere,
                    /* Never replace what an open editor shows; Review recomputes exactly. */
                    editorDirty: reviewOnly,
                    stored: K.stored,
                });
            };
            let plan = planWith(base);
            /* The body and the revision were read separately. Before anything is
             * cleared or restored on them, prove they are one server snapshot;
             * otherwise every draft stays for review. */
            if ((plan.cleanup.length || plan.restore) && !(await verifyBase(id, base))) {
                if (!current()) return bail();
                plan = planWith(Object.assign({}, base, { source: 'cache' }));
            }
            if (!current()) return bail();
            for (const draftId of plan.cleanup) {
                const candidate = candidates.find(function (c) { return c.record.draftId === draftId; });
                const record = candidate.record;
                /* Only the generation and body it read (which the server stores as
                 * the note), and only while no page has adopted it since. */
                void rt.store.deleteIfAcknowledged(draftId, record.generation, candidate.body, record.owner.pageInstanceId)
                    .catch(function () {});
            }
            const paneId = paneOf(ctx);
            for (const item of plan.represented) {
                const candidate = candidates.find(function (c) { return c.record.draftId === item.draftId; });
                const watches = ackWatch.get(item.opId) || [];
                if (watches.some(function (w) { return w.draftId === item.draftId; })) continue;
                const watch = Object.assign({
                    writer: null, adopting: null, acknowledged: false, key: key, base: base,
                    pipeline: candidate.record.pipeline, workId: String(id), tabId: String(ctx.tabId),
                }, item);
                /* Every lineage the row represents is cleared by its acknowledgement,
                 * so it is watched before adopting: an acknowledgement that lands
                 * while the adoption is pending still finds it. */
                watches.push(watch);
                ackWatch.set(item.opId, watches);
                if (!reviewOnly && candidate.lineage === 'same-runtime-orphan' && candidate.record.owner.paneId === paneId) {
                    /* Assigned before the adoption starts, so no acknowledgement can miss it. */
                    watch.adopting = Promise.resolve().then(function () {
                        return rt.writers.adopt(candidate.record, { paneId: paneId });
                    }).then(function (writer) {
                        watch.writer = writer;
                        /* Stale mount: give the lineage back, unless the acknowledgement
                         * already claimed this writer to clear it. */
                        if (writer && !watch.acknowledged && !current()) {
                            watch.writer = null;
                            /* The adoption made this page the owner: a later
                             * acknowledgement clears it under that owner. */
                            watch.pageInstanceId = rt.identity.pageInstanceId;
                            void writer.release().catch(function () {});
                        }
                    }).catch(function () {});
                    await watch.adopting;
                }
            }
            let restored = false;
            if (plan.restore && !reviewOnly) {
                const writer = await rt.writers.adopt(plan.restore.record, { paneId: paneId });
                /* The plan was made before adopting. Another pane may have saved,
                 * queued or started editing meanwhile; then the draft stays unapplied
                 * for review rather than being saved over a base it was not typed on. */
                const changed = writer && current() ? await changedSince(ctx, id, key, base, queue, true) : null;
                if (writer && (!current() || changed)) {
                    /* Stale mount or moved on: the lineage stays this pane's orphan. */
                    void writer.release().catch(function () {});
                    if (!current()) return bail();
                    if (changed && current()) {
                        const record = plan.restore.record;
                        plan.review.push({
                            draftId: record.draftId,
                            reason: changed,
                            lineage: 'same-runtime-orphan',
                            generation: record.generation,
                            bodyLength: record.bodyLength,
                            paneId: record.owner.paneId,
                            updatedAt: record.updatedAt,
                            status: record.status,
                            /* Now this page's orphan: Review can compare it, never apply it silently. */
                            action: changed === 'base-advanced' || changed === 'foreign-queue' ? 'reconcile' : null,
                        });
                    }
                } else if (writer) {
                    K.install(r.api, ctx, id, writer, plan.restore, base);
                    restored = true;
                }
            }
            /* Out of time: the unchecked report owns the notice; an empty review here would clear it. */
            if (attempt && attempt.abandoned) return null;
            report(ctx, id, plan.review, queue);
            return { restored: restored, review: plan.review };
        }

        /** Whether the server holds `base.value` at `base.revision` (the kind's `verifyBase`). */
        async function verifyBase(id, base) {
            if (!base || base.source !== 'server') return false;
            try {
                return !!(await K.verifyBase(id, base));
            } catch (_e) {
                return false;
            }
        }

        /* `base` when it is proven one server snapshot, else the same base marked unverified. */
        async function checkedBase(id, base) {
            if (!base || base.source !== 'server') return base;
            return (await verifyBase(id, base)) ? base : Object.assign({}, base, { source: 'cache' });
        }

        /**
         * Why a restore planned on `base` and `queue` is no longer safe to apply, or
         * null. With `verifyServer`, a base proven from the server is proven again
         * first (another device may have saved meanwhile without telling this page).
         * The queue is then re-read fail-closed; everything after that read is
         * synchronous up to the install.
         */
        async function changedSince(ctx, id, key, base, queue, verifyServer) {
            if (verifyServer && base && base.source === 'server' && !(await verifyBase(id, base))) {
                return 'base-advanced';
            }
            const rows = await K.readRows();
            const now = rows ? K.queueRows(rows, id) : null;
            if (!now || !queue) return 'queue-unknown';
            const observed = observedBase(ctx);
            /* No base was observed at plan time: one appearing since is an advance; the queue and other panes still count. */
            if (!base) {
                if (observed) return 'base-advanced';
            } else if (!observed || observed.value !== base.value || observed.revision !== base.revision ||
                observed.source !== base.source) {
                return 'base-advanced';
            }
            const sameQueue = now.length === queue.length && now.every(function (row, i) {
                return row.opId === queue[i].opId && row.text === queue[i].text;
            });
            if (!sameQueue) return 'foreign-queue';
            return otherDirty(id, key) ? 'dirty-session' : null;
        }

        /** Records what this pane's notice offers for review, and repaints it. */
        function report(ctx, id, review, queue) {
            if (!ctx || !ctx.ui) return;
            /* `queue` as the plan read it: undefined when not read, null when unreadable. */
            const pendingSync = queue === undefined ? 'none' : (queue === null ? 'unknown' : (queue.length ? 'queued' : 'none'));
            ctx.ui[K.reportSlot] = review.length
                ? { status: 'needs-review', workId: id, candidates: review, pendingSync: pendingSync } : null;
            K.publish(ctx);
        }

        /*
         * Recovery notice and Review (#466 slice 3).
         *
         * The pane's notice reads `view`: how many drafts this pane's last restore
         * left for review (never one a live editor owns) and whether this pane's own
         * newest text is unprotected because recovery storage refused it. Review
         * reads `details` and acts through restore, replace and discard below.
         * Every action re-reads the record, re-classifies its owner and applies
         * only through the store's compare-and-set, against the editor session the
         * dialog was opened for (`token`); anything that changed meanwhile is
         * refused and the dialog refreshes. Nothing here writes the queue except
         * the ordinary save path, and only after the user chose the text.
         */
        function view(ctx) {
            if (!ctx || !ctx.ui || ctx.destroyed) return null;
            const live = ctx.getEntity ? ctx.getEntity(K.entityType) : null;
            if (!live || live.id == null) return null;
            const id = String(live.id);
            const review = ctx.ui[K.reportSlot];
            /* Another live editor's draft is that editor's, not a recovery. */
            const drafts = review && review.workId === id
                ? review.candidates.filter(function (c) { return c.lineage !== 'self-live' && c.lineage !== 'other-live'; })
                : [];
            const entry = K.entry(ctx, id);
            let unprotected = null;
            try {
                if (entry && entry.recovery && entry.recovery.state() === 'unprotected') {
                    unprotected = entry.recoveryUnprotectedCode || 'unknown';
                }
            } catch (_e) { /* best-effort */ }
            if (!drafts.length && !unprotected) return null;
            return {
                workId: id,
                drafts: drafts.length,
                incomplete: drafts.filter(function (c) { return c.status === 'tail-missing' || c.reason === 'body-missing'; }).length,
                pendingSync: drafts.length && review.pendingSync ? review.pendingSync : 'none',
                unprotected: unprotected,
            };
        }

        /* Repaints the pane that owns this session entry. */
        function publishFor(entry) {
            if (!entry || typeof root.prksForEachLiveTabContext !== 'function') return;
            root.prksForEachLiveTabContext(function (ctx) {
                if (paneOf(ctx) !== String(entry.ownerTabId)) return;
                const shown = ctx.getEntity ? ctx.getEntity(K.entityType) : null;
                if (shown && String(shown.id) === String(K.idOf(entry))) K.publish(ctx);
            });
        }

        /* One page-level subscription: a session's recovery copy failed or caught up. */
        function watchWriters(r) {
            if (stopWriterEvents || typeof r.rt.onWriterEvent !== 'function') return;
            stopWriterEvents = r.rt.onWriterEvent(function (event) {
                if (!event || (event.type !== 'unprotected' && event.type !== 'protected')) return;
                entriesArray().forEach(function (entry) {
                    if (!entry.recovery || entry.recovery.sessionKey !== event.sessionKey) return;
                    entry.recoveryUnprotectedCode = event.type === 'unprotected' ? event.code : null;
                    publishFor(entry);
                });
            });
        }

        /* The editor shows something other than the acknowledged note, or its session has unsettled text. */
        function editorDirty(entry, target, base) {
            const shown = target ? K.shown(target) : null;
            if (!base || shown !== base.value) return true;
            return !!(entry && (entry.state !== 'committed' || entry.recoveryQueued || entry.promise));
        }

        /* The live editor the dialog was opened for, or null when the pane, Work or session moved on. */
        function targetFor(ctx, workId, token) {
            const target = K.target(ctx, workId);
            if (!target || !token || target.recoveryToken !== token) return null;
            return target;
        }

        function matches(record, expect, id) {
            return !!(record && expect && record.status !== 'discarded' && record.kind === K.kind &&
                String(record.entityId) === id && record.owner.pageInstanceId === expect.pageInstanceId &&
                record.generation === expect.generation && record.status === expect.status);
        }

        /**
         * Everything Review shows for one Work in this pane: the current note (the
         * editor's text, the acknowledged revision and whether the base was read from
         * the server), the unsettled queue, and every recovery record with its owner
         * class, stored body and the one action it allows now.
         */
        async function detailsNow(ctx, workId) {
            const r = recovery();
            const id = String(workId || '');
            const target = K.target(ctx, id);
            if (!r || !target) return null;
            if (!target.recoveryToken) target.recoveryToken = 'review-' + (++tokenSeq);
            const token = target.recoveryToken;
            const rt = r.rt;
            await rt.start();
            await rt.scanEmergency();
            const key = K.key(ctx, id);
            const entry = K.entry(ctx, id);
            const asking = entry && entry.recovery ? entry.recovery.sessionKey : null;
            const records = await rt.store.listByEntity(K.kind, id);
            const candidates = await Promise.all(records.filter(function (record) {
                return record.status !== 'discarded';
            }).map(function (record) {
                return candidateOf(rt, record, asking);
            }));
            const queue = await readQueue(id);
            if (!targetFor(ctx, id, token)) return null;
            const observed = observedBase(ctx);
            /* Review offers nothing on a base that is not one server snapshot (#490). */
            const base = await checkedBase(id, observed);
            if (!targetFor(ctx, id, token)) return null;
            const paneId = paneOf(ctx);
            const dirtyElsewhere = otherDirty(id, key);
            const dirtyHere = editorDirty(entry, target, observed);
            /* This editor's own live session is what the editor shows, not a draft to review. */
            const out = candidates.filter(function (c) { return c.lineage !== 'self-live'; }).map(function (c) {
                /* Each judged on its own: the reviewer weighs them against each other. */
                const plan = r.api.planResearchNotesRestore({
                    paneId: paneId, candidates: [c], base: base, queue: queue,
                    otherDirtySession: dirtyElsewhere, editorDirty: dirtyHere, stored: K.stored,
                });
                const judged = plan.review[0];
                let reason = judged ? judged.reason : 'restorable';
                let action = judged ? judged.action : 'restore';
                if (plan.cleanup.length) { reason = 'saved'; action = null; }
                if (plan.represented.length) { reason = 'queued'; action = null; }
                const live = c.lineage === 'self-live' || c.lineage === 'other-live';
                return {
                    expect: expectation(c.record),
                    draftId: c.record.draftId,
                    lineage: c.lineage,
                    samePane: c.record.owner.paneId === paneId,
                    status: c.record.status,
                    reason: reason,
                    action: action,
                    generation: c.record.generation,
                    updatedAt: c.record.updatedAt,
                    length: c.body !== null ? c.body.length : c.record.bodyLength,
                    /* Another live editor's text stays there; it is not offered as a recovery. */
                    body: live ? null : c.body,
                    typedOnRevision: c.record.base ? c.record.base.revision : null,
                    pipelineState: c.record.pipeline ? c.record.pipeline.state : null,
                };
            });
            return {
                workId: id,
                token: token,
                current: {
                    text: K.shown(target),
                    revision: base ? base.revision : null,
                    source: base ? base.source : 'unknown',
                    queue: queue === null ? 'unknown' : (queue.length ? 'queued' : 'none'),
                    queued: queue === null ? 0 : queue.length,
                    unsaved: !!(entry && entry.state !== 'committed'),
                },
                candidates: out,
            };
        }

        /* Re-reads one reviewed record and judges it alone against the pane as it is now. */
        async function judge(r, ctx, id, token, expect) {
            const rt = r.rt;
            const record = await rt.store.get(expect && expect.draftId);
            if (!matches(record, expect, id)) return { code: 'changed' };
            const key = K.key(ctx, id);
            const entry = K.entry(ctx, id);
            const candidate = await candidateOf(rt, record, entry && entry.recovery ? entry.recovery.sessionKey : null);
            const queue = await readQueue(id);
            const target = targetFor(ctx, id, token);
            if (!target) return { code: 'stale' };
            const observed = observedBase(ctx);
            const dirtyHere = editorDirty(K.entry(ctx, id), target, observed);
            const planWith = function (planBase) {
                return r.api.planResearchNotesRestore({
                    paneId: paneOf(ctx),
                    candidates: [candidate],
                    base: planBase,
                    queue: queue,
                    otherDirtySession: otherDirty(id, key),
                    editorDirty: dirtyHere,
                    stored: K.stored,
                });
            };
            let plan = planWith(observed);
            /* A restore applies the draft as is: only on a base proven one server snapshot (#490). */
            if (plan.restore) {
                const checked = await checkedBase(id, observed);
                if (!targetFor(ctx, id, token)) return { code: 'stale' };
                if (checked !== observed) plan = planWith(checked);
            }
            const action = plan.restore ? 'restore' : (plan.review[0] ? plan.review[0].action : null);
            return { candidate: candidate, plan: plan, base: observed, queue: queue, action: action };
        }

        /** Restore for editing: the draft overwrites nothing, so it continues its own lineage here. */
        async function restoreReviewedNow(ctx, workId, token, expect) {
            const r = recovery();
            const id = String(workId || '');
            if (!r || !targetFor(ctx, id, token)) return { ok: false, code: 'stale' };
            const judged = await judge(r, ctx, id, token, expect);
            if (judged.code) return { ok: false, code: judged.code };
            if (!judged.plan.restore) return { ok: false, code: 'changed' };
            const writer = await r.rt.writers.adopt(judged.candidate.record, { paneId: paneOf(ctx) });
            if (!writer) return { ok: false, code: 'changed' };
            const key = K.key(ctx, id);
            /* Adopting was asynchronous: the queue, the base and other panes are read again (#490). */
            const changed = targetFor(ctx, id, token)
                ? await changedSince(ctx, id, key, judged.base, judged.queue, true) : 'stale';
            const target = targetFor(ctx, id, token);
            const base = observedBase(ctx);
            if (changed || !target || editorDirty(K.entry(ctx, id), target, base)) {
                /* Moved on while adopting: the lineage stays this page's orphan, still listed. */
                void writer.release().catch(function () {});
                return { ok: false, code: target && changed !== 'stale' ? 'changed' : 'stale' };
            }
            /* The editor's clean session gives way to the restored lineage. */
            K.takeOver(r.api, ctx, id, target, writer, judged.plan.restore, base);
            return { ok: true };
        }

        /**
         * Reconciliation: the user compared the draft with the current note and chose
         * `text`. Applied only while the current note is still exactly what they
         * compared (`shown`), after this page took the record by compare-and-set; the
         * text then saves through the ordinary path as this pane's edit, and the
         * reviewed record, now superseded by it, is removed.
         */
        async function replaceNow(ctx, workId, token, expect, text, shown) {
            const r = recovery();
            const id = String(workId || '');
            if (!r || typeof text !== 'string' || !shown || !targetFor(ctx, id, token)) {
                return { ok: false, code: 'stale' };
            }
            const judged = await judge(r, ctx, id, token, expect);
            if (judged.code) return { ok: false, code: judged.code };
            if (judged.action !== 'restore' && judged.action !== 'reconcile') return { ok: false, code: 'changed' };
            const unchanged = function () {
                const target = targetFor(ctx, id, token);
                const base = observedBase(ctx);
                return target && K.shown(target) === shown.text && (base ? base.revision : null) === shown.revision ? target : null;
            };
            if (!unchanged()) return { ok: false, code: 'current-changed' };
            const claim = await r.rt.claimReviewed(expect, paneOf(ctx));
            if (!claim || claim.outcome !== 'ok') return { ok: false, code: 'changed' };
            /* Claiming was asynchronous: pending sync, the base and other panes are read again. */
            /* A note shown from the server is proven again; one shown from the cache
             * (offline) has nothing to prove against, and the save's revision check guards it. */
            const moved = await changedSince(ctx, id, K.key(ctx, id), judged.base, judged.queue, true);
            if (moved) return { ok: false, code: 'current-changed' };
            const target = unchanged();
            /* Claimed but not applied: it reads as this page's orphan and stays listed. */
            if (!target) return { ok: false, code: 'current-changed' };
            const entry = K.replace(ctx, id, target, text);
            /*
             * The reviewed draft goes only once the replacement has its own recovery
             * copy. When recovery storage refuses it, the reviewed draft stays listed
             * and the replacement is protected by the leave guard until it saves.
             */
            const writer = entry && entry.recovery;
            if (!writer) return { ok: true };
            const generation = entry.editGeneration;
            await writer.flush().catch(function () {});
            if (!(writer.committedGeneration() >= generation)) return { ok: true };
            await r.rt.discardReviewed({
                draftId: expect.draftId,
                pageInstanceId: r.rt.identity.pageInstanceId,
                generation: expect.generation,
                status: expect.status,
                kind: K.kind,
                entityType: K.entityType,
                entityId: id,
            }).catch(function () { return 'kept'; });
            return { ok: true };
        }

        /** Explicit discard after confirmation; never a draft some editor is extending now. */
        async function discardNow(ctx, workId, token, expect) {
            const r = recovery();
            const id = String(workId || '');
            if (!r || !targetFor(ctx, id, token)) return { ok: false, code: 'stale' };
            const rt = r.rt;
            const record = await rt.store.get(expect && expect.draftId);
            if (!matches(record, expect, id)) return { ok: false, code: 'changed' };
            const entry = K.entry(ctx, id);
            const lineage = await rt.classify(record, entry && entry.recovery ? entry.recovery.sessionKey : null);
            if (lineage === 'self-live' || lineage === 'other-live') return { ok: false, code: 'changed' };
            if (!targetFor(ctx, id, token)) return { ok: false, code: 'stale' };
            const outcome = await rt.discardReviewed({
                draftId: expect.draftId,
                pageInstanceId: expect.pageInstanceId,
                generation: expect.generation,
                status: expect.status,
                kind: K.kind,
                entityType: K.entityType,
                entityId: id,
            });
            return outcome === 'deleted' ? { ok: true } : { ok: false, code: 'changed' };
        }

        /* Review work runs one at a time with restores, then refreshes this pane's notice. */
        function run(ctx, workId, fn) {
            const out = chain.then(fn).catch(function () {
                return { ok: false, code: 'failed' };
            }).then(async function (result) {
                const live = ctx && ctx.getEntity ? ctx.getEntity(K.entityType) : null;
                if (live && String(live.id) === String(workId || '')) {
                    await restoreNow(ctx, live, null, { reviewOnly: true }).catch(function () {});
                }
                return result;
            });
            chain = out.catch(function () { return null; });
            return out;
        }

        function refresh(ctx, workId) {
            return run(ctx, workId, function () { return null; });
        }

        const adapter = {
            kind: K.kind,
            entityType: K.entityType,
            observedBase: observedBase,
            draftBase: draftBase,
            pipeline: pipeline,
            stateOf: stateOf,
            edit: edit,
            release: release,
            forgetDeleted: forgetDeleted,
            flush: flush,
            queued: queued,
            settled: settled,
            publishFor: publishFor,
            restore: restore,
            view: view,
            details: function (ctx, workId) {
                const out = chain.then(function () {
                    return detailsNow(ctx, workId);
                }).catch(function () { return null; });
                chain = out.catch(function () { return null; });
                return out;
            },
            restoreReviewed: function (ctx, workId, token, expect) {
                return run(ctx, workId, function () {
                    return restoreReviewedNow(ctx, workId, token, expect);
                });
            },
            replace: function (ctx, workId, token, expect, text, shown) {
                return run(ctx, workId, function () {
                    return replaceNow(ctx, workId, token, expect, text, shown);
                });
            },
            discard: function (ctx, workId, token, expect) {
                return run(ctx, workId, function () {
                    return discardNow(ctx, workId, token, expect);
                });
            },
            refresh: refresh,
            setRestoreMsForTest: function (ms) {
                restoreMs = Number.isFinite(ms) && ms > 0 ? ms : RESTORE_MS;
            },
            /* Test reset: every adopted watch and page-level listener goes. */
            resetForTest: function () {
                ackWatch.forEach(function (watches) {
                    watches.forEach(function (item) {
                        if (item.writer) void item.writer.release().catch(function () {});
                    });
                });
                ackWatch.clear();
                if (stopSync) stopSync();
                stopSync = null;
                if (stopWriterEvents) stopWriterEvents();
                stopWriterEvents = null;
                if (stopClosedPages) stopClosedPages();
                stopClosedPages = null;
                chain = Promise.resolve(null);
            },
        };
        adapters.push(adapter);
        return adapter;
    }

    root.prksCreateWorkNoteRecovery = create;
    /* #533: release every kind's sessions of a Work whose deletion is
     * confirmed; resolves to { retained }, the panes still mounting a note
     * editor of the Work, which may yet type in it (an adapter that failed
     * counts as one). */
    root.prksForgetDeletedWorkNotes = function (workId, options) {
        const evenShown = !!(options && options.evenShown);
        return Promise.all(adapters.filter(function (adapter) {
            return adapter.entityType === 'work';
        }).map(function (adapter) {
            try {
                return adapter.forgetDeleted(workId, evenShown).catch(function () { return 1; });
            } catch (_e) {
                return 1;
            }
        })).then(function (counts) {
            return { retained: counts.reduce(function (sum, count) { return sum + count; }, 0) };
        });
    };
})(typeof window === 'undefined' ? globalThis : window);
