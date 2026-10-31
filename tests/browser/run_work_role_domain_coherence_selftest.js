"use strict";
/* Production Work-role / Person-biography cache invalidation coverage.
 * Relocated from browser cases that called explicit cache-coherence helpers:
 * the actual UI/offline/real-server boundaries remain tested in Chromium.
 *
 * These cases evaluate the SHIPPED api.js helpers and the SHIPPED offline
 * runtime's domain marking / physical cache sweeps, not duplicate policy.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const entities = new Map();
const lists = new Map();
const key = (kind, id) => kind + ":" + id;
const store = {
    getEntity: async (kind, id) => entities.get(key(kind, id)) || null,
    putEntity: async (kind, id, value, rev) => {
        entities.set(key(kind, id), {value, cachedAt: 1, sourceRevision: rev});
        return true;
    },
    deleteEntity: async (kind, id) => { entities.delete(key(kind, id)); return true; },
    deleteEntitiesByKind: async kind => {
        for (const id of entities.keys()) if (id.startsWith(kind + ":")) entities.delete(id);
        return true;
    },
    getList: async name => lists.get(name) || null,
    putList: async (name, value, rev) => {
        lists.set(name, {value, cachedAt: 1, sourceRevision: rev});
        return true;
    },
    deleteList: async name => { lists.delete(name); return true; },
};
globalThis.createPrksOfflineStore = () => store;
// The module's canonical singleton captures the cache above on startup.
const offline = require("../../frontend/js/offline-runtime.js");
const source = fs.readFileSync(path.join(__dirname, "../../frontend/js/api.js"), "utf8");
const helpers = [
    "prksMarkPersonGroupsDomainChanged", "prksMarkPeopleDomainChanged",
    "prksMarkArgumentsDomainChanged", "prksMarkFoldersDomainChanged",
    "prksMarkWorksBrowseChanged", "prksMarkRecentChanged",
    "prksMarkRecentlyAddedChanged", "prksMarkWorkBrowseDisplayChanged",
    "prksMarkWorkRoleDependenciesChanged", "prksMarkWorkRoleChanged",
    "prksMarkWorkAuthorDisplayChanged",
];
function productionFunction(name) {
    const marker = "function " + name + "(";
    const at = source.indexOf(marker);
    assert.notEqual(at, -1, "missing shipped API helper " + name);
    assert.equal(source.indexOf(marker, at + marker.length), -1,
        "ambiguous API helper " + name);
    const end = source.indexOf("\n}", at);
    assert.notEqual(end, -1, "unclosed API helper " + name);
    return source.slice(at, end + 2);
}
const api = new Function(helpers.map(productionFunction).join("\n") +
    "\nreturn {" + helpers.join(", ") + "};")();
const domains = [
    ["people", "person", "people:index"],
    ["person-groups", "person-group", "person-groups:index"],
    ["arguments", "argument", "arguments:index"],
    ["folders", "folder", "folders:index"],
    ["works-browse", null, "works-browse:index"],
    ["recent", null, "recent:index"],
    ["recently-added", null, "recently-added:index"],
    ["positions", "position", "positions:index"],
    ["concepts", "concept", "concepts:index"],
    ["playlists", "playlist", "playlists:index"],
];
const entity = {id: "ROW", name: "Authoritative cached data"};
const work = {id: "W-1", title: "Article"};
async function reset() {
    entities.clear(); lists.clear();
    await store.putEntity("work", work.id, {...work}, "");
    for (const [, kind, listKey] of domains) {
        if (kind) await store.putEntity(kind, entity.id, {...entity}, "");
        await store.putList(listKey, [{...entity}], "");
    }
}
function generations() {
    return Object.fromEntries(domains.map(([name]) =>
        [name, offline.prksOfflineDomainGeneration(name)]));
}
async function verify(label, before, changes, evictWork) {
    // Sweeps are async, but the runtime blocks the domain synchronously.
    await new Promise(resolve => setImmediate(resolve));
    for (const [domain, kind, listKey] of domains) {
        const changed = changes.includes(domain);
        assert.equal(offline.prksOfflineDomainGeneration(domain) - before[domain],
            changed ? 1 : 0, label + ": generation of " + domain);
        assert.equal(offline.prksOfflineIsDomainBlocked(domain), false,
            label + ": cleanup completed for " + domain);
        assert.equal(await store.getList(listKey) === null, changed,
            label + ": list " + listKey + " cache eligibility");
        if (kind) assert.equal(await store.getEntity(kind, entity.id) === null,
            changed, label + ": entity kind " + kind + " cache eligibility");
    }
    assert.equal(await store.getEntity("work", work.id) === null, evictWork,
        label + ": preserve or evict Work snapshot correctly");
}
async function role(role, expected, evict) {
    await reset();
    const before = generations();
    if (evict) {
        api.prksMarkWorkRoleChanged(work.id, role);
    } else {
        api.prksMarkWorkRoleDependenciesChanged(role);
    }
    await verify("role " + role + (evict ? " legacy hook" : " ACK dependencies"),
        before, expected, evict);
}
const EVERY_ROLE = ["people", "person-groups"];
const DISPLAY = ["folders", "works-browse", "recent", "recently-added"];
async function main() {
    for (const roleName of ["Author", "Editor", "Reviewer", "Translator", ""]) {
        const expected = EVERY_ROLE.concat(
            roleName === "Author" ? ["arguments", ...DISPLAY] :
            roleName === "Editor" ? DISPLAY : []);
        await role(roleName, expected, false);
        await role(roleName, expected, true);
    }
    // The original deprecated author-display wrapper must not become a
    // second policy owner or invalidate a Work twice.
    await reset();
    const before = generations();
    api.prksMarkWorkAuthorDisplayChanged(work.id, "Author");
    await verify("legacy author-display delegate", before,
        [...EVERY_ROLE, "arguments", ...DISPLAY], true);

    // Biography-only changes change the People read model, not Work card
    // credits. Folder index, Folder details and all other domains survive.
    await reset();
    const bioBefore = generations();
    api.prksMarkPeopleDomainChanged();
    await verify("biography-only People invalidation", bioBefore, ["people"], false);

    // If a future refactor silently turns the cache cleanup into a no-op,
    // the expected changed-domain assertions must fail, not pass vacuously.
    console.log("All Work-role and biography domain-coherence checks passed");
}
main().catch(error => {console.error(error); process.exitCode = 1;});
