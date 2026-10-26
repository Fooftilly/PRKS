#!/usr/bin/env node
'use strict';

/* Exercise the real role-modal preparation function without a network server.
 * Both catalog fetches must begin immediately; neither is dependent on the
 * other's completion. Selection/fallback behavior must remain unchanged. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ui = fs.readFileSync(
    path.resolve(__dirname, '../../frontend/js/ui.js'), 'utf8');
const begin = ui.indexOf('async function prepareRoleModal()');
const end = ui.indexOf('function prksSetTabActive(', begin);
assert(begin >= 0 && end > begin, 'role modal preparation function exists');
const source = ui.slice(begin, end) + '\nprepareRoleModal;';

function deferred() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return { promise, resolve };
}

function harness(config = {}) {
    const people = deferred();
    const works = deferred();
    const started = [];
    const initialized = [];
    const fields = new Map([
        'role-person-search', 'role-person-id', 'role-work-search',
        'role-work-id', 'role-type',
    ].map(id => [id, { value: id === 'role-type' ? 'Author' : '' }]));
    let fallbackPeople = 0;
    let fallbackWorks = 0;
    const globals = {
        Promise,
        window: { location: { hash: config.personRoute ? '#/people/P1' : '#/works/W1' } },
        document: { getElementById: id => fields.get(id) || null },
        prksOfflinePeopleFetch: () => {
            started.push('people');
            return people.promise;
        },
        prksOfflineWorksBrowseFetch: () => {
            started.push('works');
            return works.promise;
        },
        fetchPersons: async () => {
            fallbackPeople += 1;
            return [{ id: 'fallback-person', first_name: 'Fallback', last_name: 'Person' }];
        },
        fetchWorks: async () => {
            fallbackWorks += 1;
            return [{ id: 'fallback-work', title: 'Fallback Work' }];
        },
        initSearchableCombobox: (input, results, hidden, kind) => {
            initialized.push([input, results, hidden, kind]);
        },
        prksBindRoleCreditPicker: () => {},
        prksMountLinkRoleSegmented: () => {},
        prksRefreshRoleCreditPicker: () => {},
        prksParseRoute: () => config.personRoute
            ? { name: 'person', params: { personId: 'P1' } }
            : { name: 'work', params: { workId: 'W1' } },
        prksFocusedEntity: () => ({ id: 'W1', title: 'Current Work' }),
        personDisplayName: p => p.first_name + ' ' + p.last_name,
    };
    const prepare = vm.runInNewContext(source, globals);
    return {
        prepare, people, works, started, initialized, fields, globals,
        fallbackCounts: () => [fallbackPeople, fallbackWorks],
    };
}

async function concurrentSuccessfulCatalogs() {
    const h = harness();
    let completed = false;
    const pending = h.prepare().then(() => { completed = true; });
    assert.deepEqual(h.started, ['people', 'works'],
        'both validated catalogs must begin before either resolves');
    h.people.resolve({ value: [{ id: 'P1', first_name: 'Ada', last_name: 'Offline' }] });
    await Promise.resolve();
    assert.equal(completed, false, 'modal stays inert until both catalogs resolve');
    h.works.resolve({ value: [{ id: 'W1', title: 'Work One' }] });
    await pending;
    assert.equal(h.globals.allPersons.length, 1);
    assert.equal(h.globals.allPersons[0].id, 'P1');
    assert.equal(h.globals.allWorks.length, 1);
    assert.equal(h.fields.get('role-work-id').value, 'W1');
    assert.equal(h.fields.get('role-work-search').value, 'Work One');
    assert.equal(h.fields.get('role-person-id').value, '');
    assert.deepEqual(h.fallbackCounts(), [0, 0]);
    assert.equal(h.initialized.length, 2, 'both comboboxes initialize once');
}

async function independentFallbackAndFocusedWork() {
    const h = harness();
    const pending = h.prepare();
    h.works.resolve({ source: 'unavailable', value: null });
    h.people.resolve({ source: 'unavailable', value: null });
    await pending;
    assert.deepEqual(h.fallbackCounts(), [1, 1], 'existing fallbacks preserved');
    assert.equal(h.fields.get('role-work-id').value, 'W1',
        'a missing catalog still preselects the focused Work');
    assert.equal(h.fields.get('role-work-search').value, 'Current Work');
    assert.equal(h.globals.allPersons[0].id, 'fallback-person');
    assert.equal(h.initialized.length, 2);
}

async function personRoutePreselection() {
    const h = harness({ personRoute: true });
    const pending = h.prepare();
    h.works.resolve({ value: [] });
    h.people.resolve({ value: [{ id: 'P1', first_name: 'Ada', last_name: 'Offline' }] });
    await pending;
    assert.equal(h.fields.get('role-person-id').value, 'P1');
    assert.equal(h.fields.get('role-person-search').value, 'Ada Offline');
    assert.equal(h.fields.get('role-work-id').value, '');
    assert.deepEqual(h.fallbackCounts(), [0, 0]);
}

async function main() {
    await concurrentSuccessfulCatalogs();
    await independentFallbackAndFocusedWork();
    await personRoutePreselection();
    console.log('Role modal parallel catalog checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
