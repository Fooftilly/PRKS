/**
 * Core work detail UI: research notes, wiki links, BibTeX, metadata.
 * PDF / EmbedPDF integration is in works-pdf.js (loaded via dynamic import when work.file_path is set).
 */

function prksEscapeHtmlLite(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function prksBuildWorkTitleLowerToIdMap(works) {
    const map = {};
    const sorted = [...(works || [])].sort((a, b) => String(a.id).localeCompare(String(b.id)));
    for (const w of sorted) {
        const k = (w.title || '').trim().toLowerCase();
        if (k && map[k] === undefined) map[k] = w.id;
    }
    return map;
}

/** Sorted list of { id, title } for [[…]] autocomplete in the research notes editor. */
function prksBuildWikiAutocompleteWorkList(works) {
    const rows = (works || [])
        .map((w) => ({ id: w.id, title: String(w.title || '').trim() }))
        .filter((w) => w.title);
    rows.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
    return rows;
}

const PRKS_WIKI_HINT_MAX = 50;
const PRKS_RESEARCH_DRAFT_MAX_COMMITTED = 64;
const prksWorkResearchDrafts = new Map();

function prksResearchDraftKey(ctx, workId) {
    const tab = ctx && ctx.tabId != null ? String(ctx.tabId) : '';
    return tab + '\0' + String(workId || '');
}

function prksResearchDraftEntry(ctx, workId, text) {
    const id = String(workId || '');
    if (!id) return null;
    const key = prksResearchDraftKey(ctx, id);
    let entry = prksWorkResearchDrafts.get(key);
    if (!entry) {
        entry = {
            key: key,
            workId: id,
            ownerTabId: ctx && ctx.tabId != null ? String(ctx.tabId) : '',
            text: String(text == null ? '' : text),
            editGeneration: 0,
            saveSequence: 0,
            latestSaveToken: 0,
            latestSaveEditGeneration: 0,
            settledSaveToken: 0,
            state: 'committed',
            saveError: false,
            promise: null,
            updatedAt: Date.now(),
        };
        prksWorkResearchDrafts.set(key, entry);
    }
    if (ctx && ctx.ui && String(entry.ownerTabId) === String(ctx.tabId == null ? '' : ctx.tabId)) {
        ctx.ui.workResearchNoteSession = entry;
    }
    return entry;
}

function prksPruneResearchDrafts() {
    const committed = Array.from(prksWorkResearchDrafts.values())
        .filter((entry) => entry.state === 'committed' && !entry.promise)
        .sort((a, b) => a.updatedAt - b.updatedAt);
    while (committed.length > PRKS_RESEARCH_DRAFT_MAX_COMMITTED) {
        const old = committed.shift();
        if (old) {
            prksWorkResearchDrafts.delete(old.key);
            prksResearchRecoveryRelease(old);
        }
    }
}

function prksResearchNotesTextForWork(workId, serverText, ctx) {
    const id = String(workId || '');
    const server = String(serverText == null ? '' : serverText);
    const acknowledged = (typeof prksPendingWorkNoteText === 'function')
        ? prksPendingWorkNoteText(id, 'work-research-note', server)
        : server;
    const entry = ctx && id ? prksWorkResearchDrafts.get(prksResearchDraftKey(ctx, id)) : null;
    if (!entry || String(entry.ownerTabId) !== String(ctx && ctx.tabId != null ? ctx.tabId : '')) {
        return acknowledged;
    }
    if (entry.state === 'committed' && !entry.promise && entry.text === acknowledged) {
        prksWorkResearchDrafts.delete(entry.key);
        prksResearchRecoveryRelease(entry);
        if (ctx.ui && ctx.ui.workResearchNoteSession === entry) ctx.ui.workResearchNoteSession = null;
        return acknowledged;
    }
    return entry.text;
}

function prksResearchNotesMayPaint(owner, workId, generation) {
    if (!owner || owner.destroyed) return false;
    if (typeof owner.isCurrent === 'function' && !owner.isCurrent(generation)) return false;
    const live = owner.getEntity ? owner.getEntity('work') : null;
    return !!(live && String(live.id) === String(workId || ''));
}

function prksSyncResearchNotesState(notes, entry) {
    if (!notes || !entry) return;
    notes.editGeneration = entry.editGeneration;
    notes.saveSequence = entry.saveSequence;
    notes.latestSaveToken = entry.latestSaveToken;
    notes.latestSaveEditGeneration = entry.latestSaveEditGeneration;
    notes.settledSaveToken = entry.settledSaveToken;
    notes.pendingSave = entry.latestSaveToken > entry.settledSaveToken;
    notes.drafting = entry.state === 'drafting' || entry.state === 'blocked';
    notes.saveError = entry.state === 'error';
}

function prksLiveResearchDraftStatus(entry, result) {
    if (entry && entry.state === 'drafting') return 'Drafting...';
    if (entry && entry.state === 'saving') return 'Saving...';
    if (entry && entry.state === 'blocked') return prksResearchNotesStatusForResult('scope_busy', false);
    if (result && result.code) {
        return prksResearchNotesStatusForResult(result.code, !!result.pending);
    }
    return 'Error saving changes';
}

/**
 * Status for one sync event on this owner's Research Notes (#465). An
 * acknowledgement says the server holds THAT operation's body. It reads as
 * "All changes saved" only when that body is what the editor shows and no
 * newer session revision (drafting, saving, or blocked behind it) remains.
 * Otherwise the save path owns the status, so this returns null.
 */
function prksResearchNotesSyncEventStatus(ctx, notes, event) {
    if (!event || !notes) return null;
    if (!event.acknowledged) {
        return event.op && event.op.status === 'conflict'
            ? 'This note needs a decision in Diagnostics'
            : null;
    }
    return prksResearchNotesAckIsCurrent(ctx, notes, event.op) ? 'All changes saved' : null;
}

/** The acknowledged body is the editor body and the session holds nothing newer. */
function prksResearchNotesAckIsCurrent(ctx, notes, op) {
    const payload = op && op.payload;
    if (!payload || typeof payload.text !== 'string') return false;
    const editor = notes.editor;
    if (!editor || typeof editor.value !== 'function' || editor.value() !== payload.text) return false;
    const entry = prksWorkResearchDrafts.get(prksResearchDraftKey(ctx, notes.workId));
    return !entry || (entry.state === 'committed' && entry.text === payload.text);
}

function prksSyncLiveResearchDraft(workId, entry, generation, result) {
    if (!entry || typeof prksForEachLiveTabContext !== 'function') return;
    prksForEachLiveTabContext(function (ctx) {
        if (entry.ownerTabId && String(ctx.tabId) !== String(entry.ownerTabId)) return;
        const work = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
        if (!work || String(work.id) !== String(workId)) return;
        const notes = ctx.getResource ? ctx.getResource('workNotes') : null;
        /* Latest-token settlement still reaches a same-Work refresh. Direct
         * status paint from the old editor stays generation-fenced. */
        prksSyncResearchNotesState(notes, entry);
        const generationMoved = typeof generation === 'number' && ctx.generation !== generation;
        if (generationMoved && !notes) return;
        const status = ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
        if (status) status.innerText = prksLiveResearchDraftStatus(entry, result);
        if (ctx.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
            window.prksWorkspaceRefreshTabStatus(ctx.tabId);
        }
    });
}

/*
 * Browser-local recovery of Research Notes text (#466 slice 2).
 *
 * Every edit reports the session's newest generation and body to one
 * recovery writer per session entry (`entry.recovery`), independently of the
 * 2 s semantic-save debounce. The writer belongs to `prksEditorRecovery`'s
 * page registry, not to TabContext timers, so a warm park, cold release or
 * remount keeps the lineage; it is released only when the session entry
 * itself is dropped. A recovery commit means "recoverable on this device",
 * never "saved": no status here reads as saved because of it.
 *
 * `entry.recoveryBase` is the acknowledged body this session's text was typed
 * on. It is set from the observed base when a lineage starts and advanced
 * only by the acknowledgement of this session's own queued operation, never
 * by another pane's or tab's, so a foreign edit is never silently treated as
 * the base. The record is cleared only by the exact acknowledged generation
 * and body, or when a save proves the body equals the acknowledged note.
 */
const PRKS_RESEARCH_RECOVERY_KIND = 'work-research-note';
const PRKS_RESEARCH_NOTES_RESTORED_STATUS = 'Restored unsaved changes';
/* Represented drafts (already the exact body of a queued row): op id -> list,
 * cleared on that row's acknowledgement. One from this pane before reload is
 * adopted (`writer`, `key`): the pane's next edit continues that lineage, so
 * a later save that replaces the row still ends in an exact clear. */
const prksResearchRecoveryAckWatch = new Map();
/* A restore that has not finished by then is abandoned and the editor opens
 * as it would without recovery; nothing it reads later is applied. */
const PRKS_RESEARCH_RECOVERY_RESTORE_MS = 5000;
let prksResearchRecoveryRestoreMs = PRKS_RESEARCH_RECOVERY_RESTORE_MS;
const prksResearchRecoveryPrints = [];
let prksResearchRecoveryStopSync = null;
let prksResearchRecoveryChain = Promise.resolve();

function prksResearchRecovery() {
    const api = window.prksEditorRecovery;
    if (!api || typeof api.runtime !== 'function' || typeof api.planResearchNotesRestore !== 'function') return null;
    try {
        return { api: api, rt: api.runtime() };
    } catch (_e) {
        return null;
    }
}

/* A note body is fingerprinted once per base or own save, never per keystroke. */
function prksResearchRecoveryPrint(api, text) {
    for (const item of prksResearchRecoveryPrints) {
        if (item.text === text) return item.print;
    }
    const print = api.fingerprintText(text);
    prksResearchRecoveryPrints.unshift({ text: text, print: print });
    if (prksResearchRecoveryPrints.length > 4) prksResearchRecoveryPrints.pop();
    return print;
}

function prksResearchRecoveryIdentity(api, base) {
    if (!base || typeof base.value !== 'string' || !Number.isSafeInteger(base.revision)) return null;
    return { revision: base.revision, length: base.value.length, fingerprint: prksResearchRecoveryPrint(api, base.value) };
}

function prksResearchRecoveryDraftBase(api, base) {
    const identity = prksResearchRecoveryIdentity(api, base);
    if (!identity) return Object.assign({}, api.UNKNOWN_BASE);
    const source = base.source === 'cache' || base.source === 'pending-create' ? base.source : 'server';
    return Object.assign(identity, { source: source });
}

function prksResearchRecoveryObservedBase(owner) {
    const slot = typeof prksWorkNoteObserved === 'function' ? prksWorkNoteObserved(owner, PRKS_RESEARCH_RECOVERY_KIND) : null;
    if (!slot || typeof slot.value !== 'string' || !Number.isSafeInteger(slot.revision)) return null;
    return { value: slot.value, revision: slot.revision, source: slot.source || 'server' };
}

/* Pipeline state for the stored record: informational, never authoritative. */
function prksResearchRecoveryPipeline(entry, patch) {
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

function prksResearchRecoveryStateOf(entry) {
    if (!entry) return 'drafting';
    if (entry.state === 'committed') {
        if (!entry.recoveryQueued) return 'drafting';
        return entry.recoveryConflictOpId === entry.recoveryQueued.opId ? 'conflict' : 'queued';
    }
    return entry.state === 'saving' || entry.state === 'blocked' || entry.state === 'error' ? entry.state : 'drafting';
}

/** Reports the session's newest body to its recovery writer (one lineage per session). */
function prksResearchRecoveryEdit(owner, entry) {
    const recovery = prksResearchRecovery();
    if (!recovery || !entry) return;
    try {
        void recovery.rt.start().catch(function () {});
        prksResearchRecoveryListen();
        let writer = entry.recovery;
        if (!writer) writer = prksResearchRecoveryTakeWatched(entry);
        if (!writer) {
            writer = recovery.rt.writers.openWriter({
                kind: PRKS_RESEARCH_RECOVERY_KIND,
                entityType: 'work',
                entityId: entry.workId,
                paneId: entry.ownerTabId,
            });
            entry.recovery = writer;
        }
        if (writer.state() === 'clean') {
            /* A fresh lineage: typed on the acknowledged body this pane observes now. */
            entry.recoveryBase = prksResearchRecoveryObservedBase(owner);
            entry.recoveryQueued = null;
            entry.recoveryPipeline = null;
            entry.recoveryPipelineStored = false;
            writer.setBase(prksResearchRecoveryDraftBase(recovery.api, entry.recoveryBase));
        }
        writer.edit(entry.editGeneration, entry.text);
        prksResearchRecoveryPipeline(entry, { state: 'drafting' });
    } catch (_e) { /* recovery is best-effort beside the save path */ }
}

/** The represented lineage this pane adopted on mount becomes the session's lineage. */
function prksResearchRecoveryTakeWatched(entry) {
    let taken = null;
    prksResearchRecoveryAckWatch.forEach(function (watches, opId) {
        const item = taken ? null : watches.find(function (w) { return w.writer && w.key === entry.key; });
        if (!item) return;
        watches.splice(watches.indexOf(item), 1);
        if (!watches.length) prksResearchRecoveryAckWatch.delete(opId);
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
function prksResearchRecoveryRelease(entry) {
    const writer = entry && entry.recovery;
    if (!writer) return;
    entry.recovery = null;
    void writer.release().catch(function () {});
}

/** Starts the recovery write before an ordinary save is queued. */
function prksResearchRecoveryFlush(entry) {
    const writer = entry && entry.recovery;
    if (writer) void writer.flush().catch(function () {});
}

/**
 * This session queued `text` (generation `generation`) as row `opId`, from
 * `base`. Recorded with the #475 provenance, whether or not a newer save has
 * taken over the session since: it is what a newer body was typed on.
 */
function prksResearchRecoveryQueued(entry, opId, generation, text, base) {
    const recovery = prksResearchRecovery();
    if (!recovery || !entry || !entry.recovery || !opId) return;
    try {
        entry.recoveryQueued = { opId: opId, generation: generation, text: text };
        prksResearchRecoveryPipeline(entry, {
            queuedOpId: opId,
            queuedGeneration: generation,
            ownQueued: {
                opId: opId,
                textLength: text.length,
                textFingerprint: prksResearchRecoveryPrint(recovery.api, text),
                base: prksResearchRecoveryIdentity(recovery.api, base) || { revision: null, length: null, fingerprint: null },
            },
        });
    } catch (_e) { /* recovery is best-effort beside the save path */ }
}

/** A save settled: record its pipeline outcome; clear only what the server provably holds. */
function prksResearchRecoverySettled(owner, id, entry, result, saved) {
    const recovery = prksResearchRecovery();
    if (!recovery || !entry || !entry.recovery) return;
    try {
        const code = result && result.code;
        const patch = { state: prksResearchRecoveryStateOf(entry) };
        if (entry.state === 'blocked') {
            patch.blockedBase = prksResearchRecoveryIdentity(recovery.api, entry.blockedBase);
        }
        prksResearchRecoveryPipeline(entry, patch);
        /* Nothing queued and the body is the acknowledged note (A -> B -> A,
         * or a save of an unchanged body): proven equal, so clear it. */
        if (code === 'saved' && saved && !result.opId && entry.state === 'committed' &&
            entry.editGeneration === saved.generation && prksResearchNotesMayPaint(owner, id)) {
            const slot = prksResearchRecoveryObservedBase(owner);
            if (slot && slot.source === 'server' && slot.value === saved.text) {
                void entry.recovery.acknowledged(saved.generation, saved.text).catch(function () {});
            }
        }
    } catch (_e) { /* recovery is best-effort beside the save path */ }
}

/** One page-level subscription: acknowledgements and conflicts of queued Research Notes rows. */
function prksResearchRecoveryListen() {
    if (prksResearchRecoveryStopSync || !window.prksSync || typeof window.prksSync.subscribe !== 'function') return;
    prksResearchRecoveryStopSync = window.prksSync.subscribe(prksResearchRecoveryOnSync);
}

function prksResearchRecoveryOnSync(event) {
    const recovery = prksResearchRecovery();
    if (!recovery) return;
    const op = event && event.op;
    if (event && event.acknowledged && op && op.operation === 'SET_WORK_RESEARCH_NOTE') {
        const text = op.payload && typeof op.payload.text === 'string' ? op.payload.text : null;
        const rev = event.acknowledged.server_revision;
        if (text === null || !Number.isSafeInteger(rev)) return;
        const watches = prksResearchRecoveryAckWatch.get(op.op_id) || [];
        prksResearchRecoveryAckWatch.delete(op.op_id);
        watches.forEach(function (watched) {
            watched.acknowledged = true;
            /* An adoption still in flight settles first, so the writer it
             * yields clears the lineage it now owns. */
            void Promise.resolve(watched.adopting).then(function () {
                const writer = watched.writer;
                const clear = watched.text !== text ? Promise.resolve()
                    : writer ? writer.acknowledged(watched.generation, text)
                        /* Only while the page it observed still owns it: never under a writer that adopted it since. */
                        : recovery.rt.store.deleteIfAcknowledged(watched.draftId, watched.generation, text, watched.pageInstanceId);
                return clear.catch(function () {}).then(function () {
                    if (writer) return writer.release();
                    return undefined;
                });
            }).catch(function () {});
        });
        prksWorkResearchDrafts.forEach(function (entry) {
            const queued = entry.recoveryQueued;
            if (!entry.recovery || entry.workId !== op.entity_id || !queued) return;
            if (queued.opId !== op.op_id || queued.text !== text) return;
            /* This session's own operation: its text is now the base the
             * session's newer text was typed on. */
            entry.recoveryQueued = null;
            entry.recoveryBase = { value: text, revision: rev, source: 'server' };
            try {
                entry.recovery.setBase(prksResearchRecoveryDraftBase(recovery.api, entry.recoveryBase));
                prksResearchRecoveryPipeline(entry, {
                    state: prksResearchRecoveryStateOf(entry), queuedOpId: null, queuedGeneration: 0,
                });
                void entry.recovery.acknowledged(queued.generation, text).catch(function () {});
            } catch (_e) { /* best-effort */ }
        });
        return;
    }
    if (!Array.from(prksWorkResearchDrafts.values()).some(function (entry) { return entry.recovery && entry.recoveryQueued; })) return;
    if (typeof prksRefreshPendingWorkNotes !== 'function') return;
    void prksRefreshPendingWorkNotes().then(function (rows) {
        const conflicted = new Set((rows || []).filter(function (row) {
            return row && row.status === 'conflict';
        }).map(function (row) { return row.op_id; }));
        prksWorkResearchDrafts.forEach(function (entry) {
            if (entry.recovery && entry.recoveryQueued && conflicted.has(entry.recoveryQueued.opId)) {
                entry.recoveryConflictOpId = entry.recoveryQueued.opId;
                prksResearchRecoveryPipeline(entry, { state: prksResearchRecoveryStateOf(entry) });
            }
        });
    }).catch(function () {});
}

/**
 * Same-pane restore on Research Notes mount, between `ensureBase` and the
 * first read of the session text. Applies `planResearchNotesRestore`:
 * restores only this pane's own draft from before reload when that cannot
 * overwrite anything; drafts proven equal to the server note are cleared;
 * everything else is kept untouched, never enqueued, and reported on
 * `ctx.ui.researchNotesRecovery` for slice 3. Restores run one at a time.
 */
function prksRestoreResearchNotesRecovery(ctx, work) {
    const attempt = { abandoned: false };
    let timer = null;
    const timeout = new Promise(function (resolve) {
        timer = setTimeout(function () {
            attempt.abandoned = true;
            resolve(null);
        }, prksResearchRecoveryRestoreMs);
    });
    const run = prksResearchRecoveryChain.then(function () {
        return attempt.abandoned ? null : prksRestoreResearchNotesRecoveryNow(ctx, work, attempt);
    }).catch(function () { return null; });
    /* An abandoned run that never settles must not hold up later restores. */
    const settled = Promise.race([run, timeout]);
    prksResearchRecoveryChain = settled;
    return settled.then(function (result) {
        clearTimeout(timer);
        return result;
    });
}

async function prksRestoreResearchNotesRecoveryNow(ctx, work, attempt) {
    const recovery = prksResearchRecovery();
    if (!recovery || !ctx || !work || work.id == null) return null;
    const id = String(work.id);
    const generation = ctx.generation;
    const key = prksResearchDraftKey(ctx, id);
    const current = function () {
        return !(attempt && attempt.abandoned) && prksResearchNotesMayPaint(ctx, id, generation) &&
            !prksWorkResearchDrafts.has(key);
    };
    if (ctx.ui) ctx.ui.researchNotesRecovery = null;
    /* A live session in this pane is the authority for its text. */
    if (!current()) return null;
    const rt = recovery.rt;
    prksResearchRecoveryListen();
    await rt.scanEmergency();
    const records = await rt.store.listByEntity(PRKS_RESEARCH_RECOVERY_KIND, id);
    if (!records.length || !current()) return null;
    const candidates = [];
    for (const record of records) {
        const lineage = await rt.classify(record, null);
        let body = null;
        if (lineage !== 'self-live' && lineage !== 'other-live') {
            const row = await rt.store.getBody(record.draftId);
            body = row && row.generation === record.generation ? row.body : null;
        }
        candidates.push({ record: record, body: body, lineage: lineage });
    }
    const rows = typeof prksReadPendingWorkNotesSnapshot === 'function' ? await prksReadPendingWorkNotesSnapshot() : null;
    if (!current()) return null;
    /* An unread queue is unknown, never empty. */
    const queue = rows && typeof prksWorkNoteOperations === 'function'
        ? prksWorkNoteOperations(rows, id, PRKS_RESEARCH_RECOVERY_KIND).map(function (row) {
            return { opId: row.op_id, text: row.payload && typeof row.payload.text === 'string' ? row.payload.text : '' };
        })
        : null;
    const base = prksResearchRecoveryObservedBase(ctx);
    const otherDirty = Array.from(prksWorkResearchDrafts.values()).some(function (entry) {
        return entry.workId === id && entry.key !== key && entry.state !== 'committed';
    });
    const planWith = function (planBase) {
        return recovery.api.planResearchNotesRestore({
            paneId: String(ctx.tabId == null ? '' : ctx.tabId),
            candidates: candidates,
            base: planBase,
            queue: queue,
            otherDirtySession: otherDirty,
        });
    };
    let plan = planWith(base);
    /* The body and the revision were read separately. Before anything is
     * cleared or restored on them, prove they are one server snapshot;
     * otherwise every draft stays for review. */
    if ((plan.cleanup.length || plan.restore) && !(await prksResearchRecoveryVerifyBase(id, base))) {
        if (!current()) return null;
        plan = planWith(Object.assign({}, base, { source: 'cache' }));
    }
    if (!current()) return null;
    for (const draftId of plan.cleanup) {
        const record = candidates.find(function (c) { return c.record.draftId === draftId; }).record;
        /* Only the generation it read, and only while no page has adopted it since. */
        void rt.store.deleteIfAcknowledged(draftId, record.generation, base.value, record.owner.pageInstanceId)
            .catch(function () {});
    }
    const paneId = String(ctx.tabId == null ? '' : ctx.tabId);
    for (const item of plan.represented) {
        const candidate = candidates.find(function (c) { return c.record.draftId === item.draftId; });
        const watches = prksResearchRecoveryAckWatch.get(item.opId) || [];
        if (watches.some(function (w) { return w.draftId === item.draftId; })) continue;
        const watch = Object.assign({ writer: null, adopting: null, acknowledged: false, key: key, base: base, pipeline: candidate.record.pipeline }, item);
        /* Every lineage the row represents is cleared by its acknowledgement,
         * so it is watched before adopting: an acknowledgement that lands
         * while the adoption is pending still finds it. */
        watches.push(watch);
        prksResearchRecoveryAckWatch.set(item.opId, watches);
        if (candidate.lineage === 'same-runtime-orphan' && candidate.record.owner.paneId === paneId) {
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
    if (plan.restore) {
        const writer = await rt.writers.adopt(plan.restore.record, { paneId: paneId });
        /* The plan was made before adopting. Another pane may have saved,
         * queued or started editing meanwhile; then the draft stays unapplied
         * for review rather than being saved over a base it was not typed on. */
        const changed = writer && current() ? await prksResearchRecoveryChangedSince(ctx, id, key, base, queue) : null;
        if (writer && (!current() || changed)) {
            /* Stale mount or moved on: the lineage stays this pane's orphan. */
            void writer.release().catch(function () {});
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
                });
            }
        } else if (writer) {
            prksResearchRecoveryInstall(recovery.api, ctx, id, writer, plan.restore, base);
            restored = true;
        }
    }
    if (ctx.ui && plan.review.length) {
        ctx.ui.researchNotesRecovery = { status: 'needs-review', workId: id, candidates: plan.review };
    }
    return { restored: restored, review: plan.review };
}

/**
 * Whether the server holds `base.value` at `base.revision`. The revision was
 * read after the body, so the body is read again and then the revision: when
 * that revision still equals `base.revision`, the body read between the two
 * is the note at that revision. Any failed or cached read is unverified.
 */
async function prksResearchRecoveryVerifyBase(id, base) {
    if (!base || base.source !== 'server' || typeof prksOfflineReadEntity !== 'function' ||
        typeof prksReadWorkNotesState !== 'function') {
        return false;
    }
    try {
        const work = await prksOfflineReadEntity('work', id, '/api/works/' + encodeURIComponent(id), {});
        if (!work || work.source !== 'server' || !work.value) return false;
        const body = typeof work.value.text_content === 'string' ? work.value.text_content : '';
        if (body !== base.value) return false;
        const state = await prksReadWorkNotesState(id);
        return !!(state && state.source === 'server' && state.value &&
            state.value.research_note_revision === base.revision);
    } catch (_e) {
        return false;
    }
}

/**
 * Why a restore planned on `base` and `queue` is no longer safe to apply, or
 * null. The queue is re-read fail-closed; everything after that read is
 * synchronous up to the install.
 */
async function prksResearchRecoveryChangedSince(ctx, id, key, base, queue) {
    const rows = typeof prksReadPendingWorkNotesSnapshot === 'function' ? await prksReadPendingWorkNotesSnapshot() : null;
    if (!rows || typeof prksWorkNoteOperations !== 'function') return 'queue-unknown';
    const observed = prksResearchRecoveryObservedBase(ctx);
    if (!observed || observed.value !== base.value || observed.revision !== base.revision ||
        observed.source !== base.source) {
        return 'base-advanced';
    }
    const now = prksWorkNoteOperations(rows, id, PRKS_RESEARCH_RECOVERY_KIND);
    const sameQueue = now.length === queue.length && now.every(function (row, i) {
        return row.op_id === queue[i].opId && row.payload && row.payload.text === queue[i].text;
    });
    if (!sameQueue) return 'foreign-queue';
    const otherDirty = Array.from(prksWorkResearchDrafts.values()).some(function (entry) {
        return entry.workId === id && entry.key !== key && entry.state !== 'committed';
    });
    return otherDirty ? 'dirty-session' : null;
}

function prksResearchRecoveryInstall(api, ctx, id, writer, restore, base) {
    const entry = prksResearchDraftEntry(ctx, id, restore.body);
    entry.text = restore.body;
    entry.editGeneration = restore.record.generation;
    entry.state = restore.state;
    entry.saveError = false;
    entry.updatedAt = Date.now();
    entry.recovery = writer;
    entry.recoveryBase = base;
    entry.recoveryRestored = true;
    entry.recoverySaveDue = restore.state === 'drafting';
    entry.recoveryPipeline = restore.record.pipeline;
    entry.recoveryPipelineStored = true;
    entry.recoveryQueued = null;
    const acknowledged = { value: base.value, revision: base.revision };
    if (restore.predecessor) {
        /* #475 provenance after reload: the queued predecessor's exact text,
         * queued from the base the server still holds. */
        entry.ownQueuedToken = 0;
        entry.ownQueuedText = restore.predecessor.text;
        entry.ownQueuedBase = acknowledged;
        entry.recoveryQueued = { opId: restore.predecessor.opId, generation: 0, text: restore.predecessor.text };
    }
    if (restore.state === 'blocked') entry.blockedBase = acknowledged;
    writer.setBase(prksResearchRecoveryDraftBase(api, base));
    prksResearchRecoveryPipeline(entry, { state: restore.state });
}

window.prksRestoreResearchNotesRecovery = prksRestoreResearchNotesRecovery;
window.prksResearchNotesTextForWork = prksResearchNotesTextForWork;
window.prksResearchNotesMayPaint = prksResearchNotesMayPaint;
window.prksResearchNotesSyncEventStatus = prksResearchNotesSyncEventStatus;
window.prksScheduleResearchNotesBusyRetryForTest = prksScheduleResearchNotesBusyRetry;
window.prksSetResearchRecoveryRestoreMsForTest = function (ms) {
    prksResearchRecoveryRestoreMs = Number.isFinite(ms) && ms > 0 ? ms : PRKS_RESEARCH_RECOVERY_RESTORE_MS;
};
window.prksResetResearchDraftsForTest = function () {
    prksWorkResearchDrafts.forEach(prksResearchRecoveryRelease);
    prksWorkResearchDrafts.clear();
    prksResearchRecoveryAckWatch.forEach(function (watches) {
        watches.forEach(function (item) {
            if (item.writer) void item.writer.release().catch(function () {});
        });
    });
    prksResearchRecoveryAckWatch.clear();
    if (prksResearchRecoveryStopSync) prksResearchRecoveryStopSync();
    prksResearchRecoveryStopSync = null;
    prksResearchRecoveryChain = Promise.resolve(null);
    if (typeof prksForEachLiveTabContext === 'function') {
        prksForEachLiveTabContext(function (ctx) {
            if (ctx && ctx.ui) ctx.ui.workResearchNoteSession = null;
        });
    }
};

/** EasyMDE does not set window.CodeMirror; show-hint registers on the CDN global. Copy hint APIs onto the editor's bundled CodeMirror constructor. */
function prksEnsureEasyMDECodeMirrorHints(cm) {
    const internal = cm && cm.constructor;
    const globalCM = typeof window !== 'undefined' ? window.CodeMirror : undefined;
    if (!internal) return false;
    if (typeof internal.showHint === 'function' && typeof internal.prototype.showHint === 'function') {
        return true;
    }
    if (!globalCM || typeof globalCM.showHint !== 'function') return false;
    if (globalCM.prototype.showHint && !internal.prototype.showHint) {
        internal.prototype.showHint = globalCM.prototype.showHint;
    }
    if (globalCM.prototype.closeHint && !internal.prototype.closeHint) {
        internal.prototype.closeHint = globalCM.prototype.closeHint;
    }
    if (globalCM.showHint && !internal.showHint) {
        internal.showHint = globalCM.showHint;
    }
    return typeof internal.showHint === 'function' && typeof internal.prototype.showHint === 'function';
}

function prksGetWikiLinkAutocompleteContext(cm) {
    const cur = cm.getCursor();
    const lineText = cm.getLine(cur.line);
    const before = lineText.slice(0, cur.ch);
    // Anchor on the last '[[' and reject a query holding ']' or '|'. Same
    // acceptance as the old /\[\[([^\]|]*)$/ scan (an earlier opener can only
    // be clean when the last one is) but linear instead of super-linear, and
    // read off the very opener `from` points at -- the regex reported the
    // leftmost opener while `from` has always used the last one, so a nested
    // '[[a[[b' queried 'a[[b' while offering to replace only 'b'.
    const openAt = before.lastIndexOf('[[');
    if (openAt < 0) return null;
    const startCh = openAt + 2;
    const query = before.slice(startCh);
    if (/[\]|]/.test(query)) return null;
    if (/^(pdf:|concept:|argument:)/i.test(query)) return null;
    const CM = cm.constructor;
    const from = CM.Pos(cur.line, startCh);
    const to = cur;
    return { from, to, query };
}

function prksFilterWorksForWikiHint(rows, query) {
    const ql = query.trim().toLowerCase();
    if (!ql) return rows.slice(0, PRKS_WIKI_HINT_MAX);
    const pref = [];
    const sub = [];
    for (const w of rows) {
        const tl = w.title.toLowerCase();
        if (tl.startsWith(ql)) pref.push(w);
        else if (tl.includes(ql)) sub.push(w);
        if (pref.length >= PRKS_WIKI_HINT_MAX) break;
    }
    if (pref.length >= PRKS_WIKI_HINT_MAX) return pref.slice(0, PRKS_WIKI_HINT_MAX);
    const need = PRKS_WIKI_HINT_MAX - pref.length;
    return pref.concat(sub.slice(0, need));
}

/** CodeMirror hint pick: replace query with title and close wiki link. */
function prksWikiLinkCompletionPick(cm, data, completion) {
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) return;
    const from = completion.from != null ? completion.from : data.from;
    const to = completion.to != null ? completion.to : data.to;
    const title = typeof completion.text === 'string' ? completion.text : '';
    cm.replaceRange(title + ']]', from, to, 'complete');
}

function prksHintOwnerCtx(cm) {
    if (cm && cm.__prksOwnerCtx) return cm.__prksOwnerCtx;
    return typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
}

function prksHintResource(cm, name) {
    const owner = prksHintOwnerCtx(cm);
    return owner && typeof owner.getResource === 'function' ? owner.getResource(name) : undefined;
}

function prksWikiLinkHint(cm) {
    const ctx = prksGetWikiLinkAutocompleteContext(cm);
    if (!ctx) return null;
    const rows = prksHintResource(cm, 'wikiWorkList') || [];
    const matches = prksFilterWorksForWikiHint(rows, ctx.query);
    if (matches.length === 0) return null;
    return {
        from: ctx.from,
        to: ctx.to,
        list: matches.map((w) => ({
            text: w.title,
            displayText: w.title,
            hint: prksWikiLinkCompletionPick,
        })),
    };
}

function prksAttachWikiLinkAutocomplete(cm, ownerCtx) {
    if (cm && ownerCtx) cm.__prksOwnerCtx = ownerCtx;
    const hintPatched = cm ? prksEnsureEasyMDECodeMirrorHints(cm) : false;
    const CM = cm && cm.constructor;
    if (!cm || !hintPatched || typeof CM.showHint !== 'function') {
        return;
    }

    cm.on('inputRead', function (editor, change) {
        if (!change) return;
        if (change.origin === 'setValue' || change.origin === 'complete') return;
        requestAnimationFrame(function () {
            if (prksGetPdfAnnLinkAutocompleteContext(editor)) {
                CM.showHint(editor, prksPdfAnnLinkHint, { completeSingle: false });
                return;
            }
            if (prksGetConceptLinkAutocompleteContext(editor)) {
                CM.showHint(editor, prksConceptLinkHint, { completeSingle: false });
                return;
            }
            if (!prksGetWikiLinkAutocompleteContext(editor)) return;
            CM.showHint(editor, prksWikiLinkHint, { completeSingle: false });
        });
    });

    const keys = cm.getOption('extraKeys') || {};
    const openWikiHint = function (editor) {
        if (prksGetPdfAnnLinkAutocompleteContext(editor)) {
            CM.showHint(editor, prksPdfAnnLinkHint, { completeSingle: false });
            return;
        }
        if (prksGetConceptLinkAutocompleteContext(editor)) {
            CM.showHint(editor, prksConceptLinkHint, { completeSingle: false });
            return;
        }
        if (prksGetWikiLinkAutocompleteContext(editor)) {
            CM.showHint(editor, prksWikiLinkHint, { completeSingle: false });
        }
    };
    cm.setOption(
        'extraKeys',
        Object.assign({}, keys, {
            // Manual trigger removed (Ctrl-Space conflicts in many browsers / IME).
            // Keep autocomplete via typing `[[` and `[[pdf:` only.
        })
    );
}

/** [[target|label]] / [[target]] → internal links; matches graph wiki resolution (first id per lowercase title). */
function prksReplaceWikiMarkersWithLinks(plainText, titleLowerToId) {
    const map = titleLowerToId || {};
    return plainText.replace(/\[\[([^\]]+)\]\]/g, (full, inner) => {
        const trimmed = String(inner).trim();
        if (/^(concept:|argument:|pdf:)/i.test(trimmed)) return full;
        let target;
        let label;
        const pipe = trimmed.indexOf('|');
        if (pipe >= 0) {
            target = trimmed.slice(0, pipe).trim();
            label = trimmed.slice(pipe + 1).trim() || target;
        } else {
            target = label = trimmed;
        }
        const key = target.toLowerCase();
        const id = map[key];
        if (!id) {
            return (
                '<span class="wiki-link-unresolved" title="Unresolved link">' +
                prksEscapeHtmlLite('[[' + inner + ']]') +
                '</span>'
            );
        }
        return (
            '<a href="#/works/' +
            id +
            '" class="wiki-link-internal">' +
            prksEscapeHtmlLite(label) +
            '</a>'
        );
    });
}

function prksEscapeAttr(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** Default preview label when [[pdf:id]] has no |label — uses annotation cache if available. */
function prksDefaultLabelForPdfAnnId(annId) {
    const rows = typeof window.prksGetPdfAnnotationHintList === 'function' ? window.prksGetPdfAnnotationHintList() : [];
    const sid = String(annId);
    for (const r of rows) {
        if (r.id === sid) return r.displayText;
    }
    return sid.length > 14 ? sid.slice(0, 10) + '…' : sid;
}

/**
 * [[pdf:annotationId]] / [[pdf:annotationId|label]] → link that jumps to that PDF annotation in the viewer (preview click).
 * Process before [[…]] work links so "pdf:…" is not treated as a work title.
 */
function prksReplacePdfAnnotationWikiMarkers(plainText) {
    if (plainText == null || plainText === '') return plainText;
    return plainText.replace(/\[\[pdf:([^\]|]+)(?:\|([^\]]*))?\]\]/g, (full, annIdRaw, labelRaw) => {
        const id = String(annIdRaw || '').trim();
        if (!id) return prksEscapeHtmlLite(full);
        let label = labelRaw != null ? String(labelRaw) : '';
        label = label.trim();
        if (!label) label = prksDefaultLabelForPdfAnnId(id);
        return (
            '<a href="#" class="wiki-link-pdf-ann" data-pdf-ann-id="' +
            prksEscapeAttr(id) +
            '">' +
            prksEscapeHtmlLite(label) +
            '</a>'
        );
    });
}

function prksGetPdfAnnLinkAutocompleteContext(cm) {
    const cur = cm.getCursor();
    const lineText = cm.getLine(cur.line);
    const before = lineText.slice(0, cur.ch);
    const m = before.match(/\[\[pdf:([^\]|]*)$/);
    if (!m) return null;
    const startCh = before.lastIndexOf('[[pdf:') + '[[pdf:'.length;
    const CM = cm.constructor;
    const from = CM.Pos(cur.line, startCh);
    const to = cur;
    return { from, to, query: m[1] || '' };
}

function prksFilterPdfAnnForHint(rows, query) {
    const ql = query.trim().toLowerCase();
    if (!ql) return rows.slice(0, PRKS_WIKI_HINT_MAX);
    const pref = [];
    const sub = [];
    for (const w of rows) {
        const idl = String(w.id || '').toLowerCase();
        const dl = String(w.displayText || '').toLowerCase();
        if (idl.startsWith(ql) || dl.startsWith(ql)) pref.push(w);
        else if (idl.includes(ql) || dl.includes(ql)) sub.push(w);
        if (pref.length >= PRKS_WIKI_HINT_MAX) break;
    }
    if (pref.length >= PRKS_WIKI_HINT_MAX) return pref.slice(0, PRKS_WIKI_HINT_MAX);
    const need = PRKS_WIKI_HINT_MAX - pref.length;
    return pref.concat(sub.slice(0, need));
}

function prksPdfAnnLinkCompletionPick(cm, data, completion) {
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) return;
    const from = completion.from != null ? completion.from : data.from;
    const to = completion.to != null ? completion.to : data.to;
    const id = typeof completion.text === 'string' ? completion.text : '';
    const lab = (completion.displayLabel || '').trim();
    const suffix = lab ? id + '|' + lab + ']]' : id + ']]';
    cm.replaceRange(suffix, from, to, 'complete');
}

function prksPdfAnnLinkHint(cm) {
    const ctx = prksGetPdfAnnLinkAutocompleteContext(cm);
    if (!ctx) return null;
    const rows = typeof window.prksGetPdfAnnotationHintList === 'function' ? window.prksGetPdfAnnotationHintList(prksHintOwnerCtx(cm)) : [];
    const matches = prksFilterPdfAnnForHint(rows, ctx.query);
    if (matches.length === 0) return null;
    return {
        from: ctx.from,
        to: ctx.to,
        list: matches.map((w) => ({
            text: w.id,
            displayText: w.displayText,
            displayLabel: w.displayText,
            hint: prksPdfAnnLinkCompletionPick,
        })),
    };
}

function prksGetConceptLinkAutocompleteContext(cm) {
    const cur = cm.getCursor();
    const lineText = cm.getLine(cur.line);
    const before = lineText.slice(0, cur.ch);
    const m = before.match(/\[\[concept:([^\]|]*)$/);
    if (!m) return null;
    const startCh = before.lastIndexOf('[[concept:') + '[[concept:'.length;
    const CM = cm.constructor;
    return { from: CM.Pos(cur.line, startCh), to: cur, query: m[1] || '' };
}

function prksConceptHintRows(cm) {
    const rows = prksHintResource(cm, 'conceptHintList') || [];
    const out = [];
    for (let i = 0; i < rows.length; i++) {
        const c = rows[i];
        if (!c || !c.name) continue;
        out.push({ name: String(c.name), haystack: String(c.name) });
        const aliases = c.aliases || [];
        for (let j = 0; j < aliases.length; j++) {
            const a = String(aliases[j] || '').trim();
            if (a) out.push({ name: String(c.name), haystack: a });
        }
    }
    return out;
}

function prksFilterConceptsForHint(query, cm) {
    const ql = query.trim().toLowerCase();
    const rows = prksConceptHintRows(cm);
    if (!ql) {
        const seen = {};
        const uniq = [];
        for (let i = 0; i < rows.length; i++) {
            if (seen[rows[i].name]) continue;
            seen[rows[i].name] = true;
            uniq.push(rows[i]);
            if (uniq.length >= PRKS_WIKI_HINT_MAX) break;
        }
        return uniq;
    }
    const pref = [];
    const sub = [];
    const seen = {};
    for (let i = 0; i < rows.length; i++) {
        const w = rows[i];
        const hl = w.haystack.toLowerCase();
        const nl = w.name.toLowerCase();
        if (seen[w.name]) continue;
        if (hl.startsWith(ql) || nl.startsWith(ql)) {
            seen[w.name] = true;
            pref.push(w);
        } else if (hl.includes(ql) || nl.includes(ql)) {
            seen[w.name] = true;
            sub.push(w);
        }
        if (pref.length >= PRKS_WIKI_HINT_MAX) break;
    }
    if (pref.length >= PRKS_WIKI_HINT_MAX) return pref.slice(0, PRKS_WIKI_HINT_MAX);
    return pref.concat(sub.slice(0, PRKS_WIKI_HINT_MAX - pref.length));
}

function prksConceptLinkCompletionPick(cm, data, completion) {
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) return;
    const from = completion.from != null ? completion.from : data.from;
    const to = completion.to != null ? completion.to : data.to;
    const name = typeof completion.text === 'string' ? completion.text : '';
    cm.replaceRange(name + ']]', from, to, 'complete');
}

function prksConceptLinkHint(cm) {
    const ctx = prksGetConceptLinkAutocompleteContext(cm);
    if (!ctx) return null;
    const matches = prksFilterConceptsForHint(ctx.query, cm);
    if (matches.length === 0) return null;
    return {
        from: ctx.from,
        to: ctx.to,
        list: matches.map((w) => ({
            text: w.name,
            displayText: w.haystack === w.name ? w.name : w.name + ' (' + w.haystack + ')',
            hint: prksConceptLinkCompletionPick,
        })),
    };
}

/**
 * CodeMirror's own `readOnly` option is not enough: PRKS's toolbar-driven
 * insert paths (Concept picker, Argument picker, wiki-link/PDF-annotation/
 * Concept autocomplete picks) call this directly and none of them consult
 * CodeMirror state first. This is the one predicate every PRKS-owned
 * programmatic Research Notes edit boundary must check before mutating
 * anything. It requires:
 *   - `ctx` (the resolved owner TabContext) is still live/current
 *   - a live `workNotes` resource on that ctx
 *   - when a CodeMirror instance `cm` is supplied (every real caller has
 *     one), it is the *exact* live instance still installed at
 *     notes.editor.codemirror -- never a detached CodeMirror from a picker/
 *     autocomplete callback that outlived a navigation to a different Work
 * Connectivity is not part of this predicate: Research Notes are durable.
 */
function prksWorkNotesMutationAllowed(ctx, cm) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (!owner) return false;
    if (
        typeof owner.isCurrent === 'function' &&
        typeof owner.generation === 'number' &&
        !owner.isCurrent(owner.generation)
    ) {
        return false;
    }
    const notes = typeof owner.getResource === 'function' ? owner.getResource('workNotes') : null;
    if (!notes || !notes.editor) return false;
    if (cm && notes.editor.codemirror !== cm) return false;
    return true;
}

window.prksWorkNotesMutationAllowed = prksWorkNotesMutationAllowed;

function prksInsertNotesMarkup(cm, markup) {
    if (!cm || !markup) return;
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) return;
    const cur = cm.getCursor();
    cm.replaceRange(markup, cur, cur, 'complete');
    cm.focus();
}

function prksCurrentPdfPagesForWork(workId, ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const pdf = owner && owner.getResource ? owner.getResource('pdf') : null;
    const sess = pdf && pdf.pageSession;
    if (!sess || (workId && sess.workId !== workId)) return '';
    const n = sess.pageNumber;
    if (!Number.isFinite(n) || n < 1) return '';
    return String(Math.floor(n));
}

function prksCloseResearchPicker() {
    const el = document.getElementById('prks-research-picker');
    if (el && el.parentNode) el.parentNode.removeChild(el);
}

function prksOpenResearchPicker(opts) {
    prksCloseResearchPicker();
    const d = document;
    const overlay = d.createElement('div');
    overlay.id = 'prks-research-picker';
    overlay.className = 'prks-research-picker';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'prks-research-picker-title');
    overlay.tabIndex = -1;
    const extra = opts.extraHtml
        ? '<div class="prks-research-picker__actions-start">' + opts.extraHtml + '</div>'
        : '';
    overlay.innerHTML =
        '<div class="prks-dialog prks-research-picker__dialog">' +
        '<div class="prks-dialog__header">' +
        '<h2 class="prks-dialog__title" id="prks-research-picker-title">' +
        prksEscapeHtmlLite(opts.title || '') +
        '</h2>' +
        '<button type="button" class="prks-icon-btn prks-research-picker__close" aria-label="Close">&times;</button>' +
        '</div>' +
        '<div class="prks-dialog__body">' +
        '<input type="search" class="prks-input prks-research-picker__q" placeholder="Search…" autocomplete="off">' +
        '<div class="prks-research-picker__list" role="listbox"></div>' +
        '</div>' +
        '<div class="prks-dialog__actions prks-research-picker__actions">' +
        extra +
        '<button type="button" class="prks-btn prks-btn--secondary prks-research-picker__close">Close</button>' +
        '</div>' +
        '</div>';
    d.body.appendChild(overlay);
    const q = overlay.querySelector('.prks-research-picker__q');
    const list = overlay.querySelector('.prks-research-picker__list');
    function render() {
        const query = (q.value || '').trim().toLowerCase();
        const rows = opts.items() || [];
        const filtered = [];
        for (let i = 0; i < rows.length; i++) {
            const r = rows[i];
            if (!query) {
                filtered.push(r);
            } else {
                const hay = (r.haystack || r.label || '').toLowerCase();
                if (hay.indexOf(query) >= 0) filtered.push(r);
            }
            if (filtered.length >= 40) break;
        }
        let html = filtered
            .map(function (r) {
                const kind = r.kind
                    ? '<span class="prks-research-row__kicker">' +
                      prksEscapeHtmlLite(r.kind) +
                      '</span>'
                    : '';
                return (
                    '<button type="button" class="prks-list-row prks-research-row prks-research-picker__item" data-id="' +
                    prksEscapeAttr(r.id) +
                    '"' +
                    (r.pickType
                        ? ' data-pick-type="' + prksEscapeAttr(r.pickType) + '"'
                        : '') +
                    '><span class="prks-research-row__body">' +
                    kind +
                    '<span class="prks-research-row__title">' +
                    prksEscapeHtmlLite(r.label) +
                    '</span></span></button>'
                );
            })
            .join('');
        const typed = (q.value || '').trim();
        if (typed && typeof opts.createItems === 'function') {
            html += (opts.createItems(typed) || [])
                .map(function (row) {
                    return (
                        '<button type="button" class="prks-list-row prks-research-row prks-research-picker__item prks-research-picker__create" data-create="' +
                        prksEscapeAttr(row.kind || '1') +
                        '">' +
                        prksEscapeHtmlLite(row.label || '') +
                        '</button>'
                    );
                })
                .join('');
        } else if (opts.createLabel && typed) {
            html +=
                '<button type="button" class="prks-list-row prks-research-row prks-research-picker__item prks-research-picker__create" data-create="1">' +
                prksEscapeHtmlLite(opts.createLabel(typed)) +
                '</button>';
        }
        if (!html) html = '<p class="meta-row">No matches.</p>';
        list.innerHTML = html;
    }
    function pick(id, createName, createKind, pickType) {
        prksCloseResearchPicker();
        if (createName && typeof opts.onCreate === 'function') opts.onCreate(createName, createKind);
        else if (id && typeof opts.onPick === 'function') opts.onPick(id, pickType);
    }
    list.addEventListener('click', function (e) {
        const btn = e.target.closest && e.target.closest('button[data-id], button[data-create]');
        if (!btn) return;
        const createKind = btn.getAttribute('data-create');
        if (createKind) pick('', (q.value || '').trim(), createKind);
        else pick(btn.getAttribute('data-id'), '', '', btn.getAttribute('data-pick-type'));
    });
    overlay.querySelectorAll('.prks-research-picker__close').forEach(function (btn) {
        btn.addEventListener('click', prksCloseResearchPicker);
    });
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) prksCloseResearchPicker();
    });
    overlay.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            prksCloseResearchPicker();
        }
    });
    q.addEventListener('input', render);
    q.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            prksCloseResearchPicker();
        }
        if (e.key === 'Enter') {
            e.preventDefault();
            const first = list.querySelector('button[data-id], button[data-create]');
            if (first) first.click();
        }
    });
    render();
    q.focus();
}

function prksOpenConceptPicker(cm) {
    const items = function () {
        return (prksHintResource(cm, 'conceptHintList') || []).map(function (c) {
            const aliases = (c.aliases || []).join(' ');
            return {
                id: c.id,
                label: c.name,
                haystack: (c.name || '') + ' ' + aliases,
            };
        });
    };
    prksOpenResearchPicker({
        title: 'Insert Concept',
        items: items,
        createLabel: function (name) {
            return 'Create “' + name + '”';
        },
        onPick: function (id) {
            const rows = prksHintResource(cm, 'conceptHintList') || [];
            let name = '';
            for (let i = 0; i < rows.length; i++) {
                if (rows[i].id === id) {
                    name = rows[i].name;
                    break;
                }
            }
            if (name) prksInsertNotesMarkup(cm, '[[concept:' + name + ']]');
        },
        onCreate: function (name) {
            prksInsertNotesMarkup(cm, '[[concept:' + name + ']]');
        },
    });
}

function prksOpenArgumentPicker(cm, work) {
    const items = function () {
        return (prksHintResource(cm, 'argumentHintList') || []).map(function (a) {
            return {
                id: a.id,
                label: a.name || a.id,
                kind: a.kind === 'stance' ? 'Stance' : 'Argument',
                haystack: (a.name || '') + ' ' + (a.id || '') + ' ' + (a.kind || ''),
            };
        });
    };
    function insertCreatedArgument(kind, name) {
        if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) return;
        void (async function () {
            if (typeof window.prksCreateArgumentFromWork !== 'function') return;
            const created = await window.prksCreateArgumentFromWork({
                kind: kind,
                name: name || '',
                workId: work && work.id,
                pages: prksCurrentPdfPagesForWork(work && work.id, prksHintOwnerCtx(cm)),
            });
            if (created && created.id) {
                prksInsertNotesMarkup(
                    cm,
                    '[[argument:' + created.id + '|' + (created.name || created.id) + ']]'
                );
                if (typeof fetchArguments === 'function') {
                    const list = await fetchArguments();
                    const owner = prksHintOwnerCtx(cm);
                    if (owner && typeof owner.setResource === 'function') owner.setResource('argumentHintList', list);
                }
            }
        })();
    }
    prksOpenResearchPicker({
        title: 'Insert Argument / Stance',
        items: items,
        createItems: function (name) {
            return [
                { kind: 'argument', label: 'Create Argument “' + name + '”' },
                { kind: 'stance', label: 'Create Stance “' + name + '”' },
            ];
        },
        onPick: function (id) {
            const rows = prksHintResource(cm, 'argumentHintList') || [];
            let name = id;
            for (let i = 0; i < rows.length; i++) {
                if (rows[i].id === id) {
                    name = rows[i].name || id;
                    break;
                }
            }
            prksInsertNotesMarkup(cm, '[[argument:' + id + '|' + name + ']]');
        },
        onCreate: function (name, kind) {
            insertCreatedArgument(kind === 'stance' ? 'stance' : 'argument', name);
        },
    });
}

async function deleteWork(w_id, ownerCtx) {
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const generation = ctx && ctx.generation;
    const confirmed = await prksConfirmDestructive({
        title: 'Delete file?',
        message: 'Are you sure you want to delete this file?',
        confirmLabel: 'Delete file',
    });
    if (!confirmed) return;
    if (
        typeof prksTabContextOwnsEntityRoute === 'function' &&
        !prksTabContextOwnsEntityRoute(ctx, generation, 'work', w_id, 'work')
    ) return;
    try {
        if (typeof prksDeleteWorkDurably !== 'function') {
            throw new Error('File deletion is not available.');
        }
        await prksDeleteWorkDurably(w_id);
        if (
            typeof prksTabContextOwnsEntityRoute === 'function' &&
            prksTabContextOwnsEntityRoute(ctx, generation, 'work', w_id, 'work') &&
            typeof prksNavigate === 'function'
        ) {
            prksNavigate('#/folders', { replace: true, tabId: ctx.tabId });
        }
    } catch (_e) {
        if (ctx && ctx.isCurrent && ctx.isCurrent(generation)) {
            await prksAlertMessage(
                (_e && _e.message) || 'Error deleting file!',
                'Error');
        }
    }
}

/** EasyMDE toolbar uses Font Awesome class names; PRKS does not load that webfont. Map buttons to Lucide. */
const PRKS_EASYMDE_TOOLBAR_ICONS = {
    bold: 'bold',
    italic: 'italic',
    heading: 'heading',
    quote: 'quote',
    'unordered-list': 'list',
    'ordered-list': 'list-ordered',
    link: 'link',
    image: 'image',
    'prks-insert-concept': 'lightbulb',
    'prks-insert-argument': 'message-square',
    preview: 'eye',
    'side-by-side': 'columns-2',
    fullscreen: 'maximize-2',
    'prks-notes-help': 'circle-help',
};

function prksPaintEasyMDEToolbarIcons(toolbar) {
    if (!toolbar) return;
    toolbar.querySelectorAll('button').forEach((btn) => {
        const el = btn.querySelector('i:not(.separator)');
        if (!el || el.getAttribute('data-lucide') || el.querySelector('svg, [data-lucide]')) return;
        let lucideName = '';
        btn.classList.forEach((cls) => {
            if (!lucideName && PRKS_EASYMDE_TOOLBAR_ICONS[cls]) {
                lucideName = PRKS_EASYMDE_TOOLBAR_ICONS[cls];
            }
        });
        if (!lucideName) return;
        el.setAttribute('data-lucide', lucideName);
        el.classList.add('prks-icon', 'prks-icon--sm');
    });
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(toolbar);
}

/**
 * Research Notes liveness: the owner generation and Work still match, and
 * this exact session is the installed `workNotes` slot. A same-generation
 * replacement fails the identity check.
 */
function prksWorkNotesSessionLive(ctx, notes, generation) {
    if (!prksResearchNotesMayPaint(ctx, notes && notes.workId, generation)) return false;
    return typeof ctx.getResource === 'function' && ctx.getResource('workNotes') === notes;
}

/**
 * Builds the pane-local EasyMDE session and registers it as the `workNotes`
 * owner resource with `ticket`, captured when the Work paint began. The
 * session is warm-suspendable: it lives in the parked pane DOM beside the
 * PDF, keeps its buffer and undo history, and its `saveNotesTimeout`
 * debounce stays a TabContext timer. Cold release destroys it. A stale or
 * rejected ticket builds nothing.
 */
function initEasyMDE(ctx, work, ticket) {
    if (!ctx || !work || !ctx.resourceRegistry || !ctx.resourceRegistry.accepts(ticket)) return;
    const titleLowerToId = (ctx && ctx.getResource ? ctx.getResource('wikiTitleMap') : null) || {};
    const prksNotesHelpHtml = `
<div class="prks-help-section">
  <div class="prks-help-title">Work links</div>
  <ul>
    <li><code>[[Work Title]]</code> open work</li>
    <li><code>[[Work Title|Label]]</code> open work with label</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Concepts</div>
  <ul>
    <li><code>[[concept:Culture Industry]]</code> Concept link (created on save if new)</li>
    <li>Type <code>[[concept:</code> for name/alias suggestions</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Arguments / Stances</div>
  <ul>
    <li><code>[[argument:A-123|Label]]</code> stable Argument link</li>
    <li>Unknown Argument IDs stay unresolved; they are not created</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">PDF annotation links</div>
  <ul>
    <li><code>[[pdf:&lt;annotationId&gt;]]</code> jump inside PDF viewer</li>
    <li><code>[[pdf:&lt;annotationId&gt;|Label]]</code> jump with label</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Autocomplete</div>
  <ul>
    <li>Type <code>[[</code> then work suggestions</li>
    <li>Type <code>[[concept:</code> then Concept suggestions</li>
    <li>Type <code>[[pdf:</code> then annotation suggestions</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Preview</div>
  <ul>
    <li>Click work, Concept, or Argument link to open that record</li>
    <li>Click pdf link to jump to annotation</li>
  </ul>
</div>
`.trim();
    try {
        localStorage.removeItem('smde_work-notes-' + work.id);
    } catch (_e) {
        /* ignore */
    }
    const notesEl = ctx.query ? ctx.query('[data-prks-role="research-notes-editor"]') : null;
    if (!notesEl) return;
    const easyMDE = new EasyMDE({
        element: notesEl,
        spellChecker: false,
        autoDownloadFontAwesome: false,
        /* Durable store is the source of truth; EasyMDE localStorage autosave would restore stale drafts after reload. */
        autosave: { enabled: false },
        toolbar: [
            "bold",
            "italic",
            "heading",
            "|",
            "quote",
            "unordered-list",
            "ordered-list",
            "|",
            "link",
            "image",
            "|",
            {
                name: "prks-insert-concept",
                className: "fa fa-lightbulb-o prks-notes-concept",
                title: "Concept",
                action: () => {
                    const _mde = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                    const editor = _mde && _mde.editor ? _mde.editor : _mde;
                    const cm = editor && editor.codemirror;
                    if (cm) prksOpenConceptPicker(cm);
                },
            },
            {
                name: "prks-insert-argument",
                className: "fa fa-comment prks-notes-argument",
                title: "Argument",
                action: () => {
                    const _mde = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                    const editor = _mde && _mde.editor ? _mde.editor : _mde;
                    const cm = editor && editor.codemirror;
                    if (cm) prksOpenArgumentPicker(cm, work);
                },
            },
            "|",
            "preview",
            "side-by-side",
            "fullscreen",
            "|",
            {
                name: "prks-notes-help",
                className: "fa fa-question-circle prks-notes-help",
                title: "PRKS Notes Help",
                action: () => {
                    if (typeof prksAlertDialog === 'function') {
                        void prksAlertDialog({
                            title: 'Notes help',
                            messageHtml: prksNotesHelpHtml,
                            okLabel: 'Close',
                        });
                        return;
                    }
                    window.alert('Notes help: see wiki links [[...]] and [[pdf:...]].');
                },
            },
        ],
        status: ["lines", "words", "cursor"],
        minHeight: "120px",
        previewRender: (plainText) => {
            const _cwPrev = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
            const refs =
                (_cwPrev && _cwPrev.id === work.id && _cwPrev.research_refs) ||
                work.research_refs ||
                {};
            let t = plainText;
            if (typeof window.prksReplaceResearchRefs === 'function') {
                t = window.prksReplaceResearchRefs(t, refs);
            }
            t = prksReplacePdfAnnotationWikiMarkers(t);
            t = prksReplaceWikiMarkersWithLinks(t, titleLowerToId);
            return prksSanitizeMarkdownPreviewHtml(easyMDE.markdown(t));
        },
    });

    const cmInput = easyMDE.codemirror && typeof easyMDE.codemirror.getInputField === 'function'
        ? easyMDE.codemirror.getInputField()
        : null;
    if (cmInput && typeof cmInput.setAttribute === 'function') {
        cmInput.setAttribute('aria-label', 'Research Notes');
    }

    const transient = prksWorkResearchDrafts.get(prksResearchDraftKey(ctx, work.id));
    const workNotes = {
        workId: String(work.id),
        editor: easyMDE,
        hints: {
            wikiTitleMap: titleLowerToId,
        },
        pendingSave: false,
        drafting: false,
        saveError: false,
        editGeneration: 0,
        saveSequence: 0,
        latestSaveToken: 0,
        latestSaveEditGeneration: 0,
        settledSaveToken: 0,
        destroy: function () {
            try {
                const cm = easyMDE.codemirror;
                const handler = easyMDE.__notesChangeHandler;
                if (cm && handler) cm.off('change', handler);
            } catch (_e) {}
            try {
                if (typeof easyMDE.toTextArea === 'function') easyMDE.toTextArea();
            } catch (_e) {}
            try {
                /* toTextArea() keeps EasyMDE's document keydown; cleanup() is its pair (#459). */
                if (typeof easyMDE.cleanup === 'function') easyMDE.cleanup();
            } catch (_e) {}
        },
    };
    if (transient) prksSyncResearchNotesState(workNotes, transient);
    const attached = ctx.registerResource(ticket, {
        kind: 'workNotes',
        value: workNotes,
        suspendable: true,
        dispose: function () {
            if (typeof workNotes.stopSync === 'function') workNotes.stopSync();
            workNotes.destroy();
            if (typeof window.prksVueDismissWorkResearchNotes === 'function') {
                window.prksVueDismissWorkResearchNotes(ctx);
            }
        },
    });
    if (attached === 'rejected') {
        workNotes.destroy();
        return;
    }
    const notesGeneration = ticket.generation;
    if (window.prksSync && typeof window.prksSync.subscribe === 'function') {
        workNotes.stopSync = window.prksSync.subscribe(function (event) {
            if (!event || event.operation !== 'SET_WORK_RESEARCH_NOTE') return;
            if (event.op && event.op.entity_id !== workNotes.workId) return;
            if (workNotes.drafting) return;
            if (!prksWorkNotesSessionLive(ctx, workNotes, notesGeneration)) return;
            const statusEl = ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
            if (!statusEl) return;
            const text = prksResearchNotesSyncEventStatus(ctx, workNotes, event);
            if (text) statusEl.innerText = text;
        });
    }
    /* A refresh of the same Work may remount while a newer body is still
     * waiting behind an earlier save (#465); its retry died with the old
     * editor, so arm one for this session. */
    if (transient && transient.state === 'blocked') {
        prksScheduleResearchNotesBusyRetry(ctx, work.id, transient);
    }
    /* Restored from recovery storage (#466): say so, and send it through the
     * ordinary save path as if it had just been typed. */
    if (transient && transient.recoveryRestored) {
        const restoredStatus = ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
        if (restoredStatus && transient.state === 'drafting') restoredStatus.innerText = PRKS_RESEARCH_NOTES_RESTORED_STATUS;
        if (transient.recoverySaveDue && transient.state === 'drafting') {
            transient.recoverySaveDue = false;
            prksScheduleWorkResearchNotesSave(ctx, work.id);
        }
        if (ctx.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
            window.prksWorkspaceRefreshTabStatus(ctx.tabId);
        }
    }
    prksAttachWikiLinkAutocomplete(easyMDE.codemirror, ctx);
    const toolbarHost = ctx && ctx.query ? ctx.query('.work-notes-editor-wrap .editor-toolbar') : null;
    prksPaintEasyMDEToolbarIcons(toolbarHost);

    const wrap = ctx && ctx.query ? ctx.query('.work-notes-editor-wrap') : null;
    if (wrap) {
        wrap.addEventListener(
            'click',
            (e) => {
                const pdfA = e.target.closest && e.target.closest('a.wiki-link-pdf-ann');
                if (pdfA) {
                    e.preventDefault();
                    const annId = pdfA.getAttribute('data-pdf-ann-id');
                    if (annId && typeof window.prksJumpToPdfAnnotationFromNotes === 'function') {
                        void window.prksJumpToPdfAnnotationFromNotes(annId, ctx);
                    }
                    return;
                }
                /* Internal wiki links use the workspace navigation contract. */
            },
            true
        );
    }

    const notesChangeHandler = () => {
        if (!prksWorkNotesSessionLive(ctx, workNotes, notesGeneration)) return;
        const statusEl = ctx && ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
        if (statusEl) statusEl.innerText = "Drafting...";
        prksWorkNotesMarkEdit(workNotes, work.id, easyMDE.value(), ctx);
        if (ctx && ctx.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
            window.prksWorkspaceRefreshTabStatus(ctx.tabId);
        }
        prksScheduleWorkResearchNotesSave(ctx, work.id);
    };
    easyMDE.codemirror.on("change", notesChangeHandler);
    easyMDE.__notesChangeHandler = notesChangeHandler;
}

window.initEasyMDE = initEasyMDE;

function prksResearchNotesEditOwner(notes, ctx) {
    if (ctx) return ctx;
    let found = null;
    if (typeof prksForEachLiveTabContext === 'function') {
        prksForEachLiveTabContext(function (live) {
            if (found || !live || typeof live.getResource !== 'function') return;
            if (live.getResource('workNotes') === notes) found = live;
        });
    }
    return found;
}

function prksWorkNotesMarkEdit(notes, workId, text, ctx) {
    if (!notes) return 0;
    notes.editGeneration = (Number(notes.editGeneration) || 0) + 1;
    notes.drafting = true;
    const id = String(workId || notes.workId || '');
    const owner = prksResearchNotesEditOwner(notes, ctx);
    if (id && owner) {
        const entry = prksResearchDraftEntry(owner, id, text);
        entry.text = String(text == null ? '' : text);
        entry.editGeneration = Math.max(entry.editGeneration + 1, notes.editGeneration);
        entry.state = 'drafting';
        entry.saveError = false;
        entry.updatedAt = Date.now();
        entry.recoveryRestored = false;
        prksResearchRecoveryEdit(owner, entry);
        prksSyncResearchNotesState(notes, entry);
        prksStopResearchNotesBusyRetry(owner);
    }
    return notes.editGeneration;
}

function prksWorkNotesBeginSave(notes) {
    if (!notes) return 0;
    notes.saveSequence = (Number(notes.saveSequence) || 0) + 1;
    const token = notes.saveSequence;
    const capturedEditGeneration = Number(notes.editGeneration) || 0;
    notes.latestSaveToken = token;
    notes.latestSaveEditGeneration = capturedEditGeneration;
    notes.pendingSave = notes.latestSaveToken > (Number(notes.settledSaveToken) || 0);
    notes.drafting = (Number(notes.editGeneration) || 0) > capturedEditGeneration;
    return token;
}

function prksWorkNotesSettleSave(notes, token, ok) {
    if (!notes) return false;
    if (token !== notes.latestSaveToken) return false;
    notes.settledSaveToken = token;
    notes.pendingSave = notes.latestSaveToken > notes.settledSaveToken;
    notes.saveError = !ok;
    notes.drafting =
        (Number(notes.editGeneration) || 0) > (Number(notes.latestSaveEditGeneration) || 0);
    return true;
}

function prksScheduleWorkResearchNotesSave(ctx, workId) {
    if (ctx && typeof ctx.clearTimer === 'function') ctx.clearTimer('saveNotesTimeout');
    const tid = setTimeout(function () {
        if (ctx && ctx.timers && ctx.timers.get('saveNotesTimeout') === tid) {
            ctx.clearTimer('saveNotesTimeout');
        }
        prksEnqueueWorkResearchNotesSave(ctx, workId);
    }, 2000);
    if (ctx && typeof ctx.setTimer === 'function') ctx.setTimer('saveNotesTimeout', tid);
}

const PRKS_RESEARCH_NOTES_BUSY_RETRY_TIMER = 'researchNotesBusyRetry';
const PRKS_RESEARCH_NOTES_BUSY_RETRY_MAX_MS = 30000;
// ctx -> stop for that owner's one Research Notes busy retry.
const prksResearchNotesBusyRetryStops = new WeakMap();

function prksStopResearchNotesBusyRetry(ctx) {
    const stop = ctx ? prksResearchNotesBusyRetryStops.get(ctx) : null;
    if (stop) stop();
}

/** The owner's live Research Notes editor for this Work, or null. */
function prksResearchNotesBusyRetryTarget(ctx, workId) {
    if (!ctx || ctx.destroyed) return null;
    if (typeof ctx.isCurrent === 'function' && !ctx.isCurrent()) return null;
    const live = ctx.getEntity ? ctx.getEntity('work') : null;
    if (!live || String(live.id) !== String(workId)) return null;
    const notes = ctx.getResource ? ctx.getResource('workNotes') : null;
    if (!notes || String(notes.workId) !== String(workId) || !notes.editor) return null;
    return notes;
}

/**
 * The base a blocked body is re-sent against. A blocked body was refused
 * against `entry.blockedBase`. The owner's current observed base replaces it
 * only when it is unchanged, or when it advanced to the body this session
 * itself last queued from that same blocked base: that save is B's immediate
 * predecessor, B was typed on top of it, and replacing it overwrites nothing
 * this session has not seen. An older own save with the same text, queued
 * from a different base, does not count. The blocking row comes from
 * the shared queue, its acknowledgement advances every same-Work owner, and
 * a same-Work refresh rebuilds the base from the server, so an advanced base
 * holding any other body keeps the blocked base, and the server reports the
 * conflict instead of the retry silently overwriting it.
 */
function prksResearchNotesBlockedSendBase(ctx, entry) {
    const blockedBase = entry && entry.blockedBase;
    const slot = typeof prksWorkNoteObserved === 'function' ? prksWorkNoteObserved(ctx, 'work-research-note') : null;
    if (!blockedBase) return prksResearchNotesBaseCopy(slot);
    if (!slot) return blockedBase;
    if (slot.value === blockedBase.value && slot.revision === blockedBase.revision) return prksResearchNotesBaseCopy(slot);
    const ownBase = entry.ownQueuedBase;
    const advancedByOwn = typeof entry.ownQueuedText === 'string' && slot.value === entry.ownQueuedText &&
        !!ownBase && ownBase.value === blockedBase.value && ownBase.revision === blockedBase.revision &&
        slot.revision >= blockedBase.revision;
    return advancedByOwn ? prksResearchNotesBaseCopy(slot) : blockedBase;
}

/**
 * Re-send a blocked body against `prksResearchNotesBlockedSendBase`. When
 * that base still predates the blocking save's outcome (its revision is not
 * newer than the refused base) and the body equals it, the store would treat
 * the save as a no-op and nothing would reach the server, although the
 * blocking save may have changed the note there. That cannot be confirmed
 * locally, so the body is left explicitly unsaved instead of reading as
 * saved; the next edit saves it again.
 */
function prksSendBlockedResearchNote(ctx, id, entry) {
    const base = prksResearchNotesBlockedSendBase(ctx, entry);
    const notes = prksResearchNotesBusyRetryTarget(ctx, id);
    const editor = notes && notes.editor;
    const body = editor && typeof editor.value === 'function' ? editor.value() : null;
    const refused = entry.blockedBase;
    if (base && refused && body === base.value && base.revision <= refused.revision) {
        entry.state = 'error';
        entry.saveError = true;
        entry.updatedAt = Date.now();
        prksResearchRecoveryPipeline(entry, { state: 'error' });
        prksSyncResearchNotesState(notes, entry);
        const statusEl = ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
        if (statusEl) statusEl.innerText = PRKS_RESEARCH_NOTES_UNCONFIRMED_STATUS;
        if (ctx.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
            window.prksWorkspaceRefreshTabStatus(ctx.tabId);
        }
        return;
    }
    void prksEnqueueWorkResearchNotesSave(ctx, id, base ? { base: base } : undefined);
}

const PRKS_RESEARCH_NOTES_UNCONFIRMED_STATUS = 'Not saved: this note changed elsewhere. Edit to save it again.';

/**
 * A save refused with `scope_busy` (#465): an earlier, attempted operation
 * still holds this note's aggregate, and the store keeps it immutable. The
 * newer body was never written. It stays `blocked` on the session, and this
 * re-enqueues the live editor body once no unsettled Research Notes row is
 * left for the Work, on a sync event or a backed-off fallback timer. The
 * re-send uses this owner's observed base, which only an acknowledgement in
 * this runtime advances: when another browser tab sent the blocking row, that
 * base is stale and the server may park B as REVISION_CONFLICT instead of
 * saving it (#476). It retries only while this owner
 * generation, Work, live editor, and blocked session still hold, so a newer
 * edit, a route change, cold release, or destroy stops it. The timer is a
 * TabContext timer and the subscription a `registerCleanup`, so teardown
 * stops both. Only one retry runs per owner.
 */
function prksScheduleResearchNotesBusyRetry(ctx, workId, entry) {
    /* A stale settlement (another Work, a released editor, or a replaced
     * session) must not replace this owner's live retry. */
    if (!entry || !prksResearchNotesBusyRetryTarget(ctx, workId)) return;
    if (prksWorkResearchDrafts.get(prksResearchDraftKey(ctx, workId)) !== entry) return;
    prksStopResearchNotesBusyRetry(ctx);
    /* A same-Work remount starts with a blank status; keep disclosing that
     * the body is unsaved while the retry waits. */
    const blockedStatus = entry.state === 'blocked' && ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
    if (blockedStatus) blockedStatus.innerText = prksLiveResearchDraftStatus(entry, null);
    const id = String(workId);
    const generation = ctx.generation;
    const token = entry.latestSaveToken;
    let delay = 1000;
    let stopped = false;
    let checking = false;
    let recheck = false;
    let stopSync = null;
    let unregisterCleanup = null;
    const stop = function () {
        if (stopped) return;
        stopped = true;
        if (prksResearchNotesBusyRetryStops.get(ctx) === stop) prksResearchNotesBusyRetryStops.delete(ctx);
        if (typeof ctx.clearTimer === 'function') ctx.clearTimer(PRKS_RESEARCH_NOTES_BUSY_RETRY_TIMER);
        const unsubscribe = stopSync;
        const unregister = unregisterCleanup;
        stopSync = null;
        unregisterCleanup = null;
        if (unregister) unregister();
        if (unsubscribe) {
            try { unsubscribe(); } catch (_e) { /* ignore */ }
        }
    };
    const due = function () {
        if (stopped) return false;
        if (typeof generation === 'number' && ctx.generation !== generation) return false;
        if (!prksResearchNotesBusyRetryTarget(ctx, id)) return false;
        if (prksWorkResearchDrafts.get(prksResearchDraftKey(ctx, id)) !== entry) return false;
        return entry.state === 'blocked' && !entry.promise && entry.latestSaveToken === token;
    };
    const arm = function () {
        if (stopped || typeof ctx.setTimer !== 'function') return;
        const tid = setTimeout(function () {
            if (ctx.timers && ctx.timers.get(PRKS_RESEARCH_NOTES_BUSY_RETRY_TIMER) === tid) {
                ctx.clearTimer(PRKS_RESEARCH_NOTES_BUSY_RETRY_TIMER);
            }
            void check();
        }, delay);
        delay = Math.min(delay * 2, PRKS_RESEARCH_NOTES_BUSY_RETRY_MAX_MS);
        ctx.setTimer(PRKS_RESEARCH_NOTES_BUSY_RETRY_TIMER, tid);
    };
    const check = async function () {
        if (checking) {
            recheck = true;
            return;
        }
        if (!due()) {
            stop();
            return;
        }
        checking = true;
        let busy = false;
        try {
            if (typeof prksRefreshPendingWorkNotes === 'function' && typeof prksWorkNoteOperations === 'function') {
                const rows = await prksRefreshPendingWorkNotes();
                busy = prksWorkNoteOperations(rows, id, 'work-research-note').length > 0;
            }
        } catch (_e) {
            busy = true;
        } finally {
            checking = false;
        }
        if (!due()) {
            stop();
            return;
        }
        if (busy) {
            if (recheck) {
                recheck = false;
                void check();
                return;
            }
            const armed = ctx.timers && ctx.timers.has(PRKS_RESEARCH_NOTES_BUSY_RETRY_TIMER);
            if (!armed) arm();
            return;
        }
        stop();
        prksSendBlockedResearchNote(ctx, id, entry);
    };
    if (window.prksSync && typeof window.prksSync.subscribe === 'function') {
        stopSync = window.prksSync.subscribe(function () { void check(); });
    }
    if (typeof ctx.registerCleanup === 'function') unregisterCleanup = ctx.registerCleanup(stop);
    prksResearchNotesBusyRetryStops.set(ctx, stop);
    arm();
}

window.prksWorkNotesMarkEdit = prksWorkNotesMarkEdit;
window.prksWorkNotesBeginSave = prksWorkNotesBeginSave;
window.prksWorkNotesSettleSave = prksWorkNotesSettleSave;
window.prksScheduleWorkResearchNotesSave = prksScheduleWorkResearchNotesSave;

function prksResearchNotesStatusForResult(code, pending) {
    if (code === 'saved') {
        if (!pending) return 'All changes saved';
        return (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online')
            ? 'Offline · saved locally'
            : 'Waiting to sync';
    }
    if (code === 'scope_busy') return 'Still syncing — wait or resolve the conflict in Diagnostics';
    if (code === 'unknown_base') {
        return 'Notes cannot be saved yet — open this file while connected once';
    }
    if (code === 'too-long') return 'This note is too large to save';
    if (code === 'unavailable') return 'Local changes could not be read from browser storage';
    return 'Error saving changes';
}

/** Whether a Research Notes row is still queued for the Work after a save. */
async function prksResearchNotesPendingAfterSave(workId) {
    if (typeof prksRefreshPendingWorkNotes !== 'function') return false;
    await prksRefreshPendingWorkNotes();
    if (typeof prksWorkNoteOperations !== 'function') return false;
    const rows = await prksRefreshPendingWorkNotes();
    return prksWorkNoteOperations(rows, workId, 'work-research-note').length > 0;
}

/**
 * Session state after a save settles. `scope_busy` means an earlier save
 * still holds the aggregate (#465): this body was never written, so it stays
 * unsaved (`blocked`) and is retried once that save settles, not dropped as
 * an error.
 */
function prksResearchDraftSettledState(code, hasNewerDraft) {
    if (hasNewerDraft) return 'drafting';
    if (code === 'scope_busy') return 'blocked';
    return code === 'saved' ? 'committed' : 'error';
}

function prksResearchNotesBaseCopy(base) {
    return base ? { value: base.value, revision: base.revision } : null;
}

function prksEnqueueWorkResearchNotesSave(ctx, workId, options) {
    const sendBase = options && options.base ? options.base : null;
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const saveGeneration = owner && typeof owner.generation === 'number' ? owner.generation : undefined;
    const _cwSave = owner && owner.getEntity ? owner.getEntity('work') : null;
    const id = workId || (_cwSave && _cwSave.id);
    if (!id) return;
    const notes = owner && owner.getResource ? owner.getResource('workNotes') : null;
    const editor = notes && notes.editor ? notes.editor : notes;
    let content = '';
    if (editor && typeof editor.value === 'function') {
        content = editor.value();
    }
    const existingTransient = id ? prksWorkResearchDrafts.get(prksResearchDraftKey(owner, id)) : null;
    if (
        existingTransient &&
        existingTransient.promise &&
        existingTransient.text === content &&
        existingTransient.latestSaveEditGeneration === existingTransient.editGeneration &&
        existingTransient.latestSaveEditGeneration === (Number(notes && notes.editGeneration) || 0)
    ) {
        return existingTransient.promise;
    }
    const statusEl = owner && owner.query ? owner.query('[data-prks-role="editor-status"]') : null;
    const token = prksWorkNotesBeginSave(notes);
    const transient = id ? prksResearchDraftEntry(owner, id, content) : null;
    let transientToken = 0;
    if (transient) {
        transient.text = content;
        transient.editGeneration = Math.max(transient.editGeneration, Number(notes && notes.editGeneration) || 0);
        transient.saveSequence += 1;
        transientToken = transient.saveSequence;
        transient.latestSaveToken = transientToken;
        transient.latestSaveEditGeneration = transient.editGeneration;
        transient.state = 'saving';
        transient.saveError = false;
        transient.updatedAt = Date.now();
        prksSyncResearchNotesState(notes, transient);
        /* The queue never holds a body newer than recovery storage. */
        prksResearchRecoveryFlush(transient);
        prksResearchRecoveryPipeline(transient, { state: 'saving' });
    }
    const savedGeneration = transient ? transient.latestSaveEditGeneration : 0;
    if (statusEl) statusEl.innerText = 'Saving...';
    if (owner && owner.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
        window.prksWorkspaceRefreshTabStatus(owner.tabId);
    }

    let usedBase = null;
    const savePromise = (async function () {
        let observed = sendBase || (typeof prksWorkNoteObserved === 'function'
            ? prksWorkNoteObserved(owner, 'work-research-note')
            : null);
        if (!observed && typeof prksEnsureWorkNotesBase === 'function') {
            const capturedWork = (owner.getResource && owner.getResource('workNotesCanonical')) || _cwSave;
            const base = await prksEnsureWorkNotesBase(owner, capturedWork, { publish: false });
            if (base && prksResearchNotesMayPaint(owner, id, saveGeneration)) {
                if (typeof prksRememberWorkNotesCanonical === 'function') {
                    prksRememberWorkNotesCanonical(owner, capturedWork);
                }
                if (owner && typeof owner.setResource === 'function') {
                    owner.setResource('workNotesObserved', base);
                }
            }
            observed = base && base.research ? base.research : null;
        }
        if (typeof prksSaveWorkNoteDurably !== 'function') {
            return { code: 'unavailable' };
        }
        usedBase = prksResearchNotesBaseCopy(observed);
        const saved = await prksSaveWorkNoteDurably(id, 'work-research-note', content, observed);
        /* Provenance for a later blocked body: every save this session queued,
         * newest token wins, recorded before the pending scan and whether or
         * not a newer save has since taken over the session's status. */
        if (saved && saved.code === 'saved' && transient && transientToken > (transient.ownQueuedToken || 0)) {
            transient.ownQueuedToken = transientToken;
            transient.ownQueuedText = content;
            transient.ownQueuedBase = usedBase;
            prksResearchRecoveryQueued(transient, saved.opId, savedGeneration, content, usedBase);
        }
        return saved;
    })();
    if (transient) transient.promise = savePromise;
    void savePromise
        .then(async function (result) {
            const code = result && result.code;
            const ok = code === 'saved';
            const pending = ok ? await prksResearchNotesPendingAfterSave(id) : false;
            const localApplied = prksWorkNotesSettleSave(notes, token, ok);
            let transientApplied = false;
            if (transient && transientToken === transient.latestSaveToken) {
                const hasNewerDraft = transient.editGeneration > transient.latestSaveEditGeneration;
                transient.settledSaveToken = transientToken;
                transient.promise = null;
                transient.state = prksResearchDraftSettledState(code, hasNewerDraft);
                transient.saveError = transient.state === 'error';
                const blocked = transient.state === 'blocked';
                if (blocked) {
                    /* Refused against this base; a retry keeps it (#465). */
                    transient.blockedBase = usedBase;
                }
                transient.updatedAt = Date.now();
                transientApplied = true;
                prksResearchRecoverySettled(owner, id, transient, result,
                    { generation: savedGeneration, text: content });
                prksSyncResearchNotesState(notes, transient);
                prksSyncLiveResearchDraft(id, transient, saveGeneration, { code: code, pending: pending });
                prksPruneResearchDrafts();
                if (blocked) prksScheduleResearchNotesBusyRetry(owner, id, transient);
            }
            if (!localApplied && !transientApplied) return undefined;
            if (prksResearchNotesMayPaint(owner, id, saveGeneration)) {
                const liveStatus = owner.query ? owner.query('[data-prks-role="editor-status"]') : null;
                if (liveStatus) {
                    liveStatus.innerText = transientApplied
                        ? prksLiveResearchDraftStatus(transient, { code: code, pending: pending })
                        : prksResearchNotesStatusForResult(code, pending);
                }
            }
            return result;
        })
        .catch(function () {
            const applied = prksWorkNotesSettleSave(notes, token, false);
            let transientApplied = false;
            if (transient && transientToken === transient.latestSaveToken) {
                const hasNewerDraft = transient.editGeneration > transient.latestSaveEditGeneration;
                transient.settledSaveToken = transientToken;
                transient.promise = null;
                transient.saveError = !hasNewerDraft;
                transient.state = hasNewerDraft ? 'drafting' : 'error';
                transient.updatedAt = Date.now();
                transientApplied = true;
                prksResearchRecoverySettled(owner, id, transient, { code: 'failed' }, null);
                prksSyncResearchNotesState(notes, transient);
                prksSyncLiveResearchDraft(id, transient, saveGeneration, { code: 'failed', pending: false });
            }
            if (!applied && !transientApplied) return;
            if (prksResearchNotesMayPaint(owner, id, saveGeneration)) {
                const liveStatus = owner.query ? owner.query('[data-prks-role="editor-status"]') : null;
                if (liveStatus) {
                    liveStatus.innerText =
                        transientApplied && transient.state === 'drafting'
                            ? 'Drafting...'
                            : 'Error saving changes';
                }
            }
        });
    return savePromise;
}

function prksFlushPendingWorkResearchNotes(ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (!owner) return;
    const _hasSaveTimer = owner.timers && owner.timers.has('saveNotesTimeout');
    if (_hasSaveTimer && typeof owner.clearTimer === 'function') owner.clearTimer('saveNotesTimeout');
    const _cw = owner.getEntity ? owner.getEntity('work') : null;
    const id = _cw && _cw.id;
    if (!id) return;
    if (_hasSaveTimer) {
        prksEnqueueWorkResearchNotesSave(owner, id);
        return;
    }
    prksFlushTimerlessResearchNote(owner, id);
}

/**
 * A scope_busy settlement leaves the newest body unsaved with no debounce
 * timer (#465). Leaving still sends it, through the live editor only, and a
 * blocked body against the base it was refused on.
 */
function prksFlushTimerlessResearchNote(owner, id) {
    const entry = prksWorkResearchDrafts.get(prksResearchDraftKey(owner, id));
    if (!entry || entry.promise || (entry.state !== 'blocked' && entry.state !== 'drafting')) return;
    if (!prksResearchNotesBusyRetryTarget(owner, id)) return;
    prksStopResearchNotesBusyRetry(owner);
    if (entry.state === 'blocked') {
        prksSendBlockedResearchNote(owner, id, entry);
        return;
    }
    prksEnqueueWorkResearchNotesSave(owner, id);
}

window.prksEnqueueWorkResearchNotesSave = prksEnqueueWorkResearchNotesSave;
window.prksFlushPendingWorkResearchNotes = prksFlushPendingWorkResearchNotes;

/**
 * EasyMDE toolbar buttons that alter Markdown content. PRKS's own Concept/
 * Argument buttons call picker/insert logic directly and never consult
 * CodeMirror's readOnly flag at all -- disabling every mutating button here
 * (native `disabled`, blocks both mouse and keyboard activation) is the
 * belt to prksWorkNotesMutationAllowed()'s suspenders. Preview/side-by-side/
 * fullscreen/Help never alter the document and stay available.
 */
const PRKS_EASYMDE_MUTATING_TOOLBAR_CLASSES = [
    'bold',
    'italic',
    'heading',
    'quote',
    'unordered-list',
    'ordered-list',
    'link',
    'image',
    'prks-insert-concept',
    'prks-insert-argument',
];

function prksSetEasyMDEToolbarMutationEnabled(ctx, enabled) {
    const toolbar = ctx && ctx.query ? ctx.query('.work-notes-editor-wrap .editor-toolbar') : null;
    if (!toolbar) return;
    toolbar.querySelectorAll('button').forEach((btn) => {
        const mutating = PRKS_EASYMDE_MUTATING_TOOLBAR_CLASSES.some((cls) => btn.classList.contains(cls));
        if (!mutating) return;
        btn.disabled = !enabled;
        btn.classList.toggle('prks-toolbar-btn--disabled', !enabled);
        if (enabled) btn.removeAttribute('aria-disabled');
        else btn.setAttribute('aria-disabled', 'true');
    });
}

function prksDestroyWorkNotesEditor(ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (!owner || typeof owner.clearResource !== 'function') return;
    owner.clearResource('workNotes');
    owner.clearResource('wikiTitleMap');
    owner.clearResource('wikiWorkList');
    owner.clearResource('conceptHintList');
    owner.clearResource('argumentHintList');
    if (owner.ui) owner.ui.researchNotesHints = null;
    if (typeof window.prksVueDismissWorkResearchNotes === 'function') {
        window.prksVueDismissWorkResearchNotes(owner);
    }
}

window.prksDestroyWorkNotesEditor = prksDestroyWorkNotesEditor;

function prksWorkNotesMobileSideActive(ctx) {
    const ws =
        ctx && typeof ctx.query === 'function'
            ? ctx.query('.work-workspace[data-work-id]')
            : document.querySelector('.prks-tab-root .work-workspace[data-work-id], .work-workspace[data-work-id]');
    return !!(ws && ws.classList.contains('work-workspace--side'));
}

function prksWorkNotesEditor(ctx) {
    return ctx && typeof ctx.getResource === 'function' ? ctx.getResource('workNotes') : null;
}

function prksSyncWorkNotesSplitAria(ws, handle, side) {
    if (!ws || !handle) return;
    const vertical = side != null ? side : ws.classList.contains('work-workspace--side');
    const min = 160;
    const max = vertical
        ? prksClampWorkNotesSideWidth(ws, handle, Number.MAX_SAFE_INTEGER)
        : prksClampWorkNotesHeight(ws, handle, Number.MAX_SAFE_INTEGER);
    const raw = ws.style.getPropertyValue(vertical ? '--work-notes-width' : '--work-notes-height').trim();
    const now = parseInt(raw, 10) || min;
    handle.setAttribute('aria-valuemin', String(min));
    handle.setAttribute('aria-valuemax', String(max));
    handle.setAttribute('aria-valuenow', String(Math.max(min, Math.min(max, now))));
}

function prksReapplyWorkNotesSplitLayout(ctx) {
    if (!ctx || typeof ctx.query !== 'function') return;
    const ws = ctx.query('.work-workspace[data-work-id]');
    if (!ws) return;
    const workId = ws.getAttribute('data-work-id');
    if (!workId) return;
    const handle = ws.querySelector('.work-split-handle');
    if (!handle) return;
    const width = ws.clientWidth || 0;
    const mobileForceSide =
        typeof prksGetMobileWorkNotesRightEnabled === 'function' && prksGetMobileWorkNotesRightEnabled();
    const inTiled = !!(ws.closest && ws.closest('.prks-workspace-canvas--tiled'));
    const narrowPx =
        typeof window.PRKS_WORKSPACE_NARROW_PX === 'number' && window.PRKS_WORKSPACE_NARROW_PX > 0
            ? window.PRKS_WORKSPACE_NARROW_PX
            : 720;
    const isNarrowWidth =
        typeof window.prksWorkspaceWidthIsNarrow === 'function'
            ? !!window.prksWorkspaceWidthIsNarrow(width)
            : width > 0 && width <= narrowPx;
    /* Stacked wide → sidecar. Tiled expanded → always drawer (never a side strip against
     * the Main/Secondary separator — Settings cannot override that). Narrow stacked →
     * drawer unless Settings forces a sidecar. Narrow matches CSS max-width:NARROW_PX
     * (inclusive at the threshold). */
    const wantSide =
        !inTiled && width > 0 && (!isNarrowWidth || mobileForceSide);
    const collapsed = ws.classList.contains('work-workspace--notes-collapsed');
    const wantDrawer = !wantSide && !collapsed && width > 0;
    ws.classList.toggle('work-workspace--side', wantSide);
    ws.classList.toggle('work-workspace--notes-drawer', wantDrawer);

    if (prksWorkNotesMobileSideActive(ctx)) {
        const storageKeyW = 'prks.workNotesSideWidth.' + workId;
        const savedW = localStorage.getItem(storageKeyW);
        let initialW = 280;
        if (savedW) {
            const n = parseInt(savedW, 10);
            if (!Number.isNaN(n) && n >= 160 && n <= 600) initialW = n;
        }
        const clampW = prksClampWorkNotesSideWidth(ws, handle, initialW);
        ws.style.setProperty('--work-notes-width', clampW + 'px');
    } else {
        const storageKeyH = 'prks.workNotesHeight.' + workId;
        const savedH = localStorage.getItem(storageKeyH);
        let initialH = 320;
        if (savedH) {
            const n = parseInt(savedH, 10);
            if (!Number.isNaN(n) && n >= 160 && n <= 900) initialH = n;
        }
        const clampH = prksClampWorkNotesHeight(ws, handle, initialH);
        ws.style.setProperty('--work-notes-height', clampH + 'px');
    }

    handle.setAttribute('aria-orientation', prksWorkNotesMobileSideActive(ctx) ? 'vertical' : 'horizontal');
    handle.setAttribute(
        'aria-label',
        prksWorkNotesMobileSideActive(ctx)
            ? 'Drag to resize research notes panel width'
            : 'Drag to resize research notes panel height'
    );
    prksSyncWorkNotesSplitAria(ws, handle, prksWorkNotesMobileSideActive(ctx));

    requestAnimationFrame(() => {
        const _mde = prksWorkNotesEditor(ctx);
        if (_mde && _mde.codemirror) {
            _mde.codemirror.refresh();
        }
    });
}

window.prksReapplyWorkNotesSplitLayout = prksReapplyWorkNotesSplitLayout;

function prksClampWorkNotesHeight(ws, handle, px) {
    const rect = ws.getBoundingClientRect();
    const handleH = handle.offsetHeight || 11;
    const minPdf = 120;
    const minNotes = 160;
    const maxH = Math.max(minNotes, rect.height - minPdf - handleH);
    return Math.max(minNotes, Math.min(maxH, px));
}

function prksClampWorkNotesSideWidth(ws, handle, px) {
    const rect = ws.getBoundingClientRect();
    const handleW = handle.offsetWidth || 16;
    const minPdf = 120;
    const minNotes = 160;
    const maxW = Math.max(minNotes, rect.width - minPdf - handleW);
    return Math.max(minNotes, Math.min(maxW, px));
}

function setupWorkNotesSplitResize(ctx, workId) {
    const ws = ctx && ctx.query ? ctx.query('.work-workspace[data-work-id="' + workId + '"]') : null;
    const handle = ws && ws.querySelector('.work-split-handle');
    const notesPane = ws && ws.querySelector('.work-notes-pane');
    if (!ws || !handle || !notesPane) return;

    const storageKeyH = 'prks.workNotesHeight.' + workId;
    const storageKeyW = 'prks.workNotesSideWidth.' + workId;

    const savedH = localStorage.getItem(storageKeyH);
    let initialH = 320;
    if (savedH) {
        const n = parseInt(savedH, 10);
        if (!Number.isNaN(n) && n >= 160 && n <= 900) initialH = n;
    }
    const savedW = localStorage.getItem(storageKeyW);
    let initialW = 280;
    if (savedW) {
        const n = parseInt(savedW, 10);
        if (!Number.isNaN(n) && n >= 160 && n <= 600) initialW = n;
    }

    if (prksWorkNotesMobileSideActive(ctx)) {
        ws.style.setProperty('--work-notes-width', prksClampWorkNotesSideWidth(ws, handle, initialW) + 'px');
    } else {
        ws.style.setProperty('--work-notes-height', prksClampWorkNotesHeight(ws, handle, initialH) + 'px');
    }
    handle.setAttribute('aria-orientation', prksWorkNotesMobileSideActive(ctx) ? 'vertical' : 'horizontal');
    handle.setAttribute(
        'aria-label',
        prksWorkNotesMobileSideActive(ctx)
            ? 'Drag to resize research notes panel width'
            : 'Drag to resize research notes panel height'
    );
    prksSyncWorkNotesSplitAria(ws, handle, prksWorkNotesMobileSideActive(ctx));

    function refreshNotesEditor() {
        const _mde = prksWorkNotesEditor(ctx);
        if (_mde && _mde.codemirror) {
            _mde.codemirror.refresh();
        }
    }

    handle.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const side = ws.classList.contains('work-workspace--side');
        let dragging = true;
        const pointerId = e.pointerId;
        const startX = e.clientX;
        const startY = e.clientY;
        const startNw = notesPane.getBoundingClientRect().width;
        const startNh = notesPane.getBoundingClientRect().height;
        let unregisterCleanup = function () {};
        handle.classList.add('dragging');
        try {
            handle.setPointerCapture(pointerId);
        } catch (_err) {}

        function onMove(ev) {
            if (!dragging) return;
            ev.preventDefault();
            if (side) {
                const delta = ev.clientX - startX;
                const next = prksClampWorkNotesSideWidth(ws, handle, startNw - delta);
                ws.style.setProperty('--work-notes-width', next + 'px');
            } else {
                const delta = ev.clientY - startY;
                const next = prksClampWorkNotesHeight(ws, handle, startNh - delta);
                ws.style.setProperty('--work-notes-height', next + 'px');
            }
            prksSyncWorkNotesSplitAria(ws, handle, side);
            refreshNotesEditor();
        }

        function endDrag() {
            if (!dragging) return;
            dragging = false;
            unregisterCleanup();
            handle.classList.remove('dragging');
            document.removeEventListener('pointermove', onMove, true);
            document.removeEventListener('pointerup', endDrag, true);
            document.removeEventListener('pointercancel', endDrag, true);
            handle.removeEventListener('lostpointercapture', endDrag);
            try {
                if (handle.hasPointerCapture && handle.hasPointerCapture(pointerId)) {
                    handle.releasePointerCapture(pointerId);
                }
            } catch (_err2) {}
            if (side) {
                const raw = ws.style.getPropertyValue('--work-notes-width').trim();
                const w = parseInt(raw, 10);
                if (!Number.isNaN(w)) localStorage.setItem(storageKeyW, String(w));
            } else {
                const raw = ws.style.getPropertyValue('--work-notes-height').trim();
                const h = parseInt(raw, 10);
                if (!Number.isNaN(h)) localStorage.setItem(storageKeyH, String(h));
            }
            refreshNotesEditor();
        }

        if (ctx && typeof ctx.registerCleanup === 'function') unregisterCleanup = ctx.registerCleanup(endDrag);
        handle.addEventListener('lostpointercapture', endDrag);
        document.addEventListener('pointermove', onMove, true);
        document.addEventListener('pointerup', endDrag, true);
        document.addEventListener('pointercancel', endDrag, true);
    });

    handle.addEventListener('keydown', (e) => {
        const step = e.shiftKey ? 24 : 10;
        const side = ws.classList.contains('work-workspace--side');
        if (side) {
            const raw = ws.style.getPropertyValue('--work-notes-width').trim();
            const cur = parseInt(raw, 10) || initialW;
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                e.preventDefault();
                const delta = e.key === 'ArrowLeft' ? step : -step;
                const next = prksClampWorkNotesSideWidth(ws, handle, cur + delta);
                ws.style.setProperty('--work-notes-width', next + 'px');
                localStorage.setItem(storageKeyW, String(next));
                prksSyncWorkNotesSplitAria(ws, handle, side);
                refreshNotesEditor();
            }
        } else {
            const raw = ws.style.getPropertyValue('--work-notes-height').trim();
            const cur = parseInt(raw, 10) || initialH;
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault();
                const delta = e.key === 'ArrowUp' ? step : -step;
                const next = prksClampWorkNotesHeight(ws, handle, cur + delta);
                ws.style.setProperty('--work-notes-height', next + 'px');
                localStorage.setItem(storageKeyH, String(next));
                prksSyncWorkNotesSplitAria(ws, handle, side);
                refreshNotesEditor();
            }
        }
    });

    /* The one split-view observer for this route. registerCleanup ties it to
     * the route: warm park keeps it on the parked pane; beginRoute, cold park,
     * and destroy disconnect it. */
    if (typeof ResizeObserver === 'function') {
        const ro = new ResizeObserver(function () {
            prksReapplyWorkNotesSplitLayout(ctx);
        });
        try {
            ro.observe(ws);
        } catch (_e) {}
        if (ctx && typeof ctx.registerCleanup === 'function') {
            ctx.registerCleanup(function () {
                try {
                    ro.disconnect();
                } catch (_e2) {}
            });
        }
    }

    if (!window.__prksWorkNotesViewportBound) {
        window.__prksWorkNotesViewportBound = true;
        window.addEventListener('resize', function () {
            if (typeof prksForEachMountedTabContext !== 'function') return;
            prksForEachMountedTabContext(function (c) {
                if (typeof prksReapplyWorkNotesSplitLayout === 'function') prksReapplyWorkNotesSplitLayout(c);
            });
        });
    }

    requestAnimationFrame(() => {
        refreshNotesEditor();
        const activeNotesPane = ws.querySelector('.work-notes-pane');
        if (!activeNotesPane) return;
        const nRect = activeNotesPane.getBoundingClientRect();
        const host = ws.closest('.prks-tile__body') || ws;
        const hostRect = host.getBoundingClientRect();
        const isOutOfViewport = nRect.bottom > hostRect.bottom + 1 || nRect.top < hostRect.top - 1;
        const collapsed = ws.classList.contains('work-workspace--notes-collapsed');
        const narrowPx =
            typeof window.PRKS_WORKSPACE_NARROW_PX === 'number' && window.PRKS_WORKSPACE_NARROW_PX > 0
                ? window.PRKS_WORKSPACE_NARROW_PX
                : 720;
        const isSmall =
            typeof window.prksWorkspaceWidthIsNarrow === 'function'
                ? !!window.prksWorkspaceWidthIsNarrow(ws.clientWidth)
                : ws.clientWidth > 0 && ws.clientWidth <= narrowPx;
        if (collapsed && isOutOfViewport && isSmall) {
            ws.classList.remove('work-workspace--notes-collapsed');
            try {
                localStorage.setItem('prks.workNotesCollapsed.' + workId, '0');
            } catch (_e) {}
            requestAnimationFrame(() => refreshNotesEditor());
        }
    });
}

function setupWorkNotesCollapseToggle(ctx, workId) {
    const ws = ctx && ctx.query ? ctx.query('.work-workspace[data-work-id="' + workId + '"]') : null;
    const btn = ctx && ctx.query ? ctx.query('[data-prks-role="work-notes-collapse-btn"]') : null;
    if (!ws || !btn) return;

    const storageKey = 'prks.workNotesCollapsed.' + workId;

    function syncToggleUi() {
        const collapsed = ws.classList.contains('work-workspace--notes-collapsed');
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        const side = typeof prksWorkNotesMobileSideActive === 'function' && prksWorkNotesMobileSideActive(ctx);
        if (side) {
            btn.setAttribute(
                'aria-label',
                collapsed ? 'Expand research notes panel' : 'Collapse research notes panel'
            );
            btn.title = collapsed ? 'Expand notes' : 'Collapse notes';
        } else {
            btn.setAttribute(
                'aria-label',
                collapsed ? 'Expand research notes editor' : 'Collapse research notes editor'
            );
            btn.title = collapsed ? 'Expand notes' : 'Collapse notes';
        }
    }

    function setCollapsed(collapsed) {
        ws.classList.toggle('work-workspace--notes-collapsed', collapsed);
        localStorage.setItem(storageKey, collapsed ? '1' : '0');
        if (typeof prksReapplyWorkNotesSplitLayout === 'function') {
            prksReapplyWorkNotesSplitLayout(ctx);
        }
        syncToggleUi();
        requestAnimationFrame(() => {
            const _mdeC = prksWorkNotesEditor(ctx);
            if (_mdeC && _mdeC.codemirror) {
                _mdeC.codemirror.refresh();
            }
        });
    }

    syncToggleUi();
    if (ctx && typeof ctx.setResource === 'function') ctx.setResource('workNotesCollapseSync', syncToggleUi);
    btn.addEventListener('click', () => setCollapsed(!ws.classList.contains('work-workspace--notes-collapsed')));
}

const copyBibTeXResetTimers = new WeakMap();

async function prksCopyBibTeXToClipboard(text) {
    const s = text == null ? '' : String(text);
    if (!s) return;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(s);
        return 'navigator.clipboard';
    }
    const ta = document.createElement('textarea');
    ta.value = s;
    ta.setAttribute('readonly', 'readonly');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '-9999px';
    document.body.appendChild(ta);
    let ok = false;
    try {
        ta.focus();
        ta.select();
        ok = !!document.execCommand('copy');
    } finally {
        document.body.removeChild(ta);
    }
    if (!ok) throw new Error('document.execCommand copy failed');
    return 'execCommand';
}

async function copyBibTeX(workId, btn) {
    if (!btn) return;
    const snapshotIfNeeded = () => {
        if (btn.dataset.copyBibtexOriginal == null) {
            btn.dataset.copyBibtexOriginal = btn.innerHTML;
            btn.dataset.copyBibtexStyle = btn.getAttribute('style') || '';
        }
    };
    const restore = () => {
        if (btn.dataset.copyBibtexOriginal != null) {
            btn.innerHTML = btn.dataset.copyBibtexOriginal;
            delete btn.dataset.copyBibtexOriginal;
        }
        if (btn.dataset.copyBibtexStyle != null) {
            btn.setAttribute('style', btn.dataset.copyBibtexStyle);
            delete btn.dataset.copyBibtexStyle;
        }
    };
    const previousTimer = copyBibTeXResetTimers.get(btn);
    if (previousTimer) {
        clearTimeout(previousTimer);
        copyBibTeXResetTimers.delete(btn);
        restore();
    }
    try {
        const res = await prksRequest('/api/bibtex/' + encodeURIComponent(workId));
        if (!res.ok) throw new Error('bibtex fetch failed');
        const text = await res.text();
        await prksCopyBibTeXToClipboard(text);
        snapshotIfNeeded();
        btn.innerHTML =
            (typeof prksIcon === 'function' ? prksIcon('check', { size: 'sm' }) : '') + ' BibTeX copied!';
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(btn);
        btn.style.color = '#16a34a';
        btn.style.borderColor = '#16a34a';
        const resetTimer = setTimeout(() => {
            restore();
            copyBibTeXResetTimers.delete(btn);
        }, 2500);
        copyBibTeXResetTimers.set(btn, resetTimer);
    } catch (e) {
        snapshotIfNeeded();
        btn.innerHTML =
            (typeof prksIcon === 'function' ? prksIcon('x', { size: 'sm' }) : '') + ' Copy failed';
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(btn);
        btn.style.color = '#ef4444';
        btn.style.borderColor = '#ef4444';
        const resetTimer = setTimeout(() => {
            restore();
            copyBibTeXResetTimers.delete(btn);
        }, 2500);
        copyBibTeXResetTimers.set(btn, resetTimer);
    }
}

/** Wire Copy BibTeX / Delete File in #panel-content (right column Details tab). */
function initWorkDetailRightPanelActions(work, ownerCtx) {
    const panel = document.getElementById('panel-content');
    if (ownerCtx && typeof prksRightPanelOwnedBy === 'function' && !prksRightPanelOwnedBy(ownerCtx, panel)) return;
    if (!panel || !work || !work.id) return;
    const copyBtn = panel.querySelector('.copy-bibtex-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => void copyBibTeX(work.id, copyBtn));
    }
    const delBtn = panel.querySelector('.delete-work-btn');
    if (delBtn) {
        delBtn.addEventListener('click', () => void deleteWork(work.id, ownerCtx));
    }
}

/* Node selftests import the pure note-editor helpers directly (see
 * tests/browser/run_wiki_link_autocomplete_selftest.js). Inert in the browser,
 * where `module` is undefined and these stay ordinary file-scope functions. */
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        prksGetWikiLinkAutocompleteContext,
        prksFilterWorksForWikiHint,
    };
}
