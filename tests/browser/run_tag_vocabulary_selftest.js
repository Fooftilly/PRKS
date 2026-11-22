"use strict";
/* The Tag VOCABULARY: creating one, deleting one, and what each means for the
 * relationships around it.
 *
 * The Work-Tag relationship has been durable since Phase 2. What this covers is
 * the other half: a Tag minted on this device is attachable immediately and the
 * attachment waits for it, and deleting a Tag reasons about every relationship
 * intent that names it rather than leaving the server work the next operation
 * destroys.
 */
const strict = require("assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/work-tag-state.js");
require("../../frontend/js/tag-vocabulary-state.js");

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: createFakeIndexedDBFactory(), uuid });

const rowsFor = async (store, operation) =>
    (await store.listOperations()).filter(r => r.operation === operation);

function tagAck(op) {
    return { code: "ACKNOWLEDGED", tag_id: op.entity_id, changed: true,
        tag: { id: op.entity_id, name: op.payload.name, color: op.payload.color } };
}
function relationAck(op) {
    return { code: "ACKNOWLEDGED", work_id: op.entity_id, tag_id: op.payload.tag_id,
        present: op.operation === "ADD_WORK_TAG", changed: true, server_revision: 1,
        tag: { id: op.payload.tag_id, name: "Epistemology", color: "#6d6cf7" } };
}

function runtimeFor(store, respond) {
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    return globalThis.createPrksSyncRuntime({
        store, online: () => true, request: respond,
        handlers: {
            CREATE_TAG: silent(globalThis.prksTagCreateSyncHandler),
            DELETE_TAG: silent(globalThis.prksTagDeleteSyncHandler),
            MERGE_TAG: silent(globalThis.prksTagMergeSyncHandler),
            ADD_WORK_TAG: silent(globalThis.prksWorkTagSyncHandler),
            REMOVE_WORK_TAG: silent(globalThis.prksWorkTagSyncHandler),
        },
    });
}

/* ---- construction ---- */

async function aTagIsAttachableTheMomentItIsCreated() {
    const store = newStore();
    const created = await store.createTag({ name: "Epistemology" }, []);
    assert.match(created.entity_id, /^T-[0-9A-F]{32}$/,
        "a permanent distributed id, minted here -- never remapped later");
    assert.equal(created.base_revision, null, "construction is not mutation");

    const ops = await store.listOperations();
    const catalogue = globalThis.prksEffectiveTagCatalogue([], ops);
    assert.equal(catalogue.length, 1);
    assert.equal(catalogue[0].name, "Epistemology");
    assert.deepEqual(catalogue[0].aliases, [], "and it is a well-formed catalogue row");

    /* The relationship waits for the Tag, by the GENERIC mechanism: the server
     * cannot attach one it has never heard of. */
    const link = await store.coalesceWorkTag("W-1", created.entity_id, true, false, 0,
        { id: created.entity_id, name: "Epistemology" });
    assert.deepEqual(link.depends_on, [created.op_id]);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === "CREATE_TAG" ? tagAck(body) : relationAck(body)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_TAG", "ADD_WORK_TAG"],
        "the Tag exists on the server before anything is attached to it");
    assert.equal((await store.listOperations()).length, 0, "and both retire");
}

async function anObviousLocalCollisionIsRefusedEarly() {
    const store = newStore();
    const known = [{ id: "T-EXISTING", name: "Epistemology", color: "#6d6cf7", aliases: [] }];
    await assert.rejects(
        () => store.createTag({ name: "epistemology" }, known),
        e => e.prksLocalStoreCode === "name_taken");
    /* But the authoritative answer stays CANONICAL: only the server sees every
     * Tag, so an empty local catalogue is not evidence the name is free. */
    const created = await store.createTag({ name: "epistemology" }, []);
    assert.equal(created.payload.name, "epistemology");
}

async function aTagNeedsAName() {
    const store = newStore();
    await assert.rejects(() => store.createTag({ name: "   " }, []),
        e => e.prksLocalStoreCode === "invalid_envelope");
}

/* ---- destruction ---- */

async function deletingCancelsTheRelationshipsItMakesPointless() {
    const store = newStore();
    await store.coalesceWorkTag("W-1", "T-1", true, false, 0, { id: "T-1", name: "X" });
    const removal = await store.deleteTag("T-1");
    assert.equal(removal.operation, "DELETE_TAG");
    assert.equal(removal.base_revision, null,
        "destruction addresses an identity, not a value");
    assert.equal((await store.listOperations()).length, 1,
        "attaching a Tag immediately before deleting it is work the delete undoes");

    /* And nothing else may be enqueued against it: the only outcome would be
     * TAG_DELETED. */
    await assert.rejects(
        () => store.coalesceWorkTag("W-2", "T-1", true, false, 0, { id: "T-1", name: "X" }),
        e => e.prksLocalStoreCode === "entity_deleted");
    assert.equal((await store.deleteTag("T-1")).op_id, removal.op_id,
        "deleting twice is one decision");
}

async function deletingATagCreatedHereFoldsItAway() {
    const store = newStore();
    const created = await store.createTag({ name: "Mistake" }, []);
    await store.coalesceWorkTag("W-1", created.entity_id, true, false, 0,
        { id: created.entity_id, name: "Mistake" });
    assert.equal(await store.deleteTag(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), [],
        "nothing about this tag ever reaches the server");
}

async function aSentAttachmentIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const link = await store.coalesceWorkTag("W-1", "T-1", true, false, 0,
        { id: "T-1", name: "X" });
    await store.updateOperationSyncState(link.op_id, { status: "syncing" });
    const removal = await store.deleteTag("T-1");
    assert.deepEqual(removal.depends_on, [link.op_id],
        "an envelope that may be on the wire stays immutable");
}

async function aPendingDeletionHidesTheTagEverywhere() {
    const store = newStore();
    await store.deleteTag("T-1");
    const ops = await store.listOperations();
    const catalogue = [
        { id: "T-1", name: "Doomed", color: "#6d6cf7", aliases: [] },
        { id: "T-2", name: "Kept", color: "#6d6cf7", aliases: [] },
    ];
    assert.deepEqual(
        globalThis.prksEffectiveTagCatalogue(catalogue, ops).map(t => t.id), ["T-2"]);
    assert.equal(catalogue.length, 2,
        "nothing is destroyed: a refusal restores it by doing nothing");
    /* And the chip goes with it: the relationship row is still in the cache,
     * and leaving the chip would show a Tag the user has already removed from
     * the vocabulary. */
    assert.deepEqual(
        globalThis.prksEffectiveTagChips(
            [{ id: "T-1", name: "Doomed" }, { id: "T-2", name: "Kept" }], ops)
            .map(t => t.id),
        ["T-2"]);
}

async function mergeRefusesWhileTheSourceIsStillNamed() {
    const store = newStore();
    await store.coalesceWorkTag("W-1", "T-1", true, false, 0, { id: "T-1", name: "X" });
    await assert.rejects(
        () => store.mergeTag("T-1", "T-2"),
        e => e.prksLocalStoreCode === "scope_busy");
    await store.deleteTag("T-1");
    /* Deletion cancelled the never-sent attach; merge still refuses while the
     * delete names the source. */
    await assert.rejects(
        () => store.mergeTag("T-1", "T-2"),
        e => e.prksLocalStoreCode === "scope_busy" || e.prksLocalStoreCode === "entity_deleted");
}

async function aPendingMergeHidesTheSourceAndBlocksAttachment() {
    const store = newStore();
    const merge = await store.mergeTag("T-1", "T-2");
    assert.equal(merge.operation, "MERGE_TAG");
    assert.equal(merge.base_revision, null);
    assert.equal(merge.payload.target_tag_id, "T-2");
    assert.equal((await store.mergeTag("T-1", "T-2")).op_id, merge.op_id,
        "merging twice is one decision");
    const ops = await store.listOperations();
    assert.deepEqual(
        globalThis.prksEffectiveTagCatalogue(
            [{ id: "T-1", name: "Source", color: "#6d6cf7", aliases: [] },
             { id: "T-2", name: "Target", color: "#6d6cf7", aliases: [] }], ops)
            .map(t => t.id),
        ["T-2"]);
    await assert.rejects(
        () => store.coalesceWorkTag("W-1", "T-1", true, false, 0, { id: "T-1", name: "X" }),
        e => e.prksLocalStoreCode === "entity_merged");
}

async function aRefusedCreationTakesItsAttachmentsDownVisibly() {
    const store = newStore();
    const created = await store.createTag({ name: "Epistemology" }, []);
    const link = await store.coalesceWorkTag("W-1", created.entity_id, true, false, 0,
        { id: created.entity_id, name: "Epistemology" });

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: false, status: 409, json: async () => ({
            code: "NAME_TAKEN", tag_id: body.entity_id, target_tag_id: "T-OTHER" }) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_TAG"], "the attachment is never attempted");
    const creation = (await store.listOperations()).find(r => r.op_id === created.op_id);
    assert.equal(creation.status, "conflict");
    assert.equal(creation.server_result.code, "NAME_TAKEN");

    await store.resolveConflict(created.op_id, null);
    const after = (await store.listOperations()).find(r => r.op_id === link.op_id);
    assert.equal(after.status, "conflict", "a decision, not a permanent wait");
    assert.equal(after.server_result.code, "DEPENDENCY_FAILED");
    assert.deepEqual(globalThis.prksEffectiveTagCatalogue([], await store.listOperations()), [],
        "and the tag is gone from what the user sees");
}

async function rapidTagCreateUsesOneDurableIdentity() {
    const ui = fs.readFileSync(path.join(__dirname, '../../frontend/js/ui.js'), 'utf8');
    const at = ui.indexOf('async function prksSubmitNewTag(');
    assert.ok(at >= 0, 'the real UI quick-create function is present');
    const end = ui.indexOf('\n}\n', at);
    assert.ok(end > at);
    const fn = ui.slice(at, end + 2);
    let release;
    const reading = new Promise(resolve => { release = resolve; });
    let creations = 0;
    let attachments = 0;
    const input = { dataset: {}, disabled: false,
        setAttribute: () => {}, removeAttribute: () => {} };
    const ctx = {
        JSON,
        fetchTags: async () => reading,
        prksCreateTagDurably: async () => {
            creations++;
            return { entity_id: 'T-NEW-' + creations };
        },
        prksAttachExistingTag: async () => { attachments++; },
        prksAlertMessage: async () => {},
        prksTagVocabularyMessage: () => 'create failed',
    };
    vm.createContext(ctx);
    vm.runInContext(fn, ctx);
    const first = ctx.prksSubmitNewTag('folder', 'F-1', 'New Topic', {}, input);
    const second = ctx.prksSubmitNewTag('folder', 'F-1', 'New Topic', {}, input);
    assert.equal(input.disabled, true, 'claim acquired before catalogue fetch');
    assert.ok(input.dataset.prksTagCreatePending);
    release([]);
    await Promise.all([first, second]);
    assert.equal(creations, 1, 'two rapid clicks mint only one Tag');
    assert.equal(attachments, 1, 'only one Folder relationship is queued');
    await ctx.prksSubmitNewTag('folder', 'F-1', 'New Topic', {}, input);
    assert.equal(creations, 1, 'a stale create option cannot submit again after success');
    await ctx.prksSubmitNewTag('folder', 'F-1', 'Another Topic', {}, input);
    assert.equal(creations, 2, 'a different name can still be created');

    const retryInput = { dataset: {}, disabled: false,
        setAttribute: () => {}, removeAttribute: () => {} };
    let tries = 0;
    ctx.prksCreateTagDurably = async () => {
        tries++;
        if (tries === 1) throw new Error('Failed local write');
        return { entity_id: 'T-RETRY' };
    };
    await ctx.prksSubmitNewTag('folder', 'F-2', 'Recover', {}, retryInput);
    assert.equal(retryInput.dataset.prksTagCreateDone, undefined,
        'a rejected local create does not permanently disable retry');
    await ctx.prksSubmitNewTag('folder', 'F-2', 'Recover', {}, retryInput);
    assert.equal(tries, 2, 'retry is permitted once the first local write failed');

    const attachInput = { dataset: {}, disabled: false,
        setAttribute: () => {}, removeAttribute: () => {} };
    const alerts = [];
    let createdTags = 0;
    ctx.prksCreateTagDurably = async () => {
        createdTags++;
        return { entity_id: 'T-ATTACH-FAILED' };
    };
    /* Load the actual wrapper: the Folder editor historically swallowed
     * local-store errors, so mocking the wrapper to throw missed the bug. */
    const attachAt = ui.indexOf('async function prksAttachExistingTag(');
    const attachEnd = ui.indexOf('\n}\n', attachAt);
    assert.ok(attachAt >= 0 && attachEnd > attachAt);
    vm.runInContext(ui.slice(attachAt, attachEnd + 2), ctx);
    ctx.prksFolderTagEdit = async () => false;
    ctx.prksAlertMessage = async (message, title) => alerts.push({ message, title });
    await ctx.prksSubmitNewTag('folder', 'F-3', 'Already Created', {}, attachInput);
    assert.equal(createdTags, 1, 'the Tag was durably created');
    assert.equal(alerts[0].title, 'Could not attach Tag');
    assert.match(alerts[0].message, /was created locally/);
    assert.match(alerts[0].message, /Select the existing Tag/);
    await ctx.prksSubmitNewTag('folder', 'F-3', 'Already Created', {}, attachInput);
    assert.equal(createdTags, 1,
        'the recovery message does not encourage creating a duplicate identity');
}

async function actualFolderTagEditorReportsQueueFailure() {
    /* Run the actual folder-tag editor, not a failure-throwing substitute.
     * The editor catches queue failures to paint its inline status. Its
     * return value must also let Create Tag report that the Tag exists but
     * its attachment failed, rather than silently acknowledging success. */
    const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/folder-tag-editor.js'),
        'utf8');
    const state = {
        folderId: 'F-1', generation: 1, operations: [],
        options: { assigned: [], known_absent: {} }, catalog: [],
        error: null,
    };
    const folder = { id: 'F-1', tags: [] };
    let queueAttempts = 0;
    const runtime = {
        store: {
            listOperations: async () => [],
            coalesceFolderTag: async () => {
                queueAttempts++;
                throw new Error('Durable write refused');
            },
        },
        changed: () => {},
    };
    const env = {
        window: null,
        prksSync: runtime,
        prksFolderTagBase: () => ({ present: false, revision: 0 }),
        prksRightPanelOwnedBy: () => false,
        prksOwnerTabIsFocused: () => false,
    };
    env.window = env;
    vm.createContext(env);
    vm.runInContext(source, env);
    const owner = {
        destroyed: false,
        generation: 1,
        getResource: kind => kind === 'folderTagEditor' ? state : null,
        getEntity: kind => kind === 'folder' ? folder : null,
    };
    const result = await env.prksFolderTagEdit(owner, 'T-NEW', true,
        { id: 'T-NEW', name: 'New Tag' });
    assert.equal(result, false, 'caught editor queue error propagates failure to caller');
    assert.equal(queueAttempts, 1);
    assert.match(state.error, /Could not save this Tag change locally/);
}

async function usedTagCloudHidesMergingSourcesOnly() {
    const app = fs.readFileSync(path.join(__dirname, '../../frontend/js/app.js'), 'utf8');
    const at = app.indexOf('async function prksEffectiveUsedTags(');
    assert.ok(at >= 0);
    const end = app.indexOf('\n}\n', at);
    assert.ok(end > at);
    const source = app.slice(at, end + 2);
    const ops = [
        { operation: 'MERGE_TAG', entity_type: 'tag', entity_id: 'T-OLD', status: 'pending' },
        { operation: 'CREATE_TAG', entity_type: 'tag', entity_id: 'T-NEW', status: 'pending' },
    ];
    const ctx = {
        Set, Array,
        prksDurableOperationsOrNone: async () => ops,
        prksPendingTagMerges: globalThis.prksPendingTagMerges,
        prksPendingTagDeletions: globalThis.prksPendingTagDeletions,
    };
    vm.createContext(ctx);
    vm.runInContext(source, ctx);
    const used = [
        { id: 'T-OLD', name: 'Old' },
        { id: 'T-STAYS', name: 'Used' },
    ];
    const shown = await ctx.prksEffectiveUsedTags(used);
    assert.deepEqual(shown.map(x => x.id), ['T-STAYS'],
        'the source disappears but an unassigned new Tag is not added');
}

/* The New File form attaches its first Tags only once their CREATE_TAG has
 * reached the server. A queue read that fails says nothing about that, so the
 * shipped wait must read again rather than take the failure as an empty queue. */
function unreadableBudget(app) {
    const found = /const PRKS_UNREADABLE_QUEUE_WAIT_MS = (\d+);/.exec(app);
    assert.ok(found, 'the shipped unreadable-queue budget');
    return Number(found[1]);
}

function tagWaitFor(store, changed) {
    const app = fs.readFileSync(path.join(__dirname, '../../frontend/js/app.js'), 'utf8');
    const source = ['prksDurableQueueForWait', 'prksWaitForCreatesOnServer', 'prksWaitForTagsOnServer']
        .map(name => {
            const marker = 'async function ' + name + '(';
            const at = app.indexOf(marker);
            assert.ok(at >= 0 && app.indexOf(marker, at + 1) < 0, 'one shipped ' + name);
            const end = app.indexOf('\n}\n', at);
            assert.ok(end > at);
            return app.slice(at, end + 2);
        }).join('\n') + '\nvar PRKS_UNREADABLE_QUEUE_WAIT_MS = ' + unreadableBudget(app) + ';';
    const ctx = {
        Set, Array, Date, Math, Promise, setTimeout,
        prksSync: store ? { store, changed } : null,
        prksPendingTagCreates: globalThis.prksPendingTagCreates,
    };
    vm.createContext(ctx);
    vm.runInContext(source, ctx);
    return ctx.prksWaitForTagsOnServer;
}

async function anUnreadableQueueIsNotTakenAsTheTagHavingArrived() {
    const queued = { op_id: 'O-1', operation: 'CREATE_TAG', entity_type: 'tag', entity_id: 'T-NEW',
        status: 'pending', payload: { name: 'New' } };
    let reads = 0;
    let nudges = 0;
    const flaky = {
        listOperations: async () => {
            reads += 1;
            if (reads === 1) throw new Error('IndexedDB read failed');
            return reads === 2 ? [queued] : [];
        },
    };
    const wait = tagWaitFor(flaky, () => { nudges += 1; });
    assert.equal(await wait(['T-NEW'], 5000), true, 'the Tag arrives once its creation retires');
    assert.equal(reads, 3, 'a failed read is read again, and a pending Tag is waited for');
    assert.ok(nudges >= 2, 'sync is nudged while the answer is unknown or pending');

    /* A queue that never becomes readable cannot be drained by sync either:
     * after the short unreadable budget the save goes ahead, and the server
     * refuses what it does not have. Before that budget it is still waited on. */
    let brokenReads = 0;
    const broken = { listOperations: async () => { brokenReads += 1; throw new Error('IndexedDB read failed'); } };
    const brokenStart = Date.now();
    assert.equal(await tagWaitFor(broken, () => {})(['P-EXISTING'], 10000), true,
        'a queue that stays unreadable lets the save go ahead');
    const tookMs = Date.now() - brokenStart;
    assert.ok(tookMs >= 1900 && tookMs < 4000, 'after the short budget, not the full deadline: ' + tookMs);
    assert.ok(brokenReads >= 5, 'having kept reading through the budget, not after one failure');

    /* Once a read succeeded, its answer stands: a later failure does not let a
     * still-pending Tag through early. */
    let laterReads = 0;
    const thenBroken = {
        listOperations: async () => {
            laterReads += 1;
            if (laterReads === 1) return [queued];
            throw new Error('IndexedDB read failed');
        },
    };
    assert.equal(await tagWaitFor(thenBroken, () => {})(['T-NEW'], 2600), false,
        'a pending Tag seen once is waited for until the deadline');

    /* The real store with no IndexedDB: its reads fail as 'unavailable'. */
    const unopenable = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: null, uuid });
    const started = Date.now();
    assert.equal(await tagWaitFor(unopenable, () => {})(['P-EXISTING'], 5000), true,
        'storage that does not exist here never queued a creation, so nothing is waited for');
    assert.ok(Date.now() - started < 1000, 'and the save does not sit out the deadline');

    /* A failed (re)open of a database that may hold the Tag is also 'unavailable',
     * but it is not absence: the next read can still find the Tag pending. */
    const failedOpen = new Error('Could not open local storage.');
    failedOpen.prksLocalStoreCode = 'unavailable';
    let reopenReads = 0;
    const reopening = {
        listOperations: async () => {
            reopenReads += 1;
            if (reopenReads === 1) throw failedOpen;
            return reopenReads === 2 ? [queued] : [];
        },
    };
    assert.equal(await tagWaitFor(reopening, () => {})(['T-NEW'], 5000), true);
    assert.equal(reopenReads, 3, 'a failed open is read again and the pending Tag is waited for');

    /* And the real store's own classification: an IndexedDB whose open throws is
     * a failed open, not absent storage, so the wait is not skipped. */
    const throwingFactory = { open: () => { throw new Error('QuotaExceededError'); } };
    const failing = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: throwingFactory, uuid });
    const failure = await failing.listOperations().then(() => null, error => error);
    assert.equal(failure && failure.prksLocalStoreCode, 'unavailable');
    assert.notEqual(failure.prksLocalStoreAbsent, true, 'a failed open is not marked absent');
    const failingStart = Date.now();
    assert.equal(await tagWaitFor(failing, () => {})(['T-NEW'], 10000), true);
    assert.ok(Date.now() - failingStart >= 1900,
        'so the real store failing to open is read through the budget, not taken as an empty queue');

    const blocked = new Error('Local storage is blocked by another tab.');
    blocked.prksLocalStoreCode = 'blocked';
    let blockedReads = 0;
    const blockedThenReadable = {
        listOperations: async () => {
            blockedReads += 1;
            if (blockedReads === 1) throw blocked;
            return blockedReads === 2 ? [queued] : [];
        },
    };
    assert.equal(await tagWaitFor(blockedThenReadable, () => {})(['T-NEW'], 5000), true);
    assert.equal(blockedReads, 3, 'a blocked open is not taken as empty: the pending Tag is waited for');

    assert.equal(await tagWaitFor(null, () => {})(['T-NEW'], 300), true,
        'without a local store nothing can be queued');
}

async function main() {
    await rapidTagCreateUsesOneDurableIdentity();
    await actualFolderTagEditorReportsQueueFailure();
    await usedTagCloudHidesMergingSourcesOnly();
    await aTagIsAttachableTheMomentItIsCreated();
    await anObviousLocalCollisionIsRefusedEarly();
    await aTagNeedsAName();
    await deletingCancelsTheRelationshipsItMakesPointless();
    await deletingATagCreatedHereFoldsItAway();
    await aSentAttachmentIsWaitedForRatherThanRewritten();
    await aPendingDeletionHidesTheTagEverywhere();
    await mergeRefusesWhileTheSourceIsStillNamed();
    await aPendingMergeHidesTheSourceAndBlocksAttachment();
    await aRefusedCreationTakesItsAttachmentsDownVisibly();
    await anUnreadableQueueIsNotTakenAsTheTagHavingArrived();
    console.log("All " + checks + " tag vocabulary checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
