"""Structural + Node regressions for the offline connectivity/read-through/
mutation-guard runtime (offline-runtime.js)."""
import os
import pathlib
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
        # guardMutation blocks server-bound ops only; durable intent uses local-store.js.
        self.assertIn("local-store.js", src)
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
        # Work deletion fences Concepts from the DELETE_WORK reconciler, not
        # from works.js (which only enqueues durable intent).
        for path in (
            os.path.join(_FRONTEND, "js", "api.js"),
            os.path.join(_FRONTEND, "js", "offline-runtime.js"),
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
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        self.assertIn("prksDeleteWorkDurably", works)
        self.assertNotIn("prksOfflineMarkConceptsChanged", works)

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
        notes_at = works.index("function prksEnqueueWorkResearchNotesSave(")
        notes_body = works[notes_at : notes_at + 6000]
        # Durable enqueue; Concept coherence happens on ACK in the reconciler.
        self.assertIn("prksSaveWorkNoteDurably", notes_body)
        self.assertNotIn("prksOfflineMarkConceptsChanged()", notes_body)
        delete_at = works.index("async function deleteWork(")
        delete_body = works[delete_at : delete_at + 2500]
        self.assertIn("prksDeleteWorkDurably", delete_body)
        self.assertNotIn("prksOfflineMarkConceptsChanged()", delete_body)
        runtime = _read(_RUNTIME)
        reconcile_del = runtime[runtime.index("async function reconcileDeletedWork("):]
        reconcile_del = reconcile_del[: reconcile_del.index("\n        /*")]
        self.assertIn("prksOfflineMarkConceptsChanged()", reconcile_del)
        reconcile = runtime[runtime.index("async function reconcileWorkNoteBody("):]
        reconcile = reconcile[: reconcile.index("\n        async function reconcileWorkNote(")]
        self.assertIn("DOMAIN_CONCEPTS", reconcile)

    def test_concept_mutations_route_through_durable_boundaries(self):
        """Coherence for Concepts happens on ACKNOWLEDGEMENT, not at the call.

        The invariant this test has always protected -- nothing publishes
        coherence before the change is real -- is unchanged; what moved is where
        it is enforced. A durable write changes nothing cached until the server
        answers, so marking a domain at the call site would discard a page to
        show the same thing.
        """
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn, writer in (
            ("async function createConcept(", "prksCreateConceptDurably("),
            ("async function updateConcept(", "prksSetConceptIdentityDurably("),
            ("async function deleteConcept(", "prksDeleteConceptDurably("),
            ("async function putConceptParents(", "prksSetConceptParentsDurably("),
            ("async function putConceptAliases(", "prksSetConceptIdentityDurably("),
        ):
            start = api.index(fn)
            body = api[start : api.index("\n}", start)]
            with self.subTest(fn=fn):
                self.assertIn(writer, body)
                self.assertNotIn("prksRequest('/api/concepts", body)
                self.assertNotIn("prksResearchJson(", body)
                self.assertNotIn("prksMarkConceptsDomainChanged", body)
                self.assertNotIn("prksMarkResearchGraphCoreChanged", body)
        # The reconcilers own it instead, and each one says which projections it
        # can patch and which it must fence.
        runtime = _read(_RUNTIME)
        for name in ("reconcileCreatedConcept", "reconcileConceptField",
                     "reconcileConceptIdentity", "reconcileConceptParents",
                     "reconcileDeletedConcept"):
            with self.subTest(name=name):
                self.assertIn("async function %s(" % name, runtime)

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

    def test_research_graph_uses_snapshot_adapter(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        self.assertIn("loadSnapshot: prksOfflineResearchGraphFetch", app)
        self.assertIn("prksIsResearchGraphSnapshot", app)
        graph = _read(os.path.join(_FRONTEND, "js", "components", "research-graph.js"))
        self.assertNotIn("prksOfflineDetailFetch", graph)
        self.assertNotIn("indexedDB", graph)

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
        # Profile editing is durable-first now, so it RECONCILES instead of
        # invalidating: the canonical change happens at ACKNOWLEDGEMENT, and
        # that is where coherence belongs. Invalidating at Save would drop the
        # People cache for a change the server has not applied yet -- and
        # offline there is nothing to re-read it from.
        self.assertIn("prksSavePersonFieldsDurably(", save_body)
        self.assertNotIn("prksRequest(`/api/persons/${personId}`, {\n"
                         "                method: 'PATCH',\n"
                         "                headers: { 'Content-Type': 'application/json' },\n"
                         "                body: JSON.stringify(payload)", save_body,
                         "profile fields must not also go out as a direct PATCH")
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        reconcile_at = runtime.index("async function reconcilePersonField(")
        reconcile = runtime[reconcile_at : runtime.index("\n        }\n", reconcile_at)]
        # Every profile field is in the People read model, so the People
        # snapshots are PATCHED -- precisely, because the new value is known.
        for patched in ("'person'", "'person-metadata-state'", "PEOPLE_LIST_KEY"):
            self.assertIn(patched, reconcile, patched)
        # Deletion is durable-first too, so it reconciles on acknowledgement
        # rather than invalidating at the click. The PROTECTION is unchanged and
        # still canonical: a Person credited on a file is refused.
        delete_at = people.index("async function deletePerson(")
        delete_body = people[delete_at : delete_at + 2500]
        self.assertIn("prksDeletePersonDurably(", delete_body)
        self.assertNotIn("prksMarkPeopleDomainChanged();", delete_body)
        self.assertNotIn("prksRequest(", delete_body)
        self.assertIn("prksUniquePersonWorks(p).length", delete_body,
                      "the local half of the canonical protection stays")
        deleted_at = runtime.index("async function reconcileDeletedPerson(")
        deleted = runtime[deleted_at : runtime.index("\n        }\n", deleted_at)]
        # The index this device holds is patched; the Person's own snapshot has
        # nothing left to show and is dropped.
        self.assertIn("PEOPLE_LIST_KEY", deleted)
        self.assertIn("invalidateEntity('person', id)", deleted)
        self.assertIn("prksOfflineMarkPersonGroupsChanged()", deleted)
        # Creation is durable-first now, so it RECONCILES instead of
        # invalidating: the acknowledgement carries the created Person, and
        # discarding the People cache would make a Person the user created
        # offline disappear until the next successful read. Both creation
        # surfaces go through the one durable writer.
        for path in (os.path.join(_FRONTEND, "js", "app.js"), os.path.join(_FRONTEND, "js", "ui.js")):
            src = _read(path)
            with self.subTest(module=os.path.basename(path)):
                self.assertIn("prksCreatePersonDurably(", src)
                self.assertNotIn("'/api/persons', {", src.replace(" ", ""),
                                 "Person creation must not POST directly")
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        self.assertIn("reconcileCreatedPerson", runtime)

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
        # Other bulk actions may carry their own domains (move_folder stales
        # Folders), but People must be reachable ONLY from the status branch.
        people_hooks = bulk_body.count("prksMarkPeopleDomainChanged();")
        self.assertEqual(people_hooks, 1, "People must be hooked exactly once in bulkUpdateWorks")
        status_at = bulk_body.index("if (payload && payload.action === 'set_status') {")
        status_branch = bulk_body[status_at : bulk_body.index("}", bulk_body.index("prksMarkPeopleDomainChanged();", status_at))]
        self.assertIn("prksMarkPeopleDomainChanged();", status_branch)
        for forbidden in ("move_folder'", "add_tags'", "remove_tags'"):
            self.assertNotIn(
                "payload.action === '" + forbidden,
                status_branch,
                "a non-status action must not sit inside the People branch",
            )
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        delete_at = works.index("async function deleteWork(")
        self.assertIn("prksDeleteWorkDurably", works[delete_at : delete_at + 3000])
        runtime = _read(_RUNTIME)
        reconcile_del = runtime[runtime.index("async function reconcileDeletedWork("):]
        reconcile_del = reconcile_del[: reconcile_del.index("\n        /*")]
        self.assertIn("prksOfflineMarkPeopleChanged()", reconcile_del)
        # Managed PDF save changes file_size_bytes and can add Mentioned roles.
        pdf = _read(os.path.join(_FRONTEND, "js", "components", "works-pdf.js"))
        pdf_at = pdf.index("async function exportAndPersistPdfCopy(")
        pdf_end = pdf.index("\n    async function restoreEffectiveViewerAnnotations(", pdf_at)
        pdf_export = pdf[pdf_at:pdf_end]
        self.assertIn("prksOfflineMarkPeopleChanged()", pdf_export)
        # ... but the separate annotations JSON save does not.
        ann_at = pdf.index("async function runWorkAnnotationAndPdfPersistencePass(")
        ann_end = pdf.index("\n    // PRKS may go offline mid-confirmation-loop", ann_at)
        ann_body = pdf[ann_at:ann_end]
        self.assertIn("/annotations", ann_body)
        self.assertNotIn("prksOfflineMarkPeopleChanged", ann_body.split("/annotations")[1])
        # Work creation can create role links in the same canonical request.
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        self.assertIn("Array.isArray(payload.roles) && payload.roles.length", app)

    def test_person_groups_domain_exclusions_and_work_side_hooks(self):
        """Person Groups inherits every Work-side dependency People has, because
        a cached Group detail embeds whole People index rows -- and inherits
        People's exclusions with it. The E2Es prove the behavior; this catches a
        hook being moved off the canonical-success path by a refactor."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        # Role coherence is owned by the one shared helper.
        role_at = api.index("function prksMarkWorkRoleDependenciesChanged(")
        role_body = api[role_at : api.index("\nasync function ", role_at)]
        self.assertIn("prksMarkPersonGroupsDomainChanged();", role_body)
        # Work title/metadata and bulk status are People-only: a Group's name,
        # hierarchy and membership rows cannot change.
        title_at = api.index("function prksMarkWorkTitleChanged(")
        self.assertNotIn("PersonGroups", api[title_at : title_at + 800])
        bulk_at = api.index("async function bulkUpdateWorks(")
        self.assertNotIn("PersonGroups", api[bulk_at : bulk_at + 1600])
        # Work deletion drops role rows out of every cached Group member row.
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        delete_at = works.index("async function deleteWork(")
        self.assertIn("prksDeleteWorkDurably", works[delete_at : delete_at + 3000])
        runtime = _read(_RUNTIME)
        reconcile_del = runtime[runtime.index("async function reconcileDeletedWork("):]
        reconcile_del = reconcile_del[: reconcile_del.index("\n        /*")]
        self.assertIn("prksOfflineMarkPersonGroupsChanged()", reconcile_del)
        # The managed PDF save owns it; the separate annotations JSON save does not.
        pdf = _read(os.path.join(_FRONTEND, "js", "components", "works-pdf.js"))
        pdf_at = pdf.index("async function exportAndPersistPdfCopy(")
        pdf_end = pdf.index("\n    async function restoreEffectiveViewerAnnotations(", pdf_at)
        pdf_body = pdf[pdf_at:pdf_end]
        self.assertIn("prksOfflineMarkPersonGroupsChanged()", pdf_body)
        # ... and only after the canonical response was acknowledged.
        self.assertLess(pdf_body.index("if (!pdfRes.ok)"), pdf_body.index("PersonGroups"))
        ann_at = pdf.index("async function runWorkAnnotationAndPdfPersistencePass(")
        ann_end = pdf.index("\n    // PRKS may go offline mid-confirmation-loop", ann_at)
        ann_body = pdf[ann_at:ann_end]
        self.assertNotIn("PersonGroups", ann_body.split("/annotations")[1])
        # Work creation can link roles without ever calling POST /api/roles.
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        create_at = app.index("if (res.ok && Array.isArray(payload.roles) && payload.roles.length) {")
        self.assertIn("prksMarkPersonGroupsDomainChanged();", app[create_at : create_at + 600])
        # Every Person mutation is durable now, so their Group coherence hooks
        # moved to the reconcilers: a deleted Person disappears from the member
        # lists embedded in cached Group details, and that happens when the
        # server has actually removed them.
        people = _read(os.path.join(_FRONTEND, "js", "components", "people.js"))
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        at = runtime.index("async function reconcileDeletedPerson(")
        self.assertIn("prksOfflineMarkPersonGroupsChanged()",
                      runtime[at : runtime.index("\n        }\n", at)])
        at = people.index("async function prksSavePersonGroupMemberships(")
        membership = people[at : people.index("\n}", at)]
        self.assertIn("prksSetPersonGroupMembership(", membership)
        self.assertNotIn("prksRequest(", membership)
        # Concept, Position and Argument mutations never touch it. (Research
        # Notes saves are covered behaviourally by the Person Groups E2Es.)
        for name in ("async function createConcept(",
                     "async function updateConcept(", "async function createPosition(",
                     "async function updatePosition(", "async function createArgument(",
                     "async function updateArgument(", "async function putArgumentTargets(",
                     "async function putArgumentSources("):
            at = api.index(name)
            self.assertNotIn("PersonGroups", api[at : at + 1400], name)

    def test_playlist_mutations_route_through_durable_boundaries(self):
        """Playlist writes are spread across playlists.js, ui.js and app.js, so
        the wrappers -- not each surface -- own the durable boundary. A raw
        endpoint call outside playlists.js is how that silently breaks."""
        pl = _read(os.path.join(_FRONTEND, "js", "components", "playlists.js"))
        for fn, writer in (
            ("async function createPlaylist(", "prksCreatePlaylistDurably("),
            ("async function updatePlaylist(", "prksSavePlaylistFieldsDurably("),
            ("async function prksSetWorkPlaylist(", "prksSetWorkPlaylistDurably("),
            ("async function reorderPlaylist(", "prksReorderPlaylistItemsDurably("),
            ("async function deletePlaylistCanonical(", "prksDeletePlaylistDurably("),
        ):
            start = pl.index(fn)
            end = pl.find("\nasync function ", start + 1)
            body = pl[start : end if end != -1 else len(pl)]
            self.assertIn(writer, body, fn)
            # Durable, so never gated on connectivity and never a request.
            self.assertNotIn("prksRequest(", body, fn)
            self.assertNotIn("prksOfflineGuardMutation(", body, fn)
            # No manual domain invalidation: the reconcilers own the cache once
            # the server answers, and marking a domain changed for an intent
            # that has not landed would discard a page to show the same thing.
            self.assertNotIn("prksPlaylistsChanged", body, fn)
        # Adding and removing are the SAME scalar on the Work, so both names
        # reach the one family rather than two racing membership operations.
        for fn in ("async function addWorkToPlaylist(",
                   "async function removeWorkFromPlaylist("):
            start = pl.index(fn)
            end = pl.find("\nasync function ", start + 1)
            self.assertIn("prksSetWorkPlaylist(", pl[start:end], fn)
        # An edit measures the draft against the ACKNOWLEDGED base, not against
        # the values the page happens to be showing.
        update = pl[pl.index("async function updatePlaylist("):
                    pl.index("async function prksSetWorkPlaylist(")]
        self.assertIn("prksAcknowledgedPlaylistBase(playlistId, ops)", update)
        self.assertIn("prksDirtyPlaylistFields(playlistId, draft, base, ops)", update)
        self.assertIn("if (!base) throw prksPlaylistBaseUnavailable(", update)
        # No other production file may issue a raw Playlist write.
        for name in (
            os.path.join(_FRONTEND, "js", "app.js"),
            os.path.join(_FRONTEND, "js", "ui.js"),
        ):
            src = _read(name)
            self.assertNotIn("prksRequest('/api/playlists'", src, name)
            self.assertNotIn("/api/playlists/${encodeURIComponent", src, name)
        # ... and the creation modal carries no connectivity guard any more:
        # the id is minted here, so the playlist is real before any server
        # has heard of it.
        ui = _read(os.path.join(_FRONTEND, "js", "ui.js"))
        self.assertNotIn("Creating a Playlist requires a connection to PRKS.", ui)

    def test_work_side_playlist_card_disables_only_what_needs_a_server(self):
        """The Work detail page's Playlist card is a Playlist mutation surface
        on a *Work* route, so it needs its own owned policy -- it cannot ride on
        the Playlist routes' binding. What it disables offline is the SEARCH,
        which reads the Playlist catalogue; the decisions themselves are
        durable."""
        pl = _read(os.path.join(_FRONTEND, "js", "components", "playlists.js"))
        body = pl[pl.index("async function mountPlaylistAttachControls("):]
        policy = pl[pl.index("const PRKS_WORK_PLAYLIST_MUTATION_SELECTOR"):]
        policy = policy[: policy.index("].join(', ');")]
        for control in ("#prks-work-playlist-search", "#prks-work-playlist-set-btn"):
            self.assertIn(control, policy, control)
        # Clear names no playlist at all, and New... mints one here and attaches
        # this video to it. Both are ordinary durable decisions.
        self.assertNotIn("#prks-work-playlist-clear-btn", policy)
        self.assertNotIn("#prks-work-playlist-new-btn", policy)
        self.assertIn("prksApplyWorkPlaylistOfflineState(ctx);", body)
        # Edit still refuses to *start* a session offline while Done stays live:
        # mounting the editor reads the Playlist catalogue.
        self.assertIn("editBtn.disabled = !online && !editing;", pl)
        # Neither Playlist read may reach the network while non-online: both are
        # raw fetches, not offline read-throughs, and this function is invoked
        # with `void` so a rejection would go unhandled.
        self.assertIn(
            "pid && prksPlaylistRuntimeOnline() && typeof fetchPlaylistDetails === 'function'",
            body)
        self.assertIn("if (prksPlaylistRuntimeOnline()) {", body)
        self.assertLess(body.index("if (prksPlaylistRuntimeOnline()) {"),
                        body.index("await fetchPlaylists("))
        # A failed durable write names the actual problem rather than a flat
        # "could not": an unknown base is a different thing from a refusal.
        for handler in ("setBtn.onclick", "clearBtn.onclick"):
            at = body.index(handler)
            self.assertIn("(_e && _e.message)", body[at : at + 2600], handler)

    def test_playlist_domain_dependencies_and_exclusions(self):
        """Playlist detail renders each item's title, author_text and
        published_date and nothing else from the Work summary the endpoint
        joins in -- so the dependency set is deliberately narrow."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        # A Work metadata/title save stales the rendered item rows.
        title_at = api.index("function prksMarkWorkTitleChanged(")
        title_body = api[title_at : api.index("\nfunction prksMarkWorkAuthorDisplayChanged(", title_at)]
        self.assertIn("prksMarkPlaylistsDomainChanged();", title_body)
        # Roles are not rendered by the Playlist UI.
        role_at = api.index("function prksMarkWorkRoleDependenciesChanged(")
        role_body = api[role_at : api.index("\nasync function ", role_at)]
        self.assertNotIn("Playlists", role_body)
        # Neither is Work status, nor folders/tags.
        bulk_at = api.index("async function bulkUpdateWorks(")
        self.assertNotIn("Playlists", api[bulk_at : bulk_at + 1600])
        # Concept/Position/Argument mutations do not participate at all.
        for name in (
            "async function createConcept(", "async function updateConcept(",
            "async function createPosition(", "async function updatePosition(",
            "async function createArgument(", "async function updateArgument(",
            "async function putArgumentTargets(", "async function putArgumentSources(",
        ):
            at = api.index(name)
            self.assertNotIn("Playlists", api[at : at + 1400], name)
        # Person and Group mutations do not display in a Playlist either.
        for path in (
            os.path.join(_FRONTEND, "js", "components", "people.js"),
            os.path.join(_FRONTEND, "js", "components", "people-groups.js"),
        ):
            self.assertNotIn("Playlists", _read(path), path)
        # Work deletion drops the playlist_items row.
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        delete_at = works.index("async function deleteWork(")
        self.assertIn("prksDeleteWorkDurably", works[delete_at : delete_at + 3800])
        runtime = _read(_RUNTIME)
        reconcile_del = runtime[runtime.index("async function reconcileDeletedWork("):]
        reconcile_del = reconcile_del[: reconcile_del.index("\n        /*")]
        self.assertIn("prksOfflineMarkPlaylistsChanged()", reconcile_del)
        # The managed PDF save does not: no rendered Playlist field changes.
        pdf = _read(os.path.join(_FRONTEND, "js", "components", "works-pdf.js"))
        self.assertNotIn("Playlists", pdf)
        # Work creation invalidates only when it actually requested an attach.
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        create_at = app.index("if (res.ok && String(payload.playlist_id || '').trim()) {")
        self.assertIn("prksMarkPlaylistsDomainChanged();", app[create_at : create_at + 700])
        # The Playlist inline Work rename no longer invalidates anything: a
        # Work Title is local-first, so the rename enqueues a durable
        # operation and the acknowledgement RECONCILES every cached
        # representation with the exact new value. Destroying usable offline
        # snapshots for a change whose shape is already known would be the
        # opposite of what the reconciler exists to do -- and there is no
        # PATCH left here to invalidate after.
        pl = _read(os.path.join(_FRONTEND, "js", "components", "playlists.js"))
        rename_at = pl.index("if (renSave) {")
        rename_body = pl[rename_at : rename_at + 2200]
        self.assertIn("prksSaveWorkFieldDurably(wid, 'title'", rename_body)
        self.assertNotIn("prksMarkWorkTitleChanged", rename_body)
        self.assertNotIn("method: 'PATCH'", rename_body)
        self.assertNotIn("prksPlaylistsChanged()", rename_body)

    def test_group_mutations_are_durable_and_never_raw_requests(self):
        """Every Person Group mutation is a semantic operation now.

        The canonical-request wrappers are gone: a raw PATCH or DELETE from a
        component would bypass the revision model an offline device depends on
        and would be, once again, a feature that exists only while connected.
        """
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for gone in ("async function createPersonGroup(",
                     "async function updatePersonGroup(",
                     "async function deletePersonGroup(",
                     "async function addPersonGroupMember(",
                     "async function removePersonGroupMember("):
            self.assertNotIn(gone, api, gone)
        groups = _read(os.path.join(_FRONTEND, "js", "components", "people-groups.js"))
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        for source, name in ((groups, "people-groups.js"), (app, "app.js")):
            self.assertNotIn("`/api/person-groups/${", source, name)
            self.assertNotIn("prksRequest('/api/person-groups'", source, name)
        for durable in ("prksCreatePersonGroupDurably(",
                        "prksSavePersonGroupFieldsDurably(",
                        "prksSetPersonGroupMemberDurably(",
                        "prksDeletePersonGroupDurably("):
            self.assertIn(durable, groups + app, durable)

    def test_group_coherence_moved_to_the_reconcilers(self):
        """Coherence follows the CANONICAL change, which for a durable family
        is the acknowledgement rather than the click."""
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        for fn in ("async function reconcileCreatedPersonGroup(",
                   "async function reconcilePersonGroupField(",
                   "async function reconcilePersonGroupMember(",
                   "async function reconcileDeletedPersonGroup("):
            self.assertIn(fn, runtime, fn)
        # A rename reaches the group chips embedded in every People row; a
        # description or a reparent appears in none of them.
        at = runtime.index("async function reconcilePersonGroupField(")
        body = runtime[at: runtime.index("\n        /**", at + 10)]
        self.assertIn("if (field === 'name') {", body)
        self.assertLess(body.index("if (field === 'name') {"), body.index("DOMAIN_PEOPLE"))
        # A creation cannot appear in any Person's read model: nobody is in it.
        at = runtime.index("async function reconcileCreatedPersonGroup(")
        create = runtime[at: runtime.index("\n        /**", at + 10)]
        self.assertNotIn("DOMAIN_PEOPLE", create)

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
        detail_body = app[detail_at : app.index("case 'research-graph': {", detail_at)]
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
        # A freshly mounted route resets stale edit-mode UI, but mutation stays
        # available offline through the durable queue.
        self.assertIn("ctx.ui.argumentEditing = false;", detail_body)
        self.assertIn("prksEffectiveArgumentDetail(item, argumentOps)", detail_body)

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

    def test_position_mutations_route_through_durable_boundaries(self):
        """Coherence for Positions happens on ACKNOWLEDGEMENT, not at the call.

        The invariant this has always protected -- nothing publishes coherence
        before the change is real -- is unchanged; where it is enforced moved.
        """
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn, writer in (
            ("async function createPosition(", "prksCreatePositionDurably("),
            ("async function updatePosition(", "prksSavePositionFieldsDurably("),
            ("async function deletePosition(", "prksDeletePositionDurably("),
        ):
            start = api.index(fn)
            body = api[start : api.index("\n}", start)]
            with self.subTest(fn=fn):
                self.assertIn(writer, body)
                self.assertNotIn("prksRequest('/api/positions", body)
                self.assertNotIn("prksResearchJson(", body)
                self.assertNotIn("prksMarkPositionsDomainChanged", body)
        runtime = _read(_RUNTIME)
        for name in ("reconcileCreatedPosition", "reconcilePositionField",
                     "reconcileDeletedPosition"):
            with self.subTest(name=name):
                self.assertIn("async function %s(" % name, runtime)

    def test_argument_mutations_route_through_durable_boundaries(self):
        """User actions write semantic intent; reconcilers own ACK coherence."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn, writer in (
            ("async function createArgument(", "prksCreateArgumentDurably("),
            ("async function updateArgument(", "prksSaveArgumentFieldsDurably("),
            ("async function deleteArgument(", "prksDeleteArgumentDurably("),
            ("async function putArgumentTargets(", "prksSetArgumentTargetsDurably("),
            ("async function putArgumentSources(", "prksSetArgumentSourcesDurably("),
        ):
            start = api.index(fn)
            body = api[start : api.index("\n}", start)]
            with self.subTest(fn=fn):
                self.assertIn(writer, body)
                self.assertNotIn("prksRequest('/api/arguments", body)
                self.assertNotIn("prksResearchJson(", body)
        runtime = _read(_RUNTIME)
        self.assertIn("prksOfflineMarkPositionsChanged();", runtime)
        self.assertIn("async function reconcileArgumentSources(", runtime)
        sources = runtime[runtime.index("async function reconcileArgumentSources("):
                          runtime.index("async function reconcileArgumentTargets(")]
        self.assertNotIn("prksOfflineMarkPositionsChanged", sources)

    def test_positions_are_not_invalidated_by_unrelated_read_models(self):
        """Research Notes, Work and Concept coherence hooks must not have been
        cargo-culted onto the Positions domain."""
        for name in ("ui.js", os.path.join("components", "works.js"), os.path.join("components", "playlists.js")):
            src = _read(os.path.join(_FRONTEND, "js", name))
            self.assertNotIn("prksOfflineMarkPositionsChanged", src, name)
            self.assertNotIn("prksMarkPositionsDomainChanged", src, name)
        concepts = _read(os.path.join(_FRONTEND, "js", "components", "concepts.js"))
        self.assertNotIn("Positions", concepts)

    def test_argument_sources_touch_arguments_but_never_positions(self):
        """Source Works and their authors are in the Argument read model and NOT
        in the Position one -- the sharpest boundary between the two domains."""
        runtime = _read(_RUNTIME)
        start = runtime.index("async function reconcileArgumentSources(")
        body = runtime[start : runtime.index("async function reconcileArgumentTargets(")]
        self.assertIn("prksOfflineMarkArgumentsChanged();", body)
        self.assertNotIn("prksMarkPositionsDomainChanged", body)
        # ... while targets legitimately move both.
        targets_start = runtime.index("async function reconcileArgumentTargets(")
        targets_body = runtime[targets_start : runtime.index("async function reconcileDeletedArgument(")]
        self.assertIn("prksOfflineMarkArgumentsChanged();", targets_body)
        self.assertIn("prksOfflineMarkPositionsChanged();", targets_body)

    def test_a_position_rename_stales_arguments_but_a_description_edit_does_not(self):
        """A cached Argument's targets embed the Position's NAME, and nothing
        else about it -- so the dependency belongs to the rename alone, and the
        reconciler is now where that distinction is drawn.

        Create and delete still owe Arguments nothing of that kind: a brand-new
        Position cannot already be targeted, and a targeted one cannot be
        deleted.
        """
        runtime = _read(_RUNTIME)
        start = runtime.index("async function reconcilePositionField(")
        body = runtime[start : runtime.index("\n        /**", start)]
        self.assertIn("if (field !== 'name' || !result.changed) return true;", body)
        self.assertLess(body.index("if (field !== 'name'"),
                        body.index("prksOfflineMarkArgumentsChanged();"),
                        "a description edit must return before the Arguments fence")
        start = runtime.index("async function reconcileCreatedPosition(")
        create_body = runtime[start : runtime.index("\n        /**", start)]
        self.assertNotIn("prksOfflineMarkArgumentsChanged", create_body)

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
        start = api.index("function prksMarkWorkRoleDependenciesChanged(")
        body = api[start : start + 1200]
        # People is unconditional, Arguments is gated on Author.
        people_at = body.index("prksMarkPeopleDomainChanged();")
        author_at = body.index("=== 'Author'")
        self.assertLess(people_at, author_at, "People must not be inside the Author branch")
        self.assertLess(author_at, body.index("prksMarkArgumentsDomainChanged();"))
        # The Work-evicting variant is a thin wrapper, never a second copy.
        evicting = api[api.index("function prksMarkWorkRoleChanged(") :][:500]
        self.assertIn("prksOfflineMarkEntityChanged('work', workId)", evicting)
        self.assertIn("prksMarkWorkRoleDependenciesChanged(roleType);", evicting)
        self.assertNotIn("prksMarkPeopleDomainChanged();", evicting)
        # The old name survives only as a delegate, never a second implementation.
        legacy = api[api.index("function prksMarkWorkAuthorDisplayChanged(") :][:400]
        self.assertIn("return prksMarkWorkRoleChanged(workId, roleType);", legacy)
        self.assertNotIn("prksOfflineMarkEntityChanged", legacy)

        # Role mutations are durable-first now, so the dependency sweep happens
        # at ACK reconciliation rather than at each UI call site -- and it must
        # be the variant that does NOT evict the Work the pass just patched.
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        reconcile = runtime[runtime.index("async function reconcileWorkRole(") :]
        reconcile = reconcile[: reconcile.index("\n        async function ")]
        self.assertIn("prksMarkWorkRoleDependenciesChanged(result.role_type)", reconcile)
        self.assertNotIn("prksMarkWorkRoleChanged(", reconcile)

        # ... and no role surface writes through the old direct-API path.
        ui = _read(os.path.join(_FRONTEND, "js", "ui.js"))
        self.assertNotIn("prksMarkWorkRoleChanged(", ui,
                         "role UI is durable-first; invalidation belongs to the ACK")

    def test_research_notes_and_work_delete_invalidate_arguments_too(self):
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        notes_at = works.index("function prksEnqueueWorkResearchNotesSave(")
        notes_body = works[notes_at : notes_at + 7000]
        self.assertIn("prksSaveWorkNoteDurably", notes_body)
        self.assertNotIn("prksOfflineMarkArgumentsChanged()", notes_body)
        delete_at = works.index("async function deleteWork(")
        delete_body = works[delete_at : delete_at + 3000]
        self.assertIn("prksDeleteWorkDurably", delete_body)
        self.assertNotIn("prksOfflineMarkArgumentsChanged()", delete_body)
        runtime = _read(_RUNTIME)
        reconcile_del = runtime[runtime.index("async function reconcileDeletedWork("):]
        reconcile_del = reconcile_del[: reconcile_del.index("\n        /*")]
        self.assertIn("prksOfflineMarkArgumentsChanged()", reconcile_del)
        reconcile = runtime[runtime.index("async function reconcileWorkNoteBody("):]
        reconcile = reconcile[: reconcile.index("\n        async function reconcileWorkNote(")]
        self.assertIn("DOMAIN_ARGUMENTS", reconcile)

    def test_person_rename_invalidates_arguments_only_on_a_real_name_change(self):
        """The rule survived the move to durable editing; only its home did.

        A cached Argument source, a Graph label and a Work card credit line
        display a Person's NAME, in rows keyed by Work -- so there is no
        precise patch to make and those domains are invalidated. Every other
        profile field is absent from all of them, and must not cost the user
        their cache.
        """
        state = _read(os.path.join(_FRONTEND, "js", "person-metadata-state.js"))
        displayed = state[state.index("const DISPLAY_FIELDS ="):]
        displayed = displayed[: displayed.index(";")]
        self.assertIn("'first_name'", displayed)
        self.assertIn("'last_name'", displayed)
        for absent in ("about", "birth_date", "link_wikipedia", "image_url"):
            self.assertNotIn("'%s'" % absent, displayed, absent)

        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        start = runtime.index("async function reconcilePersonField(")
        body = runtime[start : runtime.index("\n        }\n", start)]
        self.assertIn("PRKS_PERSON_DISPLAY_FIELDS", body)
        gate = body.index("PRKS_PERSON_DISPLAY_FIELDS")
        for hook in ("prksOfflineMarkArgumentsChanged", "prksOfflineMarkResearchGraphPeopleChanged",
                     "prksOfflineMarkFoldersChanged", "prksOfflineMarkWorksBrowseChanged"):
            self.assertIn(hook, body, hook)
            self.assertLess(gate, body.index(hook),
                            "%s must be gated on the field being displayed elsewhere" % hook)

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


def _fn_body(src, signature):
    """Source of one top-level function, from its signature to its closing brace."""
    at = src.index(signature)
    end = src.index("\n}\n", at)
    return src[at:end]


class FrontendFoldersOfflineTests(unittest.TestCase):
    """Static contracts for the Folders/Home offline surface.

    These guard the boundaries an E2E cannot cheaply prove for every call
    site: that no production surface writes Folders outside the canonical
    wrappers, and that each wrapper carries both its guard and its coherence
    hook.
    """

    def test_folder_routes_use_the_offline_read_through(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        # The plain online-only helpers must no longer be the routes' read path.
        folders_at = app.index("case 'folders': {")
        folders_body = app[folders_at : folders_at + 1200]
        self.assertIn("prksOfflineListFetch(", folders_body)
        self.assertIn("PRKS_FOLDERS_LIST_KEY", folders_body)
        self.assertNotIn("fetchFolders(", folders_body)
        detail_at = app.index("case 'folder-detail': {")
        detail_body = app[detail_at : detail_at + 1400]
        self.assertIn("prksOfflineDetailFetch(", detail_body)
        self.assertNotIn("fetchFolderDetails(", detail_body)

    def test_missing_snapshot_is_distinct_from_a_cached_empty_library(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        folders_at = app.index("case 'folders': {")
        body = app[folders_at : folders_at + 1200]
        self.assertIn("Folders not available offline", body)
        # A cached [] resolves truthy through the resolver, so the unavailable
        # branch must be gated on the resolver's null, never on list length.
        self.assertNotIn(".length === 0", body)

    def test_canonical_folder_wrappers_guard_and_publish_coherence(self):
        """Folder TAG membership is durable (ADD/REMOVE_FOLDER_TAG). Folder
        create/field/delete/work-folder remain durable and reconcile at ACK.
        Tag merge is also durable (MERGE_TAG) and reconciles at acknowledgement."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in ("async function addTagToFolder(", "async function removeTagFromFolder("):
            body = _fn_body(api, fn)
            with self.subTest(fn=fn):
                self.assertNotIn("prksGuardFolderMutation(", body)
                self.assertNotIn("prksRequest(", body)
                self.assertIn("prksEnqueueFolderTag(", body)
        # Direct durable enqueue helper must exist for non-mounted call sites.
        self.assertIn("async function prksEnqueueFolderTag(", api)
        self.assertIn("coalesceFolderTag(", api)
        for fn in ("async function createFolder(", "async function patchFolder(",
                   "async function deleteFolderCanonical(", "async function addWorkToFolder(",
                   "async function patchWorkFolder("):
            body = _fn_body(api, fn)
            with self.subTest(fn=fn):
                self.assertNotIn("prksGuardFolderMutation(", body)
                self.assertNotIn("prksRequest(", body)
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        for reconciler in ("async function reconcileCreatedFolder(",
                           "async function reconcileFolderField(",
                           "async function reconcileWorkFolder(",
                           "async function reconcileDeletedFolder(",
                           "async function reconcileFolderTag("):
            self.assertIn(reconciler, runtime, reconciler)

    def test_no_production_surface_writes_folders_outside_the_wrappers(self):
        """A raw Folder write anywhere else silently reopens the guard gap.

        api.js holds the canonical wrappers. Folder private-notes autosave
        goes through patchFolder (SET_FOLDER_FIELD). Everywhere else must
        also go through a wrapper — never a raw /api/folders mutation.
        """
        for name in ("app.js", "components/folders.js", "components/processing-files.js",
                     "components/works.js", "ui.js"):
            src = _read(os.path.join(_FRONTEND, "js", *name.split("/")))
            with self.subTest(module=name):
                # A write is a /api/folders URL paired with a mutating method.
                for chunk in src.split("/api/folders")[1:]:
                    window = chunk[:240]
                    for method in ("'POST'", "'PATCH'", "'DELETE'", "'PUT'"):
                        self.assertNotIn(method, window,
                                         "%s writes /api/folders directly" % name)
        ui = _read(os.path.join(_FRONTEND, "js", "ui.js"))
        folder_notes = _fn_body(ui, "function prksEnqueuePrivateNotesSave(")
        self.assertIn("patchFolder(", folder_notes)
        self.assertNotIn("prksRequest(", folder_notes)
        self.assertNotIn("Documented exception to the canonical-Folder-wrapper rule", ui)

    def test_folder_title_rename_evicts_member_work_snapshots(self):
        """A cached Work detail embeds `folder_title`. The acknowledgement NAMES
        the members, so exactly those are staled -- narrow, canonical, and
        independent of which page is focused."""
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        at = runtime.index("async function reconcileFolderField(")
        body = runtime[at : runtime.index("\n        /**", at)]
        self.assertIn("result.member_work_ids", body)
        self.assertIn("invalidateEntity('work', members[i])", body)
        backend = _read(os.path.join(_PROJECT_DIR, "backend", "folder_sync.py"))
        self.assertIn('result["member_work_ids"]', backend)

    def test_folder_parent_id_must_be_present_not_merely_nullish(self):
        """A truncated HTTP-200 row must not be read as a root folder."""
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        body = _fn_body(app, "function prksIsFolderParentId(")
        self.assertIn("hasOwnProperty.call(row, 'parent_id')", body)
        # `undefined` must NOT be accepted as equivalent to canonical null.
        self.assertNotIn("value === undefined", body)
        # Both index rows and detail must go through it.
        self.assertIn("prksIsFolderParentId(row) &&", app)
        self.assertIn("!prksIsFolderParentId(value)", app)

    def test_tag_mutations_publish_coherence_from_the_server_answer(self):
        """Deleting and merging are durable: coherence follows the
        acknowledgement. The ordinary HTTP merge path (if any residual caller
        remains) still publishes through prksPublishTagCoherence; the durable
        wrappers reconcile the same affected ids at ACK."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        self.assertNotIn("async function deleteTag(", api)
        body = _fn_body(api, "async function mergeTags(")
        self.assertIn("prksMergeTagDurably(", body)
        self.assertNotIn("prksGuardFolderMutation(", body)
        self.assertNotIn("prksPublishTagCoherence(data)", body)
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        at = runtime.index("async function reconcileDeletedTag(")
        deleted = runtime[at : runtime.index("\n        /**", at)]
        # The catalogue is PATCHED; only the Works/Folders the answer NAMES are staled.
        self.assertIn("TAGS_LIST_KEY", deleted)
        self.assertIn("result.affected_work_ids", deleted)
        self.assertIn("result.affected_folder_ids", deleted)
        self.assertIn("invalidateEntity('work-tag-options'", deleted)
        self.assertIn("invalidateEntity('folder-tag-options'", deleted)
        merge_at = runtime.index("async function reconcileMergedTag(")
        merged = runtime[merge_at : runtime.index("\n        /* ---- Person Groups", merge_at)]
        self.assertIn("result.canonical_tag_id", merged)
        self.assertIn("result.affected_folder_ids", merged)
        publish = _fn_body(api, "function prksPublishTagCoherence(")
        self.assertIn("affected_folder_ids", publish)
        self.assertIn("affected_work_ids", publish)
        self.assertIn("prksMarkFoldersDomainChanged()", publish)
        self.assertIn("prksOfflineMarkEntityChanged('work', workId)", publish)

    def test_tags_component_has_no_raw_delete_or_merge(self):
        tags = _read(os.path.join(_FRONTEND, "js", "components", "tags.js"))
        self.assertNotIn("'/api/tags/merge'", tags)
        # Alias endpoints stay raw on purpose: aliases live in `tag_aliases`
        # and never appear in a cached work.tags[] / folder.tags[].
        for chunk in tags.split("/api/tags/")[1:]:
            window = chunk[:200]
            if "aliases" in window:
                continue
            self.assertNotIn("'DELETE'", window, "raw Tag delete in tags.js")

    def test_work_display_hooks_reach_folders(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        title_at = api.index("function prksMarkWorkTitleChanged(")
        self.assertIn("prksMarkFoldersDomainChanged();", api[title_at : title_at + 1200])
        role_at = api.index("function prksMarkWorkRoleDependenciesChanged(")
        role_body = api[role_at : role_at + 1200]
        # Author AND Editor -- the card credit line falls back to primary_editor.
        self.assertIn("role === 'Author' || role === 'Editor'", role_body)

    def test_cached_folder_detail_suppresses_thumbnails(self):
        folders = _read(os.path.join(_FRONTEND, "js", "components", "folders.js"))
        self.assertIn("suppressThumbnail: true", folders)
        # Lazy hydration must be skipped too, not just the src.
        self.assertIn("if (!offlineCached && typeof window.prksInitLazyWorkThumbs", folders)

    def test_recently_added_reads_through_its_own_offline_snapshot(self):
        """Recently added is cached now, so it is no longer connectivity-gated:
        it must go through the read-through rather than a raw fetch, and must
        never be recomputed from the Folder hierarchy or the stable catalog."""
        folders = _read(os.path.join(_FRONTEND, "js", "components", "folders.js"))
        body = _fn_body(folders, "async function prksLoadFolderLibraryRecentlyAdded(")
        self.assertIn("prksOfflineRecentlyAddedFetch", body)
        self.assertIn("prksResolveOfflineRecentlyAdded", body)
        self.assertNotIn("fetchRecentlyAdded", body)
        # A missing snapshot is an explicit unavailable state, not an empty tab.
        self.assertIn("not available offline", body)
        # The tab must no longer be disabled while offline.
        self.assertNotIn("Recently added requires a connection", folders)

    def test_home_glance_never_warms_independent_browse_domains(self):
        """Boot on #/folders must not populate works-browse:index or recent:index
        as a side effect of At-a-glance — those missing snapshots are the
        Progress/Types/Recent offline-unavailable signal."""
        folders = _read(os.path.join(_FRONTEND, "js", "components", "folders.js"))
        body = _fn_body(folders, "async function prksCollectFolderLibraryGlanceExtras(")
        self.assertNotIn("await prksOfflineBrowseFetch", body)
        self.assertNotIn("await prksOfflineWorksBrowseFetch", body)
        self.assertNotIn("await prksOfflineRecentlyAddedFetch", body)
        self.assertNotIn("prksOfflineBrowseFetch(", body)
        self.assertNotIn("prksOfflineWorksBrowseFetch(", body)
        self.assertIn("prksPeekCachedBrowseList", body)
        self.assertIn("recent:index", body)
        self.assertIn("works-browse:index", body)

    def test_work_folder_card_does_not_fetch_the_catalog_offline(self):
        folders = _read(os.path.join(_FRONTEND, "js", "components", "folders.js"))
        body = _fn_body(folders, "async function mountFolderAttachControlsForWork(")
        guard_at = body.index("if (!prksFolderRuntimeOnline())")
        fetch_at = body.index("await fetchFolders()")
        self.assertLess(guard_at, fetch_at)

    def test_work_folder_offline_policy_matches_playlist_clear_new(self):
        folders = _read(os.path.join(_FRONTEND, "js", "components", "folders.js"))
        self.assertIn("PRKS_WORK_FOLDER_MUTATION_SELECTOR", folders)
        start = folders.index("const PRKS_WORK_FOLDER_MUTATION_SELECTOR")
        # Bound the declaration tightly — Clear/New appear later as live controls.
        decl = folders[start : start + 180]
        self.assertIn("prks-work-folder-search", decl)
        self.assertIn("prks-work-folder-set-btn", decl)
        self.assertNotIn("prks-work-folder-clear-btn", decl)
        self.assertNotIn("prks-work-folder-new-btn", decl)
        open_body = _fn_body(folders, "function prksOpenFolderModalFromLibrarySearch(")
        self.assertNotIn("prksOfflineGuardMutation", open_body)
        apply = _fn_body(folders, "function prksApplyWorkFolderOfflineState(")
        self.assertIn("prks-work-folder-clear-btn", apply)
        self.assertIn("el.disabled = false", apply)


class FrontendBrowseProjectionTests(unittest.TestCase):
    """Static contracts for the three browse projections.

    The invariant these guard is independence: opening a Work must cost the
    Recent cache and nothing else, which only holds while the three keys stay
    separate and every component goes through the semantic helpers.
    """

    def test_three_independent_keys_and_domains(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        for const, value in (
            ("PRKS_WORKS_BROWSE_LIST_KEY", "'works-browse:index'"),
            ("PRKS_RECENT_LIST_KEY", "'recent:index'"),
            ("PRKS_RECENTLY_ADDED_LIST_KEY", "'recently-added:index'"),
        ):
            self.assertIn("const %s = %s;" % (const, value), app)
        # One catalog carrying last_opened_at is exactly what this design avoids.
        browse = _fn_body(app, "async function prksOfflineWorksBrowseFetch(")
        self.assertIn("projection=browse", browse)
        self.assertNotIn("last_opened_at", browse)

    def test_browse_routes_use_the_read_through_not_raw_fetches(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        for case, key in (("case 'progress': {", "PRKS_WORKS_BROWSE"),
                          ("case 'types': {", "PRKS_WORKS_BROWSE"),
                          ("case 'type-detail': {", "PRKS_WORKS_BROWSE"),
                          ("case 'recent': {", "PRKS_RECENT_LIST_KEY")):
            at = app.index(case)
            body = app[at: at + 1400]
            with self.subTest(case=case):
                self.assertNotIn("fetchWorks(", body)
                self.assertNotIn("fetchRecent(", body)
                self.assertIn("prksOffline", body)

    def test_recent_is_never_recomputed_from_the_stable_catalog(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        at = app.index("case 'recent': {")
        body = app[at: at + 1400]
        # Its canonical order (last_opened_at + id tie-break) is not derivable
        # from a catalog that does not carry last_opened_at at all.
        self.assertIn("PRKS_RECENT_LIST_KEY", body)
        self.assertNotIn("PRKS_WORKS_BROWSE_LIST_KEY", body)

    def test_opening_a_work_records_an_explicit_open_event_only(self):
        """The Work route is the ONLY genuine foreground open. It records an
        explicit durable event rather than relying on a side effect of reading,
        and it touches no other browse projection."""
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        at = app.index("case 'work': {")
        nxt = app.find("case 'concepts':", at)
        body = app[at: nxt if nxt > at else at + 8000]
        self.assertIn("prksRecordWorkOpened(offlineWork.value)", body)
        for forbidden in ("prksMarkWorksBrowseChanged", "prksMarkRecentlyAddedChanged",
                          "prksMarkWorkBrowseDisplayChanged"):
            self.assertNotIn(forbidden, body, forbidden)
        # A Work opened from cache while PRKS is unreachable is just as
        # genuinely opened, so the event is no longer gated on a server read.
        self.assertNotIn("offlineWork.source === 'server'", body)

    def test_only_the_work_route_records_an_open_event(self):
        """Every other fetchWorkDetails() call site is an INTERNAL refresh --
        a post-save reload, or a folder/playlist/tag/role/notes refresh. None
        may make a Work look recently opened, nor stale recent:index."""
        for name in ("ui.js", "components/folders.js", "components/playlists.js",
                     "components/works.js", "components/works-pdf.js",
                     "components/people.js", "components/tags.js"):
            src = _read(os.path.join(_FRONTEND, "js", *name.split("/")))
            with self.subTest(module=name):
                self.assertNotIn("prksRecordWorkOpened", src)
                self.assertNotIn("/opened", src)
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        self.assertEqual(app.count("prksRecordWorkOpened("), 1)

    def test_the_browser_has_exactly_one_open_event_path(self):
        """`POST /api/works/:id/opened` survives for other canonical callers,
        but the browser must not keep a second client route to it: two client
        paths would mean online and offline opens diverge, which is the whole
        defect the durable queue exists to prevent."""
        endpoint = re.compile(r"/opened['\"`]")
        for path in pathlib.Path(_FRONTEND, "js").rglob("*.js"):
            with self.subTest(module=path.name):
                self.assertIsNone(endpoint.search(path.read_text(encoding="utf-8")))

    def test_recording_an_open_never_blocks_showing_the_work(self):
        """Activity metadata is not the research content. A durable-write
        failure costs a Recent ordering; refusing to show the Work over it
        would cost the user the thing they actually asked for."""
        module = _read(os.path.join(_FRONTEND, "js", "work-open-state.js"))
        at = module.index("async function recordOpened(")
        body = module[at: module.index("/* ---- sync handler ---- */", at)]
        self.assertIn("catch (_)", body)
        self.assertIn("return false", body)
        self.assertNotIn("prksAlertMessage", body)
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        at = app.index("case 'work': {")
        nxt = app.find("case 'concepts':", at)
        work_case = app[at: nxt if nxt > at else at + 8000]
        # Fire-and-forget: never awaited on the render path.
        self.assertIn("void prksRecordWorkOpened(", work_case)
        self.assertNotIn("await prksRecordWorkOpened(", work_case)

    def test_work_route_classifies_pending_delete_without_empty_ops_race(self):
        """Pending DELETE_WORK keeps the disposable cache until ACK. The Work
        route must not race listOperations against an empty fallback and then
        treat a missing answer as "no pending delete" — that re-renders a
        tombstoned Work from cache. Cached opens classify via the targeted
        persisted lifecycle marker (not a full queue scan / listOperations)."""
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        lifecycle = _read(os.path.join(_FRONTEND, "js", "work-lifecycle-state.js"))
        store = _read(os.path.join(_FRONTEND, "js", "local-store.js"))
        at = app.index("case 'work': {")
        nxt = app.find("case 'concepts':", at)
        body = app[at: nxt if nxt > at else at + 5500]
        self.assertIn("prksResolveWorkLifecycle", body)
        self.assertIn("prksApplyLiveWorkLifecycleFromOperations", body)
        # The empty-ops Promise.race fallback was the regression.
        self.assertNotIn("Promise.resolve({ ops: null })", body)
        self.assertNotIn("raced.ops || []", body)
        # Cached + not deleted: paint via detail fetch; ops are background.
        paint = body[body.index("} else {\n                        offlineWork = await prksOfflineDetailFetch"):
                     body.index("void workOpsPromise.then") + 80]
        self.assertIn("prksOfflineDetailFetch", paint)
        self.assertIn("void workOpsPromise.then", paint)
        self.assertNotIn("workOps = await workOpsPromise", paint)
        self.assertIn("prksResolveWorkLifecycle", lifecycle)
        self.assertIn("getWorkLifecycle", store)
        self.assertIn("work-lifecycle:", store)
        self.assertIn("putWorkLifecycleIn", store)
        self.assertIn("clearWorkLifecycleIfOwnedIn", store)
        # CREATE ACK must not clear a DELETE-owned marker.
        self.assertIn("op_id", store[store.index("putWorkLifecycleIn"):
                                     store.index("putWorkLifecycleIn") + 400])

    def test_semantic_helpers_are_the_only_invalidation_path(self):
        """A future sync coordinator needs ONE place to turn "discard" into
        "apply the pending operation optimistically"."""
        for name in ("app.js", "ui.js", "components/folders.js", "components/works.js",
                     "components/progress.js", "components/types.js", "components/search.js"):
            src = _read(os.path.join(_FRONTEND, "js", *name.split("/")))
            with self.subTest(module=name):
                for key in ("works-browse:index", "recent:index", "recently-added:index"):
                    self.assertNotIn("deleteList('%s')" % key, src)
                    self.assertNotIn('deleteList("%s")' % key, src)

    def test_work_create_marks_catalog_and_recently_added_but_not_recent(self):
        """PDF create still publishes at the call site; video CREATE_WORK ACKs
        through reconcileCreatedWork with the same domains (never Recent)."""
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        at = app.index("if (res.ok && typeof prksMarkWorksBrowseChanged === 'function')")
        body = app[at: at + 600]
        self.assertIn("prksMarkWorksBrowseChanged();", body)
        self.assertIn("prksMarkRecentlyAddedChanged();", body)
        # A new Work has last_opened_at NULL, so it cannot be in Recent.
        self.assertNotIn("prksMarkRecentChanged();", body)
        self.assertIn("prksCreateWorkDurably", app)
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        at = runtime.index("async function reconcileCreatedWork(")
        end = runtime.index("\n        /**", at)
        reconcile = runtime[at:end]
        self.assertIn("prksMarkWorksBrowseChanged()", reconcile)
        self.assertIn("prksMarkRecentlyAddedChanged()", reconcile)
        self.assertIn("prksMarkFoldersDomainChanged()", reconcile)
        self.assertNotIn("prksMarkRecentChanged()", reconcile)

    def test_folder_membership_marks_recently_added_only(self):
        """Filing a Work is durable now, so its coherence moved to the
        reconciler -- the canonical change is the acknowledgement."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in ("async function addWorkToFolder(", "async function patchWorkFolder("):
            body = _fn_body(api, fn)
            with self.subTest(fn=fn):
                self.assertIn("prksFileWorkInFolder(", body)
        runtime = _read(os.path.join(_FRONTEND, "js", "offline-runtime.js"))
        at = runtime.index("async function reconcileWorkFolder(")
        body = runtime[at : runtime.index("\n        /**", at)]
        self.assertIn("prksOfflineMarkRecentlyAddedChanged();", body)
        # The Work's own snapshot carries `folder_title`, and the answer states
        # it exactly -- so it is PATCHED rather than dropped.
        self.assertIn("folder_title: result.folder_title", body)

    def test_display_hooks_reach_all_three_projections(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        display = _fn_body(api, "function prksMarkWorkBrowseDisplayChanged(")
        for helper in ("prksMarkWorksBrowseChanged()", "prksMarkRecentChanged()",
                       "prksMarkRecentlyAddedChanged()"):
            self.assertIn(helper, display)
        title = _fn_body(api, "function prksMarkWorkTitleChanged(")
        self.assertIn("prksMarkWorkBrowseDisplayChanged();", title)

    def test_projection_validators_require_each_routes_own_field(self):
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        browse = _fn_body(app, "function prksIsWorksBrowseRowShape(")
        self.assertIn("hasOwnProperty.call(row, 'abstract_excerpt')", browse)
        recent = _fn_body(app, "function prksIsRecentRowShape(")
        self.assertIn("row.last_opened_at", recent)
        added = _fn_body(app, "function prksIsRecentlyAddedRowShape(")
        self.assertIn("row.created_at", added)
        self.assertIn("prksIsBrowseFolderId(row)", added)
        # folder_id must be present, not merely nullish -- same rule as parent_id.
        folder_id = _fn_body(app, "function prksIsBrowseFolderId(")
        self.assertIn("hasOwnProperty.call(row, 'folder_id')", folder_id)

    def test_cached_browse_renders_suppress_thumbnails(self):
        for name, fn in (("components/progress.js", "function renderProgressByStatus("),
                         ("components/search.js", "function renderRecent("),
                         ("components/types.js", "function renderWorksByDocType(")):
            src = _read(os.path.join(_FRONTEND, "js", *name.split("/")))
            with self.subTest(module=name):
                self.assertIn("offlineCached", src)
                self.assertIn("suppressThumbnail", src)

    def test_progress_reads_the_bounded_excerpt(self):
        src = _read(os.path.join(_FRONTEND, "js", "components", "progress.js"))
        body = _fn_body(src, "function renderProgressByStatus(")
        self.assertIn("abstract_excerpt", body)
