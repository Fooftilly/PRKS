"""Static contracts for the People Vue route surface (#284 / #230)."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"
FEATURE = ROOT / "frontend-app" / "src" / "features" / "people"


class PeopleVueContracts(unittest.TestCase):
    def test_coordinator_owns_effective_person_projection(self):
        app = (FRONTEND / "app.js").read_text()
        index = app[app.index("case 'people': {") : app.index("case 'people-role': {")]
        role = app[app.index("case 'people-role': {") : app.index("case 'people-groups': {")]
        detail = app[app.index("case 'person': {") : app.index("default: {", app.index("case 'person': {"))]
        self.assertIn("prksOfflinePeopleFetch(routeSignal)", index)
        self.assertIn("renderPeopleList(ctx, persons, contentDiv)", index)
        self.assertIn("prksOfflinePrependBanner(contentDiv, null)", index)
        self.assertIn("prksOfflinePeopleFetch(routeSignal)", role)
        self.assertIn("unknownRole: true", role)
        self.assertIn("renderPeopleList(ctx, rolePersons, contentDiv, { roleFilter })", role)
        self.assertLess(detail.index("prksDurableOperationsOrNone()"), detail.index("prksPendingPersonDeletions"))
        self.assertLess(detail.index("prksPendingPersonCreates"), detail.index("prksOfflineDetailFetch"))
        self.assertIn("source: 'unavailable'", detail)
        self.assertLess(detail.index("prksEffectivePersonRecord"), detail.index("prksHydratePendingWorkMetadata()"))
        self.assertLess(detail.index("prksHydratePendingWorkMetadata()"), detail.index("renderPersonDetails(ctx, person, contentDiv)"))
        self.assertIn("samePersonRefresh && previousPersonEditing", detail)
        self.assertIn("previousPersonDraft", detail)
        self.assertIn("prksRetainPersonEditAcrossRefresh", detail)
        self.assertLess(app.index("const previousPersonDraft"), app.index("const generation = ctx.beginRoute(route)"))
        self.assertIn("prksTakePersonIndexCreateTabId", app)
        self.assertIn("tabId: createTabId", app)
        self.assertIn("availability: 'unavailable'", detail)
        self.assertIn("notFoundTitle: 'Person not available offline'", detail)

    def test_people_route_reuses_host_and_dismisses_before_retry(self):
        app = (FRONTEND / "app.js").read_text()
        self.assertIn("samePeopleWorkspace", app)
        self.assertIn("__prksRetainPeopleSurface", app)
        present = app[app.index("function prksPresentVuePeople") : app.index("function prksRenderRouteLoading")]
        self.assertIn(":scope > [data-prks-vue-route-host]", present)
        self.assertIn("contentDiv.innerHTML = '';", present)
        self.assertLess(present.index("querySelector"), present.index("contentDiv.innerHTML = '';"))
        self.assertIn("samePeopleWorkspace && typeof window.prksVueDismissPeople", app)
        refresh = app[
            app.index("function prksOfflineMaybeRefreshFocusedRoute") : app.index(
                "function prksRenderConnectivityIndicator"
            )
        ]
        self.assertIn("ctx.ui.personDetailEditing", refresh)

    def test_vue_calls_public_wrappers_and_not_the_durable_store(self):
        combined = "\n".join(path.read_text() for path in FEATURE.rglob("*") if path.suffix != ".md")
        for banned in (
            "listOperations",
            "prksDurable",
            "fetch(",
            "prksRequest(",
            "useDebounceFn",
            "useEventListener",
            "createPinia",
            "vue-router",
            "crypto.randomUUID",
        ):
            self.assertNotIn(banned, combined, banned)
        intents = (FEATURE / "intents.ts").read_text()
        for wrapper in (
            "savePersonProfileDraft",
            "deletePerson",
            "prksTogglePersonWorksEdit",
            "prksRemoveWorkRoleLink",
            "openModal",
            "prksOpenNewPersonModalFromPeoplePage",
        ):
            self.assertIn(wrapper, intents, wrapper)
        self.assertNotIn("prksSavePersonFieldsDurably", intents)
        self.assertNotIn("prksDeletePersonDurably", intents)
        index = (FEATURE / "PeopleIndexRoute.vue").read_text()
        self.assertIn('id="prks-people-header-new"', index)
        self.assertIn('id="prks-people-empty-new"', index)
        self.assertIn("No people yet.", index)
        self.assertIn('data-prks-role="offline-unavailable"', index)
        self.assertIn('role="link"', index)
        self.assertIn("@keydown.enter.prevent", index)
        detail = (FEATURE / "PersonDetailRoute.vue").read_text()
        self.assertIn('id="pd-first-name"', detail)
        self.assertIn('id="pd-save-btn"', detail)
        self.assertIn('busy-label="Saving…"', detail)
        self.assertNotIn("prks-person-delete-btn", detail)
        self.assertNotIn('busy-label="Deleting…"', detail)
        self.assertNotIn('v-else class="document-view document-view--person"', detail)
        self.assertLess(detail.index('v-if="showForm"'), detail.index("person-profile__works"))
        self.assertIn("Remove link to ${work.title} (${work.roleType})", detail)
        people = (FRONTEND / "components" / "people.js").read_text()
        people_delete = people[people.index("async function deletePerson(") : people.index("function prksTogglePersonWorksEdit(")]
        self.assertIn("busyLabel: 'Deleting…'", people_delete)
        self.assertIn("prksConfirmDestructive", people_delete)
        self.assertIn("prksUniquePersonWorks(p).length", people_delete)
        self.assertIn("data-prks-person-cancel", detail)
        self.assertNotIn(':key="person.id"', detail)
        self.assertIn("async function savePersonProfileDraft(", people)
        self.assertIn("function prksBindPersonProfileDraft(", people)
        self.assertIn("async function deletePerson(explicitCtx, explicitGeneration)", people)
        self.assertIn("new Event('input'", people)
        sidebar = people[people.index("function renderPersonProfileDetailsSidebarHtml") : people.index("function renderPersonProfileEditFormHtml")]
        self.assertIn("Edit profile", sidebar)
        self.assertIn("Done", sidebar)
        self.assertIn('id="prks-person-view-graph"', sidebar)
        write = people[people.index("async function prksWriteDirtyPersonFields(") : people.index("async function savePersonProfileDraft(")]
        owned = write.rindex("prksPersonProfileWriteStillOwned")
        saved = write.index("await prksSavePersonFieldsDurably")
        self.assertLess(owned, saved)
        self.assertNotIn("await ", write[owned:saved])
        self.assertIn("prksSavePersonFieldsDurably(personId, changes, base, stillOwned)", write)
        groups = people[
            people.index("async function prksSavePersonGroupIds(") : people.index(
                "function prksFinishPersonProfileSave("
            )
        ]
        self.assertIn(
            "prksSetPersonGroupMembership(groupId, personId, present, true, stillOwns)",
            groups,
        )
        draft_save = people[people.index("async function savePersonProfileDraft(") : people.index("window.savePersonProfileDraft")]
        self.assertLess(draft_save.index("if (!stillOwned())"), draft_save.index("prksSavePersonGroupIds"))
        self.assertNotIn("prksAlertMessage", draft_save)
        ui = (FRONTEND / "ui.js").read_text()
        panel = ui[ui.index("function updatePanelContent(") : ui.index("function prksBindPlaylistSummaryEditBtn(")]
        person_branch = panel.index("routeName === 'person'")
        group_branch = panel.index("person-group-detail", person_branch)
        self.assertNotIn("prksRefreshMountedPersonSurfaces", panel[person_branch:group_branch])
        self.assertIn("prksRefreshMountedPersonSurfaces", panel[group_branch:])

    def test_stale_delete_confirmation_does_not_delete_or_navigate(self):
        people = (FRONTEND / "components" / "people.js").read_text()
        script = "\n".join(
            (
                "const prksAlertMessage = async () => {};",
                _extract(people, "prksUniquePersonWorks"),
                _extract(people, "deletePerson"),
                r"""
const person = { id: 'P-A', first_name: 'Ada', last_name: 'A', works: [] };
const ctx = {
  tabId: 'side',
  generation: 4,
  ui: { personDetailEditing: true, personProfileDraft: { personId: 'P-A' } },
  getEntity: () => person,
  setEntity() {},
  isCurrent(generation) { return generation === 4 && owns; },
};
let confirm;
let owns = true;
let deleted = false;
let navigated = null;
const calls = [];
globalThis.prksTabContextOwnsEntityRoute = (owner, generation, type, id, route) => {
  calls.push({ generation, type, id, route, tabId: owner && owner.tabId });
  return owns;
};
globalThis.prksDeletePersonDurably = async () => { deleted = true; };
globalThis.prksConfirmDestructive = () => new Promise((resolve) => { confirm = resolve; });
globalThis.prksNavigate = (hash, opts) => { navigated = { hash, tabId: opts && opts.tabId }; };
(async () => {
  const stale = deletePerson(ctx, 4);
  await Promise.resolve();
  owns = false;
  ctx.generation = 9;
  confirm(true);
  await stale;
  if (deleted) throw new Error('stale confirmation deleted the person');
  if (navigated) throw new Error('stale confirmation navigated');
  if (ctx.ui.personDetailEditing !== true) throw new Error('stale confirmation cleared editing');
  if (calls.length !== 1) throw new Error('expected one ownership check, got ' + calls.length);
  process.stdout.write(JSON.stringify({ calls: calls.length }));
})().catch((error) => { console.error(error); process.exit(1); });
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["calls"], 1)

    def test_older_profile_save_does_not_close_a_later_session(self):
        people = (FRONTEND / "components" / "people.js").read_text()
        script = "\n".join(
            (
                "const PRKS_PERSON_PROFILE_FIELDS = ['first_name','last_name','aliases','about','image_url','link_wikipedia','link_stanford_encyclopedia','link_iep','links_other','birth_date','death_date'];",
                "const PERSON_DATE_HELP = 'date';",
                "const parsePersonBirthDeathField = (value) => String(value || '');",
                "const prksReadPersonProfileBase = async () => ({ base: { first_name: { value: 'Ada' }, last_name: { value: 'Lovelace' } }, operations: [] });",
                "const prksDirtyPersonFields = (id, desired) => desired;",
                "let writes = 0;",
                "const prksSavePersonFieldsDurably = async () => { writes += 1; };",
                "const prksPersonRecordFor = async () => ({ id: 'P1', first_name: 'Augusta', last_name: 'Lovelace', works: [] });",
                "const prksUniquePersonWorks = () => [];",
                "let rendered = 0;",
                "const renderPersonDetails = () => { rendered += 1; };",
                _extract(people, "prksCompareText"),
                _extract(people, "prksSortedUniqueIds"),
                _extract(people, "prksPersonProfileRouteStill"),
                _extract(people, "prksPersonEditSessionToken"),
                _extract(people, "prksPersonEditSessionStill"),
                _extract(people, "prksPersonProfileSaveMessage"),
                _extract(people, "prksPersonProfileDesiredChanges"),
                _extract(people, "prksPersonProfileWriteStillOwned"),
                _extract(people, "prksWriteDirtyPersonFields"),
                _extract(people, "prksSavePersonGroupIds"),
                _extract(people, "prksFinishPersonProfileSave"),
                _extract(people, "savePersonProfileDraft"),
                r"""
const prksTabContextOwnsEntityRoute = (ctx, generation, type, id) => {
  if (!ctx.isCurrent(generation)) return false;
  const live = ctx.getEntity(type);
  const route = ctx.lastResolvedRoute;
  return !!(live && String(live.id) === String(id) && route && route.name === 'person');
};
const ctx = {
  mounted: true,
  root: {},
  ui: { personDetailEditing: true, personEditSession: 2, personProfileDraft: { personId: 'P1' } },
  lastResolvedRoute: { name: 'person', params: { personId: 'P1' } },
  getEntity: () => ({ id: 'P1' }),
  setEntity() {},
  isCurrent(generation) { return generation === 4; },
};
(async () => {
  const pending = savePersonProfileDraft(ctx, 'P1',
    { first_name: 'Augusta', last_name: 'Lovelace', aliases: '', about: '', birth_date: '', death_date: '', image_url: '', link_wikipedia: '', link_stanford_encyclopedia: '', link_iep: '', links_other: '' },
    { first_name: 'Ada', last_name: 'Lovelace', aliases: '', about: '', birth_date: '', death_date: '', image_url: '', link_wikipedia: '', link_stanford_encyclopedia: '', link_iep: '', links_other: '' },
    [], [], 2, 4);
  ctx.ui.personEditSession = 5;
  ctx.ui.personProfileDraft = { personId: 'P1', first_name: 'Later' };
  const result = await pending;
  if (!result.ok) throw new Error('landed write should still report ok');
  if (ctx.ui.personDetailEditing !== true) throw new Error('older save closed the later editor');
  if (ctx.ui.personProfileDraft.first_name !== 'Later') throw new Error('older save replaced the later draft');
  if (writes !== 0) throw new Error('a reopened session accepted the older write');
  if (rendered !== 0) throw new Error('older save repainted the later session');
  process.stdout.write(JSON.stringify({ writes, rendered }));
})().catch((error) => { console.error(error); process.exit(1); });
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["writes"], 0)

    def test_save_started_on_person_a_does_not_write_after_navigation_to_b(self):
        people = (FRONTEND / "components" / "people.js").read_text()
        fields = (
            "{ first_name: 'Ada', last_name: 'Lovelace', aliases: '', about: '', birth_date: '', "
            "death_date: '', image_url: '', link_wikipedia: '', link_stanford_encyclopedia: '', "
            "link_iep: '', links_other: '' }"
        )
        changed = fields.replace("first_name: 'Ada'", "first_name: 'Augusta'")
        script = "\n".join(
            (
                "const PRKS_PERSON_PROFILE_FIELDS = ['first_name','last_name','aliases','about','image_url','link_wikipedia','link_stanford_encyclopedia','link_iep','links_other','birth_date','death_date'];",
                "const PERSON_DATE_HELP = 'date';",
                "const parsePersonBirthDeathField = (value) => String(value || '');",
                "let writes = 0;",
                "let memberships = 0;",
                "const prksReadPersonProfileBase = async () => { moved(); return { base: { first_name: { value: 'Ada' }, last_name: { value: 'Lovelace' } }, operations: [] }; };",
                "const prksDirtyPersonFields = (id, desired) => desired;",
                "const prksSavePersonFieldsDurably = async () => { writes += 1; };",
                "const prksSetPersonGroupMembership = async () => { memberships += 1; return true; };",
                "const prksPersonRecordFor = async () => null;",
                "const renderPersonDetails = () => {};",
                _extract(people, "prksCompareText"),
                _extract(people, "prksSortedUniqueIds"),
                _extract(people, "prksPersonProfileRouteStill"),
                _extract(people, "prksPersonEditSessionToken"),
                _extract(people, "prksPersonEditSessionStill"),
                _extract(people, "prksPersonProfileSaveMessage"),
                _extract(people, "prksPersonProfileDesiredChanges"),
                _extract(people, "prksPersonProfileWriteStillOwned"),
                _extract(people, "prksWriteDirtyPersonFields"),
                _extract(people, "prksSavePersonGroupIds"),
                _extract(people, "prksFinishPersonProfileSave"),
                _extract(people, "savePersonProfileDraft"),
                r"""
const prksTabContextOwnsEntityRoute = (ctx, generation, type, id) => {
  if (!ctx.isCurrent(generation)) return false;
  const live = ctx.getEntity(type);
  const route = ctx.lastResolvedRoute;
  return !!(
    live && String(live.id) === String(id) &&
    route && route.name === 'person' &&
    route.params && String(route.params.personId) === String(id)
  );
};
const ctx = {
  mounted: true,
  root: {},
  ui: { personDetailEditing: true, personEditSession: 2, personProfileDraft: { personId: 'P1' } },
  lastResolvedRoute: { name: 'person', params: { personId: 'P1' } },
  getEntity: () => ({ id: 'P1' }),
  setEntity() {},
  isCurrent() { return true; },
};
function moved() {
  ctx.getEntity = () => ({ id: 'P2' });
  ctx.lastResolvedRoute = { name: 'person', params: { personId: 'P2' } };
}
(async () => {
  const fields = """
                + fields
                + r""";
  const changed = """
                + changed
                + r""";
  const fieldResult = await savePersonProfileDraft(ctx, 'P1', changed, fields, ['G1'], ['G1'], 2, 4);
  if (!fieldResult.ok || fieldResult.message) throw new Error('navigated save should stay quiet');
  if (writes !== 0) throw new Error('field write landed on person B');
  if (memberships !== 0) throw new Error('group write landed on person B');
  const groupResult = await savePersonProfileDraft(ctx, 'P1', fields, fields, ['G2'], ['G1'], 2, 4);
  if (!groupResult.ok || groupResult.message) throw new Error('group-only navigated save should stay quiet');
  if (writes !== 0 || memberships !== 0) throw new Error('group membership wrote after leaving person A');
  process.stdout.write(JSON.stringify({ writes, memberships }));
})().catch((error) => { console.error(error); process.exit(1); });
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["writes"], 0)
        self.assertEqual(payload["memberships"], 0)

    def test_retained_same_person_refresh_still_writes_once(self):
        people = (FRONTEND / "components" / "people.js").read_text()
        guard = people[
            people.index("function prksPersonProfileWriteStillOwned(") : people.index(
                "async function prksWriteDirtyPersonFields("
            )
        ]
        self.assertIn("prksPersonEditSessionStill", guard)
        self.assertNotIn("prksTabContextOwnsEntityRoute", guard)
        script = "\n".join(
            (
                "const PRKS_PERSON_PROFILE_FIELDS = ['first_name','last_name','aliases','about','image_url','link_wikipedia','link_stanford_encyclopedia','link_iep','links_other','birth_date','death_date'];",
                "const PERSON_DATE_HELP = 'date';",
                "const parsePersonBirthDeathField = (value) => String(value || '');",
                "let releaseRead;",
                "const prksReadPersonProfileBase = () => new Promise((resolve) => { releaseRead = resolve; });",
                "const prksDirtyPersonFields = (id, desired) => desired;",
                "let writes = 0;",
                "const prksSavePersonFieldsDurably = async () => { writes += 1; };",
                "const prksPersonRecordFor = async () => ({ id: 'P1', first_name: 'Augusta', last_name: 'Lovelace', works: [] });",
                "const prksUniquePersonWorks = () => [];",
                "const renderPersonDetails = () => {};",
                _extract(people, "prksCompareText"),
                _extract(people, "prksSortedUniqueIds"),
                _extract(people, "prksPersonProfileRouteStill"),
                _extract(people, "prksPersonEditSessionToken"),
                _extract(people, "prksPersonEditSessionStill"),
                _extract(people, "prksPersonProfileSaveMessage"),
                _extract(people, "prksPersonProfileDesiredChanges"),
                _extract(people, "prksPersonProfileWriteStillOwned"),
                _extract(people, "prksWriteDirtyPersonFields"),
                _extract(people, "prksSavePersonGroupIds"),
                _extract(people, "prksFinishPersonProfileSave"),
                _extract(people, "savePersonProfileDraft"),
                r"""
const ctx = {
  mounted: true,
  root: {},
  generation: 4,
  ui: { personDetailEditing: true, personEditSession: 2, personProfileDraft: { personId: 'P1', first_name: 'Augusta' } },
  lastResolvedRoute: { name: 'person', params: { personId: 'P1' } },
  getEntity: () => ({ id: 'P1' }),
  setEntity() {},
  isCurrent(generation) { return generation === ctx.generation; },
};
(async () => {
  const pending = savePersonProfileDraft(ctx, 'P1',
    { first_name: 'Augusta', last_name: 'Lovelace', aliases: '', about: '', birth_date: '', death_date: '', image_url: '', link_wikipedia: '', link_stanford_encyclopedia: '', link_iep: '', links_other: '' },
    { first_name: 'Ada', last_name: 'Lovelace', aliases: '', about: '', birth_date: '', death_date: '', image_url: '', link_wikipedia: '', link_stanford_encyclopedia: '', link_iep: '', links_other: '' },
    [], [], 2, 4);
  ctx.generation = 9;
  if (ctx.isCurrent(4)) throw new Error('retained refresh left the captured generation current');
  if (ctx.ui.personEditSession !== 2 || !ctx.ui.personDetailEditing) throw new Error('retained refresh dropped the edit session');
  releaseRead({ base: { first_name: { value: 'Ada' }, last_name: { value: 'Lovelace' } }, operations: [] });
  const result = await pending;
  if (!result.ok || result.message) throw new Error('same-person save should complete quietly');
  if (writes !== 1) throw new Error('expected one durable write, got ' + writes);
  process.stdout.write(JSON.stringify({ writes }));
})().catch((error) => { console.error(error); process.exit(1); });
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["writes"], 1)

    def test_editing_relationships_keep_role_subtitles_and_card_fields(self):
        people = (FRONTEND / "components" / "people.js").read_text()
        keys_start = people.index("const PRKS_PERSON_WORK_CARD_KEYS")
        keys = people[keys_start : people.index("function prksPersonWorkCardFields(")]
        script = "\n".join(
            (
                keys,
                _extract(people, "prksUniquePersonWorks"),
                _extract(people, "prksPersonWorkRolesById"),
                _extract(people, "prksPersonWorkCardFields"),
                _extract(people, "prksPersonViewRecord"),
                r"""
const person = {
  id: 'P1',
  works: [
    { id: 'W1', title: 'Notes', role_type: 'Author', order_index: 0, credit_name: 'Ada', file_path: '/api/pdfs/notes.pdf', status: 'read' },
    { id: 'W1', title: 'Notes', role_type: 'Editor', order_index: 1, file_path: '/api/pdfs/notes.pdf', status: 'read' },
  ],
};
const editing = prksPersonViewRecord({ ui: { personWorksEditing: true } }, person);
if (editing.works.length !== 2) throw new Error('expected one card per role');
if (editing.works[0].subtitle !== 'Author' || editing.works[1].subtitle !== 'Editor') {
  throw new Error('editing cards lost their roles: ' + editing.works.map(w => w.subtitle).join('|'));
}
if (editing.works[0].file_path !== '/api/pdfs/notes.pdf' || editing.works[0].status !== 'read') {
  throw new Error('editing cards dropped summary fields');
}
const reading = prksPersonViewRecord({ ui: { personWorksEditing: false } }, person);
if (reading.works.length !== 1) throw new Error('read mode should collapse roles');
if (reading.works[0].subtitle.indexOf('Author') === -1 || reading.works[0].subtitle.indexOf('Editor') === -1) {
  throw new Error('read mode dropped a role: ' + reading.works[0].subtitle);
}
process.stdout.write(JSON.stringify({ editing: editing.works.map(w => w.subtitle), reading: reading.works[0].subtitle }));
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["editing"], ["Author", "Editor"])

    def test_refresh_skips_a_mismatched_person_and_keeps_the_open_draft(self):
        people = (FRONTEND / "components" / "people.js").read_text()
        script = "\n".join(
            (
                _extract(people, "prksRefreshPersonDetailMain"),
                _extract(people, "prksRetainPersonEditAcrossRefresh"),
                r"""
const painted = [];
function renderPersonDetails(_owner, person) { painted.push(person && person.id); }
const ctx = {
  root: {},
  destroyed: false,
  lastResolvedRoute: null,
  route: { name: 'person', params: { personId: 'P1' } },
  getEntity: () => ({ id: 'P1' }),
};
prksRefreshPersonDetailMain(ctx);
ctx.lastResolvedRoute = { name: 'person', params: { personId: 'P2' } };
prksRefreshPersonDetailMain(ctx);
ctx.lastResolvedRoute = { name: 'work', params: {} };
prksRefreshPersonDetailMain(ctx);
if (painted.length !== 0) throw new Error('stale refresh painted: ' + painted.join(','));
ctx.lastResolvedRoute = { name: 'person', params: { personId: 'P1' } };
prksRefreshPersonDetailMain(ctx);
if (painted.length !== 1 || painted[0] !== 'P1') throw new Error('matching refresh did not paint');
ctx.ui = { personDetailEditing: false, personProfileDraft: null, personWorksEditing: false };
const draft = { personId: 'P1', first_name: 'Ada' };
prksRetainPersonEditAcrossRefresh(ctx, true, draft, true);
if (!ctx.ui.personDetailEditing || ctx.ui.personProfileDraft !== draft || !ctx.ui.personWorksEditing) {
  throw new Error('same-person refresh dropped the open draft');
}
prksRetainPersonEditAcrossRefresh(ctx, false, draft, false);
if (ctx.ui.personDetailEditing || ctx.ui.personProfileDraft || ctx.ui.personWorksEditing) {
  throw new Error('a different person kept the previous draft');
}
process.stdout.write(JSON.stringify({ painted: painted.length }));
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["painted"], 1)

    def test_people_index_create_keeps_only_that_owner(self):
        people = (FRONTEND / "components" / "people.js").read_text()
        script = "\n".join(
            (
                "const window = globalThis;",
                _extract(people, "prksRememberPersonIndexCreate"),
                _extract(people, "prksClearPersonIndexCreateOrigin"),
                _extract(people, "prksOpenNewPersonModalFromPeoplePage"),
                _extract(people, "prksTakePersonIndexCreateTabId"),
                r"""
const tabs = {
  side: { tabId: 'side', destroyed: false, generation: 3, lastResolvedRoute: { name: 'people' } },
  main: { tabId: 'main', destroyed: false, generation: 1, lastResolvedRoute: { name: 'folders' } },
};
function prksGetTabContext(id) { return tabs[id] || null; }
function prksGetFocusedTabContext() { return tabs.main; }
function openModal(id) {
  if (id !== 'person-modal') return;
  if (window.__prksPersonIndexCreateArmed) window.__prksPersonIndexCreateArmed = false;
  else prksClearPersonIndexCreateOrigin();
}
prksOpenNewPersonModalFromPeoplePage(tabs.side);
if (!window.__prksPersonIndexCreateOrigin || window.__prksPersonIndexCreateOrigin.tabId !== 'side') {
  throw new Error('people index did not record its owner');
}
if (prksTakePersonIndexCreateTabId() !== 'side') throw new Error('valid owner was dropped');
if (prksTakePersonIndexCreateTabId() !== '') throw new Error('origin survived the take');
prksOpenNewPersonModalFromPeoplePage(tabs.side);
tabs.side.generation = 9;
if (prksTakePersonIndexCreateTabId() !== '') throw new Error('stale generation was reused');
tabs.side.generation = 3;
prksOpenNewPersonModalFromPeoplePage(tabs.side);
tabs.side.lastResolvedRoute = { name: 'work' };
if (prksTakePersonIndexCreateTabId() !== '') throw new Error('owner left the people index');
tabs.side.lastResolvedRoute = { name: 'people' };
window.__prksPersonIndexCreateOrigin = { tabId: 'side', generation: 3 };
window.__prksPersonIndexCreateArmed = false;
openModal('person-modal');
if (window.__prksPersonIndexCreateOrigin) throw new Error('generic open kept a people origin');
process.stdout.write(JSON.stringify({ ok: true }));
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])


def _extract(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    if start >= 6 and src[start - 6 : start] == "async ":
        start -= 6
    brace = src.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for index in range(brace, len(src)):
        char = src[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1]
    raise AssertionError(f"unterminated function {name}")
