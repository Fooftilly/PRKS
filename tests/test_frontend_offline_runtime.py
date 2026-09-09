"""Structural + Node regressions for the offline connectivity/read-through/
mutation-guard runtime (offline-runtime.js)."""
import os
import re
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_RUNTIME = os.path.join(_FRONTEND, "js", "offline-runtime.js")
_STORE = os.path.join(_FRONTEND, "js", "offline-store.js")
_COORD = os.path.join(_FRONTEND, "js", "request-coordinator.js")
_INDEX = os.path.join(_FRONTEND, "index.html")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_offline_runtime_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendOfflineRuntimeTests(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_RUNTIME))
        self.assertTrue(os.path.isfile(_RUNNER))

    def test_loaded_before_api_after_offline_store(self):
        html = _read(_INDEX)
        store_at = html.find('src="/js/offline-store.js"')
        runtime_at = html.find('src="/js/offline-runtime.js"')
        api_at = html.find('src="/js/api.js"')
        self.assertNotEqual(store_at, -1)
        self.assertNotEqual(runtime_at, -1)
        self.assertNotEqual(api_at, -1)
        self.assertLess(store_at, runtime_at)
        self.assertLess(runtime_at, api_at)

    def test_request_coordinator_stays_memory_only(self):
        coord = _read(_COORD)
        self.assertNotIn("createPrksOfflineRuntime", coord)
        self.assertNotIn("indexedDB", coord)
        self.assertNotIn("createPrksOfflineStore", coord)
        # Transport-health signalling is allowed via dynamic lookups; it must
        # not import or construct the offline runtime/store.
        self.assertIn("root.prksOfflineNoteRequestSuccess", coord)
        self.assertIn("root.prksOfflineNoteRequestFailure", coord)

    def test_no_canonical_persistence_in_runtime(self):
        src = _read(_RUNTIME)
        # All persistence is delegated to offline-store.js; the runtime holds
        # no canonical domain data of its own.
        self.assertNotIn("indexedDB.open", src)
        self.assertIn("createPrksOfflineStore", src)

        def test_navigator_online_is_a_hint_not_authoritative(self):
            src = _read(_RUNTIME)
            # Both the browser's 'online' AND 'offline' events are hints only; each
            # one only ever triggers a real probe request (runProbe), never a
            # direct flip to STATE_ONLINE/STATE_OFFLINE by the listener itself.
            self.assertIn("addEventListener('online', runProbe)", src)
            self.assertIn("addEventListener('offline', runProbe)", src)

        def test_init_begins_a_real_probe_immediately(self):
            src = _read(_RUNTIME)
            init_start = src.index("function init()")
            init_body = src[init_start : init_start + 400]
            self.assertIn("runProbe()", init_body)

        def test_probe_reachability_is_any_http_response_not_just_ok(self):
            src = _read(_RUNTIME)
            probe_start = src.index("function runProbe()")
            probe_body = src[probe_start : probe_start + 1600]
            # Must react to a resolved response existing at all, not res.ok.
            self.assertIn("if (res) {", probe_body)
            self.assertNotIn("if (res && res.ok)", probe_body)

    def test_mutation_guard_never_queues(self):
        src = _read(_RUNTIME)
        self.assertIn("This change requires a connection to PRKS.", src)
        # The module documents that it deliberately has no outbox (Phase 1), it
        # must never actually implement queuing/persisting a blocked mutation.
        self.assertIn("no offline mutation outbox", src)
        self.assertNotIn("queueMutation", src)
        self.assertNotIn("pendingMutation", src)

    def test_domain_invalidation_blocks_before_the_physical_sweep(self):
        src = _read(_RUNTIME)
        start = src.index("function markDomainChanged(")
        body = src[start : start + 2200]
        block_at = body.index("blockedDomains.add(key)")
        # The generation bump and the fallback block must both be synchronous,
        # and must come before any IndexedDB work is scheduled: a stale domain
        # is ineligible the instant a canonical mutation is acknowledged.
        self.assertLess(body.index("domainGeneration.set(key, next)"), block_at)
        self.assertLess(block_at, body.index("deleteEntitiesByKind"))
        # Only the current generation may settle the domain.
        self.assertIn("if (currentDomainGeneration(key) !== next) return ok;", body)

    def test_concepts_domain_shape_is_defined_once(self):
        src = _read(_RUNTIME)
        self.assertIn("function prksOfflineMarkConceptsChanged()", src)
        self.assertIn("entityKinds: ['concept']", src)
        self.assertIn("const CONCEPTS_LIST_KEY = 'concepts:index';", src)
        # Every canonical caller shares that one definition rather than
        # re-listing the domain's kinds/list keys at each call site.
        for path in (
            os.path.join(_FRONTEND, "js", "api.js"),
            os.path.join(_FRONTEND, "js", "components", "works.js"),
        ):
            caller = _read(path)
            self.assertIn("prksOfflineMarkConceptsChanged", caller)
        for path in (
            os.path.join(_FRONTEND, "js", "api.js"),
            os.path.join(_FRONTEND, "js", "ui.js"),
            os.path.join(_FRONTEND, "js", "components", "works.js"),
            os.path.join(_FRONTEND, "js", "components", "playlists.js"),
        ):
            self.assertNotIn("entityKinds:", _read(path))

    def test_every_work_title_surface_uses_the_shared_coherence_helper(self):
        """A cached Concept detail shows the titles of the Works that mention it,
        so every canonical Work-title change must invalidate the Concepts domain
        -- not just the one in the metadata editor."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        start = api.index("function prksMarkWorkTitleChanged(")
        body = api[start : start + 700]
        self.assertIn("prksOfflineMarkEntityChanged('work', workId)", body)
        self.assertIn("prksMarkConceptsDomainChanged();", body)

        # Find every surface that PATCHes a Work title, and require each one to
        # route through that helper rather than evicting only the Work entity.
        title_patch = re.compile(r"JSON\.stringify\(\{\s*title[:,]")
        for name in ("ui.js", os.path.join("components", "playlists.js"), os.path.join("components", "works.js")):
            src = _read(os.path.join(_FRONTEND, "js", name))
            for match in title_patch.finditer(src):
                window = src[match.start() : match.start() + 1800]
                if "/api/works/" not in src[max(0, match.start() - 600) : match.start()]:
                    continue
                self.assertIn(
                    "prksMarkWorkTitleChanged",
                    window,
                    "%s PATCHes a Work title without the shared Concepts coherence hook" % name,
                )

    def test_concept_domain_invalidated_by_canonical_research_changes(self):
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        # Successful Research Notes save and successful Work deletion both change
        # canonical Work -> Concept research data.
        notes_at = works.index("function prksEnqueueWorkResearchNotesSave(")
        notes_body = works[notes_at : notes_at + 6000]
        self.assertIn("prksOfflineMarkConceptsChanged()", notes_body)
        delete_at = works.index("async function deleteWork(")
        delete_body = works[delete_at : delete_at + 2500]
        self.assertIn("prksOfflineMarkConceptsChanged()", delete_body)
        # ... and only on acknowledged success, never on a failed/aborted save.
        self.assertIn("if (ok && typeof prksOfflineMarkConceptsChanged === 'function')", notes_body)

    def test_concept_mutations_invalidate_at_the_canonical_helper_boundary(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function createConcept(",
            "async function updateConcept(",
            "async function deleteConcept(",
            "async function putConceptParents(",
            "async function putConceptAliases(",
        ):
            start = api.index(fn)
            body = api[start : start + 900]
            # prksResearchJson throws on a non-ok response, so reaching the
            # invalidation means the canonical mutation actually succeeded.
            self.assertIn("prksResearchJson(", body, fn)
            self.assertIn("prksMarkConceptsDomainChanged();", body, fn)
            self.assertLess(body.index("prksResearchJson("), body.index("prksMarkConceptsDomainChanged();"), fn)

    def test_positions_domain_shape_is_defined_once(self):
        src = _read(_RUNTIME)
        self.assertIn("function prksOfflineMarkPositionsChanged()", src)
        self.assertIn("entityKinds: ['position']", src)
        self.assertIn("const POSITIONS_LIST_KEY = 'positions:index';", src)
        self.assertIn("const DOMAIN_POSITIONS = 'positions';", src)
        # Callers share that one definition rather than re-listing the domain's
        # kinds/list keys, and the two domains stay separate names.
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        self.assertIn("prksOfflineMarkPositionsChanged", api)
        self.assertNotIn("'positions:index'", api)
        self.assertNotIn("entityKinds:", api)

    def test_position_routes_use_offline_read_through(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        index_at = app.index("case 'positions': {")
        index_body = app[index_at : app.index("case 'position-detail': {")]
        self.assertIn("prksOfflineListFetch(", index_body)
        self.assertIn("PRKS_POSITIONS_LIST_KEY", index_body)
        self.assertIn("domain: PRKS_POSITIONS_DOMAIN", index_body)
        self.assertIn("validate: prksIsPositionIndexShape", index_body)
        self.assertIn("renderPositionsIndexUnavailable", index_body)
        self.assertIn("prksOfflinePrependBanner(", index_body)
        # The plain online-only fetch helper is no longer the route's read path.
        self.assertNotIn("fetchPositions(", index_body)

        detail_at = app.index("case 'position-detail': {")
        detail_body = app[detail_at : app.index("case 'arguments': {")]
        self.assertIn("prksOfflineDetailFetch(", detail_body)
        self.assertIn("'position',", detail_body)
        self.assertIn("domain: PRKS_POSITIONS_DOMAIN", detail_body)
        self.assertIn("prksIsPositionShape(value, positionId)", detail_body)
        self.assertIn("prksOfflineRenderUnavailable(contentDiv, 'Position not available offline')", detail_body)
        # A reachable-server 404 keeps its own distinct meaning.
        self.assertIn("renderPositionNotFound", detail_body)
        self.assertNotIn("fetchPosition(", detail_body)

        self.assertIn(
            "typeof PRKS_OFFLINE_POSITIONS_LIST_KEY === 'string' ? PRKS_OFFLINE_POSITIONS_LIST_KEY : 'positions:index'",
            app,
        )
        self.assertIn(
            "typeof PRKS_OFFLINE_DOMAIN_POSITIONS === 'string' ? PRKS_OFFLINE_DOMAIN_POSITIONS : 'positions'",
            app,
        )

    def test_graph_and_playlists_stay_online_only(self):
        """Phase 1 stops at Work/Concept/Position/Argument/People. Nothing else
        may be wrapped in the offline read-through path as a side effect."""
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        for case in (
            "case 'research-graph': {",
            "case 'playlists': {",
        ):
            if case not in app:
                continue
            start = app.index(case)
            # Bound the slice to this case only; the next one may legitimately
            # be an offline-capable route.
            after = app.find("\n            case '", start + 1)
            body = app[start : after if after != -1 else start + 1600]
            for forbidden in ("prksOfflineListFetch", "prksOfflineDetailFetch"):
                self.assertNotIn(forbidden, body, case)
        runtime = _read(_RUNTIME)
        for forbidden in ("playlists:index", "graph:"):
            self.assertNotIn(forbidden, runtime)

    def test_people_domain_shape_is_defined_once(self):
        src = _read(_RUNTIME)
        self.assertIn("function prksOfflineMarkPeopleChanged()", src)
        self.assertIn("entityKinds: ['person']", src)
        self.assertIn("const PEOPLE_LIST_KEY = 'people:index';", src)
        self.assertIn("const DOMAIN_PEOPLE = 'people';", src)
        # Role-filter views are local projections; never their own cache keys.
        for forbidden in ("people:index:", "'people:Author'", "peopleByRole"):
            self.assertNotIn(forbidden, src)
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        self.assertIn("prksOfflineMarkPeopleChanged", api)
        self.assertNotIn("'people:index'", api)

    def test_people_routes_use_offline_read_through(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        # Both People routes share ONE fetch helper, so a future People route
        # cannot introduce a second, role-filtered cache by accident.
        helper_at = app.index("async function prksOfflinePeopleFetch(")
        helper = app[helper_at : helper_at + 700]
        self.assertIn("prksOfflineListFetch(PRKS_PEOPLE_LIST_KEY, '/api/persons'", helper)
        self.assertIn("domain: PRKS_PEOPLE_DOMAIN", helper)
        self.assertIn("validate: prksIsPeopleIndexShape", helper)
        self.assertNotIn("role", helper.split("prksOfflineListFetch(")[1])

        index_at = app.index("case 'people': {")
        index_body = app[index_at : app.index("case 'people-role': {")]
        self.assertIn("prksOfflinePeopleFetch(", index_body)
        self.assertIn("renderPeopleListUnavailable", index_body)
        self.assertIn("prksOfflinePrependBanner(", index_body)
        self.assertNotIn("fetchPersons(", index_body)

        role_at = app.index("case 'people-role': {")
        role_body = app[role_at : app.index("case 'people-groups': {")]
        # The role route reads the COMPLETE list through the same helper and
        # filters locally; it must never request or cache a server-filtered
        # subset.
        self.assertIn("prksOfflinePeopleFetch(", role_body)
        self.assertNotIn("role=", role_body)
        self.assertIn("roleFilter", role_body)
        self.assertNotIn("fetchPersons(", role_body)

        detail_at = app.index("case 'person': {")
        detail_body = app[detail_at : detail_at + 3500]
        self.assertIn("prksOfflineDetailFetch(", detail_body)
        self.assertIn("'person',", detail_body)
        self.assertIn("domain: PRKS_PEOPLE_DOMAIN", detail_body)
        self.assertIn("prksIsPersonShape(value, personId)", detail_body)
        self.assertIn("prksOfflineRenderUnavailable(contentDiv, 'Person not available offline')", detail_body)
        self.assertNotIn("fetchPersonDetails(", detail_body)

    def test_person_mutations_invalidate_people(self):
        people = _read(os.path.join(_FRONTEND, "js", "components", "people.js"))
        save_at = people.index("async function savePersonProfile(")
        save_body = people[save_at : save_at + 7000]
        # Every profile field is in the People read model, so People goes
        # unconditionally; Arguments only when the canonical name changed.
        self.assertIn("prksMarkPeopleDomainChanged();", save_body)
        self.assertIn("_personNameChanged", save_body)
        delete_at = people.index("async function deletePerson(")
        delete_body = people[delete_at : delete_at + 2500]
        self.assertIn("prksMarkPeopleDomainChanged();", delete_body)
        # Creation surfaces (modal + quick-create) invalidate too.
        for path in (os.path.join(_FRONTEND, "js", "app.js"), os.path.join(_FRONTEND, "js", "ui.js")):
            src = _read(path)
            self.assertIn("prksMarkPeopleDomainChanged", src)

    def test_work_side_people_coherence_hooks(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        # A Work card inside a cached Person shows title/status/doc type/etc.
        title_at = api.index("function prksMarkWorkTitleChanged(")
        self.assertIn("prksMarkPeopleDomainChanged();", api[title_at : title_at + 800])
        # Bulk status is on the card; folder moves and tags are not.
        bulk_at = api.index("async function bulkUpdateWorks(")
        bulk_body = api[bulk_at : bulk_at + 1600]
        # Gated on the status action alone -- the other bulk actions are not on
        # a Person's Work cards.
        self.assertIn("if (payload && payload.action === 'set_status') {", bulk_body)
        self.assertIn("prksMarkPeopleDomainChanged();", bulk_body)
        for forbidden in ("move_folder'", "add_tags'", "remove_tags'"):
            self.assertNotIn("payload.action === '" + forbidden, bulk_body)
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        delete_at = works.index("async function deleteWork(")
        self.assertIn("prksOfflineMarkPeopleChanged()", works[delete_at : delete_at + 3000])
        # Managed PDF save changes file_size_bytes and can add Mentioned roles.
        pdf = _read(os.path.join(_FRONTEND, "js", "components", "works-pdf.js"))
        pdf_at = pdf.index("async function exportAndPersistPdfCopy(")
        self.assertIn("prksOfflineMarkPeopleChanged()", pdf[pdf_at : pdf_at + 1800])
        # ... but the separate annotations JSON save does not.
        ann_at = pdf.index("async function runWorkAnnotationAndPdfPersistencePass(")
        ann_body = pdf[ann_at : ann_at + 2000]
        self.assertNotIn("prksOfflineMarkPeopleChanged", ann_body.split("/annotations")[1])
        # Work creation can create role links in the same canonical request.
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        self.assertIn("Array.isArray(payload.roles) && payload.roles.length", app)

    def test_group_mutations_invalidate_people_except_bare_creation(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function updatePersonGroup(",
            "async function deletePersonGroup(",
            "async function addPersonGroupMember(",
            "async function removePersonGroupMember(",
        ):
            start = api.index(fn)
            body = api[start : api.index('\nasync function ', start + 1)]
            self.assertIn("if (res.ok) {", body, fn)
            self.assertIn("prksMarkPeopleDomainChanged();", body, fn)
            self.assertIn("prksMarkPersonGroupsDomainChanged();", body, fn)
        # A brand-new unassigned Group cannot appear in any Person's read model.
        create = api.split('async function createPersonGroup(', 1)[1].split('async function updatePersonGroup(', 1)[0]
        self.assertNotIn('prksMarkPeopleDomainChanged', create)
        self.assertIn('prksMarkPersonGroupsDomainChanged', create)
        groups = _read(os.path.join(_FRONTEND, "js", "components", "people-groups.js"))
        # Every Group mutation goes through the wrappers, none direct.
        self.assertNotIn("`/api/person-groups/${", groups)
        self.assertNotIn("prksRequest('/api/person-groups'", groups)
        self.assertNotIn("prksRequest('/api/person-groups'", _read(os.path.join(_FRONTEND, 'js', 'app.js')))
        for wrapper in (
            "updatePersonGroup(",
            "deletePersonGroup(",
            "addPersonGroupMember(",
            "removePersonGroupMember(",
        ):
            self.assertIn(wrapper, groups)

    def test_people_are_not_invalidated_by_unrelated_read_models(self):
        """Research Notes, Concepts, Positions and Argument mutations do not
        touch the People read model."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function createConcept(",
            "async function updateConcept(",
            "async function createPosition(",
            "async function updatePosition(",
            "async function createArgument(",
            "async function updateArgument(",
            "async function putArgumentTargets(",
            "async function putArgumentSources(",
        ):
            start = api.index(fn)
            body = api[start : start + 1200]
            self.assertNotIn("prksMarkPeopleDomainChanged", body, fn)
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        notes_at = works.index("function prksEnqueueWorkResearchNotesSave(")
        notes_body = works[notes_at : notes_at + 7000]
        self.assertNotIn("prksOfflineMarkPeopleChanged", notes_body)

    def test_argument_routes_use_offline_read_through(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        index_at = app.index("case 'arguments': {")
        index_body = app[index_at : app.index("case 'argument-detail': {")]
        self.assertIn("prksOfflineListFetch(", index_body)
        self.assertIn("PRKS_ARGUMENTS_LIST_KEY", index_body)
        self.assertIn("domain: PRKS_ARGUMENTS_DOMAIN", index_body)
        self.assertIn("validate: prksIsArgumentIndexShape", index_body)
        self.assertIn("renderArgumentsIndexUnavailable", index_body)
        self.assertIn("prksOfflinePrependBanner(", index_body)
        # The COMPLETE collection is fetched and cached under one key; ?kind= is
        # a local subset of it, never a separately cached server-filtered list.
        self.assertIn("'/api/arguments',", index_body)
        self.assertNotIn("kind=", index_body.split("prksOfflineListFetch(")[1].split(");")[0])
        self.assertIn("prksFilterArgumentsByKind(allArguments, kind)", index_body)
        self.assertNotIn("fetchArguments(", index_body)

        detail_at = app.index("case 'argument-detail': {")
        detail_body = app[detail_at : detail_at + 3000]
        self.assertIn("prksOfflineDetailFetch(", detail_body)
        self.assertIn("'argument',", detail_body)
        self.assertIn("domain: PRKS_ARGUMENTS_DOMAIN", detail_body)
        self.assertIn("prksIsArgumentShape(value, argumentId)", detail_body)
        self.assertIn(
            "prksOfflineRenderUnavailable(contentDiv, 'Argument or Stance not available offline')", detail_body
        )
        # A reachable-server 404 keeps its own distinct meaning.
        self.assertIn("renderArgumentNotFound", detail_body)
        self.assertNotIn("fetchArgument(", detail_body)
        # A freshly mounted route always starts read-only.
        self.assertIn("ctx.ui.argumentEditing = false;", detail_body)

        self.assertIn("prksFilterArgumentsByKind", app)
        self.assertIn(
            "typeof PRKS_OFFLINE_ARGUMENTS_LIST_KEY === 'string' ? PRKS_OFFLINE_ARGUMENTS_LIST_KEY : 'arguments:index'",
            app,
        )

    def test_arguments_domain_shape_is_defined_once(self):
        src = _read(_RUNTIME)
        self.assertIn("function prksOfflineMarkArgumentsChanged()", src)
        self.assertIn("entityKinds: ['argument']", src)
        self.assertIn("const ARGUMENTS_LIST_KEY = 'arguments:index';", src)
        self.assertIn("const DOMAIN_ARGUMENTS = 'arguments';", src)
        # Stances are not a separate domain: they are Arguments with kind
        # 'stance', and the read model is interconnected across both.
        self.assertNotIn("'stances'", src)
        self.assertNotIn("stances:index", src)
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        self.assertIn("prksOfflineMarkArgumentsChanged", api)
        self.assertNotIn("'arguments:index'", api)

    def test_position_mutations_invalidate_at_the_canonical_helper_boundary(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function createPosition(",
            "async function updatePosition(",
            "async function deletePosition(",
        ):
            start = api.index(fn)
            body = api[start : start + 900]
            # prksResearchJson throws on a non-ok response, so reaching the
            # invalidation means the canonical mutation actually succeeded.
            self.assertIn("prksResearchJson(", body, fn)
            self.assertIn("prksMarkPositionsDomainChanged();", body, fn)
            self.assertLess(
                body.index("prksResearchJson("), body.index("prksMarkPositionsDomainChanged();"), fn
            )

    def test_argument_mutations_that_stale_position_summaries_invalidate_positions(self):
        """A cached Position detail embeds Argument name/kind/verdict and the
        whole targeting list, so those Argument helpers owe it an invalidation --
        while Argument *sources* (Works) are not in the Position read model."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function createArgument(",
            "async function updateArgument(",
            "async function deleteArgument(",
            "async function putArgumentTargets(",
        ):
            start = api.index(fn)
            body = api[start : start + 900]
            self.assertIn("prksResearchJson(", body, fn)
            self.assertIn("prksMarkPositionsDomainChanged();", body, fn)
            self.assertLess(
                body.index("prksResearchJson("), body.index("prksMarkPositionsDomainChanged();"), fn
            )
        sources_at = api.index("async function putArgumentSources(")
        sources_body = api[sources_at : api.index("async function putArgumentTargets(")]
        self.assertNotIn(
            "prksMarkPositionsDomainChanged",
            sources_body,
            "Argument source Works are not part of the Position read model",
        )

    def test_positions_are_not_invalidated_by_unrelated_read_models(self):
        """Research Notes, Work and Concept coherence hooks must not have been
        cargo-culted onto the Positions domain."""
        for name in ("ui.js", os.path.join("components", "works.js"), os.path.join("components", "playlists.js")):
            src = _read(os.path.join(_FRONTEND, "js", name))
            self.assertNotIn("prksOfflineMarkPositionsChanged", src, name)
            self.assertNotIn("prksMarkPositionsDomainChanged", src, name)
        concepts = _read(os.path.join(_FRONTEND, "js", "components", "concepts.js"))
        self.assertNotIn("Positions", concepts)

    def test_direct_argument_mutations_invalidate_the_arguments_domain(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function createArgument(",
            "async function updateArgument(",
            "async function deleteArgument(",
            "async function putArgumentTargets(",
            "async function putArgumentSources(",
        ):
            start = api.index(fn)
            body = api[start : start + 1200]
            self.assertIn("prksResearchJson(", body, fn)
            self.assertIn("prksMarkArgumentsDomainChanged();", body, fn)
            self.assertLess(
                body.index("prksResearchJson("), body.index("prksMarkArgumentsDomainChanged();"), fn
            )

    def test_argument_sources_touch_arguments_but_never_positions(self):
        """Source Works and their authors are in the Argument read model and NOT
        in the Position one -- the sharpest boundary between the two domains."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        start = api.index("async function putArgumentSources(")
        body = api[start : api.index("async function putArgumentTargets(")]
        self.assertIn("prksMarkArgumentsDomainChanged();", body)
        self.assertNotIn("prksMarkPositionsDomainChanged", body)
        # ... while targets legitimately move both.
        targets_start = api.index("async function putArgumentTargets(")
        targets_body = api[targets_start : targets_start + 1200]
        self.assertIn("prksMarkArgumentsDomainChanged();", targets_body)
        self.assertIn("prksMarkPositionsDomainChanged();", targets_body)

    def test_position_rename_invalidates_arguments_but_create_delete_do_not(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        update_start = api.index("async function updatePosition(")
        update_body = api[update_start : api.index("async function deletePosition(")]
        # A cached Argument's targets embed the Position's name.
        self.assertIn("prksMarkArgumentsDomainChanged();", update_body)
        create_body = api[api.index("async function createPosition(") : update_start]
        self.assertNotIn("prksMarkArgumentsDomainChanged", create_body)
        delete_start = api.index("async function deletePosition(")
        delete_body = api[delete_start : delete_start + 800]
        self.assertNotIn("prksMarkArgumentsDomainChanged", delete_body)

    def test_work_title_helper_owns_both_dependent_domains(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        start = api.index("function prksMarkWorkTitleChanged(")
        body = api[start : start + 900]
        self.assertIn("prksOfflineMarkEntityChanged('work', workId)", body)
        # A Work title shows in cached Concept mentions AND cached Argument
        # sources/mentions, so one helper owns both.
        self.assertIn("prksMarkConceptsDomainChanged();", body)
        self.assertIn("prksMarkArgumentsDomainChanged();", body)

    def test_role_surfaces_share_one_coherence_helper_with_two_dependencies(self):
        """Every role type stales People (assigned_roles, the Person's linked
        Work rows, and aliases via credit names); only Author additionally
        stales cached Argument source authors."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        start = api.index("function prksMarkWorkRoleChanged(")
        body = api[start : start + 1000]
        self.assertIn("prksOfflineMarkEntityChanged('work', workId)", body)
        # People is unconditional, Arguments is gated on Author.
        people_at = body.index("prksMarkPeopleDomainChanged();")
        author_at = body.index("=== 'Author'")
        self.assertLess(people_at, author_at, "People must not be inside the Author branch")
        self.assertLess(author_at, body.index("prksMarkArgumentsDomainChanged();"))
        # The old name survives only as a delegate, never a second implementation.
        legacy = api[api.index("function prksMarkWorkAuthorDisplayChanged(") :][:400]
        self.assertIn("return prksMarkWorkRoleChanged(workId, roleType);", legacy)
        self.assertNotIn("prksOfflineMarkEntityChanged", legacy)
        # Every role surface routes through it rather than evicting only the Work.
        ui = _read(os.path.join(_FRONTEND, "js", "ui.js"))
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        self.assertIn("prksMarkWorkRoleChanged", app)
        # link, credit-name edit and unlink: three call sites.
        self.assertGreaterEqual(ui.count("prksMarkWorkRoleChanged("), 3)

    def test_research_notes_and_work_delete_invalidate_arguments_too(self):
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        notes_at = works.index("function prksEnqueueWorkResearchNotesSave(")
        notes_body = works[notes_at : notes_at + 7000]
        # Notes are the canonical source of [[argument:...]] mentions.
        self.assertIn("prksOfflineMarkArgumentsChanged()", notes_body)
        self.assertIn("if (ok && typeof prksOfflineMarkArgumentsChanged === 'function')", notes_body)
        delete_at = works.index("async function deleteWork(")
        delete_body = works[delete_at : delete_at + 3000]
        self.assertIn("prksOfflineMarkArgumentsChanged()", delete_body)

    def test_person_rename_invalidates_arguments_only_on_a_real_name_change(self):
        people = _read(os.path.join(_FRONTEND, "js", "components", "people.js"))
        start = people.index("async function savePersonProfile(")
        body = people[start : start + 6000]
        self.assertIn("_personNameChanged", body)
        self.assertIn("first_name", body)
        self.assertIn("last_name", body)
        self.assertIn("if (_personNameChanged && typeof prksMarkArgumentsDomainChanged === 'function')", body)
        # The diff exists precisely so a biography/links/dates/groups edit does
        # not cost the user their cached Arguments.
        self.assertLess(body.index("_personNameChanged ="), body.index("prksRequest(`/api/persons/"))

    def test_arguments_are_not_invalidated_by_unrelated_read_models(self):
        """Concept mutations and ordinary Work relationship edits do not touch
        the Argument read model, and must not be cargo-culted into it."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function createConcept(",
            "async function updateConcept(",
            "async function deleteConcept(",
            "async function putConceptParents(",
            "async function putConceptAliases(",
        ):
            start = api.index(fn)
            body = api[start : start + 900]
            self.assertNotIn("prksMarkArgumentsDomainChanged", body, fn)
        concepts = _read(os.path.join(_FRONTEND, "js", "components", "concepts.js"))
        self.assertNotIn("prksOfflineMarkArgumentsChanged", concepts)
        playlists = _read(os.path.join(_FRONTEND, "js", "components", "playlists.js"))
        self.assertNotIn("prksOfflineMarkArgumentsChanged", playlists)
        self.assertNotIn("prksMarkArgumentsDomainChanged", playlists)

    def test_people_offline_media_policy(self):
        """Phase 1 caches structured data only: no portrait or thumbnail bytes
        in IndexedDB, in the service worker, or requested by a cached mount."""
        people = _read(os.path.join(_FRONTEND, "js", "components", "people.js"))
        # A cached mount suppresses both media sources.
        self.assertIn("const offlineCached = !!(ctx && ctx.ui && ctx.ui.personOfflineCached);", people)
        self.assertIn("offlineCached ? null : personProfileImageSrc(person)", people)
        self.assertIn("suppressThumbnail: true", people)
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        self.assertIn("ctx.ui.personOfflineCached = offlinePerson.source === 'cache';", app)
        # The card option exists and only removes the source, never the layout.
        cards = _read(os.path.join(_FRONTEND, "js", "components", "work-cards.js"))
        self.assertIn("const suppressThumbnail = options.suppressThumbnail === true;", cards)
        self.assertIn("const thumbSrc = suppressThumbnail", cards)
        # No image bytes anywhere in the offline stack.
        store = _read(_STORE)
        for forbidden in ("profile-image", "thumbnail", "image/"):
            self.assertNotIn(forbidden, store)
            self.assertNotIn(forbidden, _read(_RUNTIME))
        sw = _read(os.path.join(_FRONTEND, "sw.js"))
        self.assertNotIn("profile-image", sw)
        self.assertNotIn("/thumbnail", sw)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for offline runtime tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
