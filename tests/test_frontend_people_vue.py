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
        self.assertIn('busy-label="Deleting…"', detail)
        self.assertIn("data-prks-person-cancel", detail)
        self.assertNotIn(':key="person.id"', detail)
        people = (FRONTEND / "components" / "people.js").read_text()
        self.assertIn("async function savePersonProfileDraft(", people)
        self.assertIn("function prksBindPersonProfileDraft(", people)
        self.assertIn("async function deletePerson(explicitCtx, explicitGeneration)", people)
        self.assertIn("new Event('input'", people)
        sidebar = people[people.index("function renderPersonProfileDetailsSidebarHtml") : people.index("function renderPersonProfileEditFormHtml")]
        self.assertIn("Edit profile", sidebar)
        self.assertIn("Done", sidebar)
        self.assertIn('id="prks-person-view-graph"', sidebar)

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
                _extract(people, "prksPersonProfileRouteStill"),
                _extract(people, "prksPersonEditSessionToken"),
                _extract(people, "prksPersonEditSessionStill"),
                _extract(people, "prksPersonProfileSaveMessage"),
                _extract(people, "prksSavePersonGroupIds"),
                _extract(people, "prksFinishPersonProfileSave"),
                _extract(people, "savePersonProfileDraft"),
                r"""
const ctx = {
  mounted: true,
  root: {},
  ui: { personDetailEditing: true, personEditSession: 2, personProfileDraft: { personId: 'P1' } },
  lastResolvedRoute: { name: 'person', params: { personId: 'P1' } },
  getEntity: () => ({ id: 'P1' }),
  setEntity() {},
};
(async () => {
  const pending = savePersonProfileDraft(ctx, 'P1',
    { first_name: 'Augusta', last_name: 'Lovelace', aliases: '', about: '', birth_date: '', death_date: '', image_url: '', link_wikipedia: '', link_stanford_encyclopedia: '', link_iep: '', links_other: '' },
    { first_name: 'Ada', last_name: 'Lovelace', aliases: '', about: '', birth_date: '', death_date: '', image_url: '', link_wikipedia: '', link_stanford_encyclopedia: '', link_iep: '', links_other: '' },
    [], [], 2);
  ctx.ui.personEditSession = 5;
  ctx.ui.personProfileDraft = { personId: 'P1', first_name: 'Later' };
  const result = await pending;
  if (!result.ok) throw new Error('landed write should still report ok');
  if (ctx.ui.personDetailEditing !== true) throw new Error('older save closed the later editor');
  if (ctx.ui.personProfileDraft.first_name !== 'Later') throw new Error('older save replaced the later draft');
  if (writes !== 1) throw new Error('expected the field write');
  if (rendered !== 0) throw new Error('older save repainted the later session');
  process.stdout.write(JSON.stringify({ writes, rendered }));
})().catch((error) => { console.error(error); process.exit(1); });
""",
            )
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["writes"], 1)


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
