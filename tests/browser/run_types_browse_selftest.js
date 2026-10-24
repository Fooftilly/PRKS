#!/usr/bin/env node
'use strict';

const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', '..');
globalThis.window = globalThis;
;(0, eval)(fs.readFileSync(path.join(root, 'frontend/js/doc-types.js'), 'utf8'))
;(0, eval)(fs.readFileSync(path.join(root, 'frontend/js/components/types.js'), 'utf8'))

let passed = 0
function check(name, fn) {
    fn()
    passed += 1
    process.stdout.write('ok ' + name + '\n')
}

check('index groups unknown and blank types into misc and drops zeros', () => {
    const model = prksTypesIndexModel([
        { doc_type: 'book', title: 'B' },
        { doc_type: 'BOOK', title: 'B2' },
        { doc_type: '', title: 'Blank' },
        { doc_type: 'not-a-type', title: 'Weird' },
        { doc_type: 'article', title: 'A' },
    ])
    assert.deepEqual(model.rows.map((row) => [row.value, row.count, row.label]), [
        ['book', 2, 'Book'],
        ['misc', 2, 'Misc'],
        ['article', 1, 'Article'],
    ])
    assert.equal(model.typeCount, 3)
    assert.equal(model.totalFiles, 5)
})

check('index sorts equal counts by label', () => {
    const model = prksTypesIndexModel([
        { doc_type: 'misc' },
        { doc_type: 'article' },
        { doc_type: 'book' },
    ])
    assert.deepEqual(model.rows.map((row) => row.label), ['Article', 'Book', 'Misc'])
})

check('index ignores a non-array and does not mutate the input', () => {
    assert.deepEqual(prksTypesIndexModel(null), { rows: [], typeCount: 0, totalFiles: 0 })
    const works = [{ doc_type: 'online', title: 'O' }]
    prksTypesIndexModel(works)
    assert.deepEqual(works, [{ doc_type: 'online', title: 'O' }])
})

check('detail normalizes the route segment and sorts titles', () => {
    const model = prksTypesDetailModel([
        { id: '2', title: 'zeta', doc_type: 'Book' },
        { id: '1', title: 'Ada', doc_type: 'book' },
        { id: '3', title: 'Other', doc_type: 'article' },
        { id: '4', title: 'blank', doc_type: '' },
    ], 'BOOK')
    assert.equal(model.docType, 'book')
    assert.equal(model.label, 'Book')
    assert.equal(model.workCount, 2)
    assert.deepEqual(model.works.map((row) => row.id), ['1', '2'])
})

check('an unknown type detail is misc', () => {
    const model = prksTypesDetailModel([
        { id: 'm', title: 'M', doc_type: 'nope' },
        { id: 'a', title: 'A', doc_type: 'article' },
    ], 'nope')
    assert.equal(model.docType, 'misc')
    assert.equal(model.label, 'Misc')
    assert.deepEqual(model.works.map((row) => row.id), ['m'])
})

process.stdout.write(passed + ' passed, 0 failed\n')
