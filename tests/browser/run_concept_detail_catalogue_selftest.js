#!/usr/bin/env node
'use strict';

/* Exercise the real app.js Concept detail helpers in isolation. Their
 * catalogue read is the costly exception: no parent-set intent means the
 * acknowledged detail already has names for its parents and children.
 * These tests check that dropping the read cannot drop the pending overlay,
 * Work references, stale-route checks, or pending hierarchy names. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/app.js'), 'utf8');
const start = source.indexOf('function prksConceptDetailNeedsCatalogue(');
const end = source.indexOf('\n/**\n * Re-read this owner', start);
assert.ok(start > 0 && end > start, 'Concept detail functions must exist in app.js');
const helpers = source.slice(start, end);

const conceptOp = (operation, status = 'pending') => ({
    entity_type: 'concept', operation, status, entity_id: 'C-child',
    payload: { parent_ids: ['C-parent'] },
});

async function check(operations, catalogueExpected, staleAfter = null) {
    const calls = [];
    const catalogue = [{ id: 'C-parent', name: 'Live Parent' }];
    const sandbox = {
        prksHydratePendingWorkMetadata: async () => { calls.push('hydrate'); },
        prksEffectiveConceptCatalogue: async ops => {
            assert.strictEqual(ops, operations);
            calls.push('catalogue');
            return catalogue;
        },
        prksEffectiveConceptDetail: (item, ops, rows) => {
            assert.strictEqual(ops, operations);
            assert.equal(item.id, 'C-child');
            assert.equal(rows.length, catalogueExpected ? 1 : 0);
            calls.push('detail');
            return { id: item.id, parents: rows };
        },
        prksEffectiveWorkReferences: (kind, detail) => {
            assert.equal(kind, 'concept');
            calls.push('work-refs');
            return detail;
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(helpers, sandbox, { filename: 'app.js:ConceptDetailHelpers' });
    assert.equal(sandbox.prksConceptDetailNeedsCatalogue(operations), catalogueExpected);
    let checks = 0;
    const stale = () => {
        checks++;
        return staleAfter !== null && checks >= staleAfter;
    };
    const result = await sandbox.prksEffectiveConceptDetailView(
        { id: 'C-child', parents: [] }, operations, stale,
    );
    if (staleAfter !== null) {
        assert.equal(result, null);
        const expected = ['hydrate'];
        if (staleAfter > 1 && catalogueExpected) expected.push('catalogue');
        assert.deepEqual(calls, expected);
    } else {
        assert.equal(result.id, 'C-child');
        assert.deepEqual(calls,
            catalogueExpected
                ? ['hydrate', 'catalogue', 'detail', 'work-refs']
                : ['hydrate', 'detail', 'work-refs']);
    }
}

(async () => {
    // The common case has no hierarchy edit, and ordinary unsent identity,
    // description, create and deletion operations must preserve this fast path.
    await check([], false);
    await check(null, false);
    for (const op of [
        conceptOp('CREATE_CONCEPT'),
        conceptOp('SET_CONCEPT_FIELD'),
        conceptOp('SET_CONCEPT_IDENTITY'),
        conceptOp('DELETE_CONCEPT'),
        conceptOp('SET_CONCEPT_PARENTS', 'acknowledged'),
        { ...conceptOp('SET_CONCEPT_PARENTS'), entity_type: 'position' },
    ]) await check([op], false);
    // An unsettled hierarchy intent can name a parent/child that is not
    // present on the acknowledged Concept detail, so it must read the list.
    await check([conceptOp('SET_CONCEPT_PARENTS')], true);
    await check([conceptOp('SET_CONCEPT_PARENTS', 'conflict')], true);
    await check([conceptOp('SET_CONCEPT_PARENTS', 'queued')], true);
    await check([conceptOp('SET_CONCEPT_FIELD'), conceptOp('SET_CONCEPT_PARENTS')], true);
    // A stale owner must not continue work after either await.
    await check([], false, 1);
    await check([conceptOp('SET_CONCEPT_PARENTS')], true, 2);
    console.log('concept detail catalogue fast-path checks passed');
})().catch(err => {
    console.error(err);
    process.exitCode = 1;
});
