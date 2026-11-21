#!/usr/bin/env node
'use strict';

/**
 * Folder Tag options for a Folder this device created but the server has not
 * acknowledged: answered locally (no Tags, no relation revisions) instead of a
 * server read that can only 404. Acknowledged Folders still read the server.
 * No vm sandbox — Sonar S1523.
 */

const assert = require('assert');
const path = require('path');
const rootDir = path.resolve(__dirname, '../..');

globalThis.window = globalThis;
let operations = [];
const reads = [];
globalThis.prksSync = { store: { listOperations: async () => operations } };
globalThis.prksOfflineReadEntity = async function (kind, id, url) {
    reads.push(url);
    return { value: { folder_id: id, assigned: [{ tag_id: 'tag-1', relation_revision: 2 }], known_absent: {} },
        source: 'network', cachedAt: null };
};
globalThis.prksOfflineInvalidateEntity = async function () {};
require(path.join(rootDir, 'frontend/js/folder-tag-state.js'));
require(path.join(rootDir, 'frontend/js/folder-state.js'));

function createOp(id, status) {
    return { entity_type: 'folder', entity_id: id, operation: 'CREATE_FOLDER', status, payload: { title: 'New' } };
}

(async function main() {
    operations = [createOp('F-NEW', 'pending')];
    const pending = await globalThis.prksReadFolderTagOptions('F-NEW');
    assert.deepStrictEqual(pending.value, { folder_id: 'F-NEW', assigned: [], known_absent: {} });
    assert.ok(globalThis.prksIsFolderTagOptionsShape(pending.value, 'F-NEW'));
    assert.deepStrictEqual(reads, [], 'a pending-create Folder must not be read from the server');
    assert.deepStrictEqual(globalThis.prksFolderTagBase(pending.value, 'tag-1'), { present: false, revision: 0 });

    operations = [createOp('F-NEW', 'acknowledged')];
    const acked = await globalThis.prksReadFolderTagOptions('F-NEW');
    assert.strictEqual(acked.value.assigned.length, 1);
    assert.deepStrictEqual(reads, ['/api/folders/F-NEW/tag-options']);

    operations = [createOp('F-OTHER', 'pending'), { ...createOp('F-REFUSED', 'conflict') }];
    await globalThis.prksReadFolderTagOptions('F-REFUSED');
    assert.strictEqual(reads.length, 2, 'a refused create is not a local Folder: read the server');
    console.log('folder tag options selftest: OK');
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
