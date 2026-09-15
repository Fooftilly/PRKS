"""Durable Arguments/Stances: identity, overlays, aggregates and dependencies."""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.research_network import (
    get_argument,
    replace_argument_sources,
    replace_argument_targets,
    update_argument,
)
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    ARGUMENT_A_NAME,
    ARGUMENT_B_NAME,
    ARGUMENT_C_NAME,
    POSITION_ARGUMENT_NAME,
    seed_arguments_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class DurableArgumentTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_arguments_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers="allow")
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        self.index(page)
        o._wait_list_cached(page, "arguments:index")
        return server, page, context

    def db_for(self, server):
        db = getattr(self, "_db", None)
        if db is None:
            db = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
            self._db = db
        return db

    def index(self, page, kind=""):
        route = "#/arguments" + (("?kind=" + kind) if kind else "")
        page.evaluate("route => prksNavigate(route)", route)

    def detail(self, page, argument_id):
        page.evaluate("id => prksNavigate('#/arguments/' + id)", argument_id)

    def prepare(self, page, argument_id):
        self.detail(page, argument_id)
        o._wait_entity_cached(page, "argument", argument_id)
        page.evaluate("id => prksReadArgumentState(id)", argument_id)
        o._wait_entity_cached(page, "argument-state", argument_id)

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate("() => prksOfflineNoteRequestFailure()")
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def wait_family(self, page, family):
        wait_for_async(page,
            "family => prksSync.store.listOperations().then(rows => rows.some("
            "op => op.operation === family))", arg=family, timeout=30000,
            message=family + " was never enqueued")

    def drained(self, page):
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=50000, message="durable queue did not drain")

    def settled(self, page):
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(rows => !rows.some("
            "op => op.status === 'pending' || op.status === 'syncing'))",
            timeout=50000, message="durable queue did not settle")

    def test_offline_argument_and_stance_survive_reload_and_sync_once(self):
        server, page, context = self.start()
        self.offline(page, context)
        ids = page.evaluate("""async () => [
            (await createArgument({name:'Offline Argument',kind:'argument'})).id,
            (await createArgument({name:'Offline Stance',kind:'stance'})).id
        ]""")
        for entity_id in ids:
            self.assertRegex(entity_id, r"^A-[0-9A-F]{32}$")
        self.index(page, "argument"); o._wait_content_contains(page, "Offline Argument")
        self.index(page, "stance"); o._wait_content_contains(page, "Offline Stance")

        page.reload(wait_until="domcontentloaded"); page.wait_for_selector("#sidebar")
        self.offline(page, context)
        self.index(page, "argument"); o._wait_content_contains(page, "Offline Argument")
        self.index(page, "stance"); o._wait_content_contains(page, "Offline Stance")
        self.detail(page, ids[0]); o._wait_content_contains(page, "Offline Argument")

        self.reconnect(page, context); self.drained(page)
        rows = self.db_for(server).execute_query(
            "SELECT id, name FROM arguments WHERE name IN (?, ?)",
            ("Offline Argument", "Offline Stance"))
        self.assertEqual({row["id"] for row in rows}, set(ids))
        self.assertEqual(len(rows), 2, "one canonical row per construction")

    def test_scalar_edits_compose_move_tabs_and_cancel(self):
        server, page, context = self.start()
        argument = server.ids["argument_a"]
        other = server.ids["argument_target"]
        self.prepare(page, argument)
        self.prepare(page, other)
        self.offline(page, context)
        page.evaluate("""id => Promise.all([
            updateArgument(id,{name:'Offline Rename'}),
            updateArgument(id,{kind:'stance'}),
            updateArgument(id,{main_text:'Offline body'})
        ])""", argument)
        self.index(page, "argument")
        page.wait_for_function("name => !document.body.innerText.includes(name)", arg=ARGUMENT_A_NAME)
        self.index(page, "stance"); o._wait_content_contains(page, "Offline Rename")
        self.detail(page, argument); o._wait_content_contains(page, "Offline body")
        fields = page.evaluate("() => prksSync.store.listOperations().then(rows => rows.filter("
            "op => op.operation === 'SET_ARGUMENT_FIELD').map(op => op.payload.field).sort())")
        self.assertEqual(fields, ["kind", "main_text", "name"])

        # Separate acknowledged Argument: A -> B -> A leaves no scalar intent.
        page.evaluate("id => updateArgument(id,{name:'Temporary'})", other)
        self.wait_family(page, "SET_ARGUMENT_FIELD")
        page.evaluate("id => updateArgument(id,{name:'E2E Target Argument'})", other)
        remaining = page.evaluate("id => prksSync.store.listOperations().then(rows => rows.filter("
            "op => op.operation === 'SET_ARGUMENT_FIELD' && op.entity_id === id).length)", other)
        self.assertEqual(remaining, 0)

        self.reconnect(page, context); self.drained(page)
        stored = get_argument(self.db_for(server), argument)
        self.assertEqual((stored["name"], stored["kind"], stored["main_text"]),
                         ("Offline Rename", "stance", "Offline body"))

    def test_ordered_source_and_target_replacements_survive_reload(self):
        server, page, context = self.start()
        argument = server.ids["argument_a"]
        self.prepare(page, argument); self.offline(page, context)
        works = [server.ids["work_b"], server.ids["work_a"]]
        targets = [
            {"type": "argument", "id": server.ids["argument_target"], "verdict_id": "qualifies"},
            {"type": "position", "id": server.ids["position_a"], "verdict_id": "opposes"},
        ]
        page.evaluate("([id,works,targets]) => Promise.all(["
            "putArgumentSources(id, works.map((work_id,i)=>({work_id,pages:String(i+1)}))),"
            "putArgumentTargets(id,targets)])", [argument, works, targets])
        self.wait_family(page, "SET_ARGUMENT_SOURCES")
        self.wait_family(page, "SET_ARGUMENT_TARGETS")
        page.reload(wait_until="domcontentloaded"); page.wait_for_selector("#sidebar")
        self.offline(page, context); self.detail(page, argument)
        o._wait_content_contains(page, "pp. 1")

        self.reconnect(page, context); self.drained(page)
        stored = get_argument(self.db_for(server), argument)
        self.assertEqual([row["work_id"] for row in stored["sources"]], works)
        self.assertEqual([(row["type"], row["id"], row["verdict_id"])
                          for row in stored["targets"]],
                         [(row["type"], row["id"], row["verdict_id"]) for row in targets])

    def test_stale_aggregate_conflicts_instead_of_merging(self):
        server, page, context = self.start()
        argument = server.ids["argument_a"]
        self.prepare(page, argument)
        replace_argument_sources(self.db_for(server), argument,
                                 [{"work_id": server.ids["work_b"], "pages": "server"}])
        self.offline(page, context)
        page.evaluate("([id,work]) => putArgumentSources(id,[{work_id:work,pages:'device'}])",
                      [argument, server.ids["work_a"]])
        self.reconnect(page, context); self.settled(page)
        conflict = page.evaluate("() => prksSync.store.listOperations().then(rows => rows.find("
            "op => op.operation === 'SET_ARGUMENT_SOURCES').server_result)")
        self.assertEqual(conflict["code"], "REVISION_CONFLICT")
        self.assertEqual(get_argument(self.db_for(server), argument)["sources"][0]["pages"], "server")

    def test_response_and_create_from_work_are_atomic_constructions(self):
        server, page, context = self.start()
        parent = server.ids["argument_a"]
        self.prepare(page, parent); self.offline(page, context)
        response = page.evaluate("id => createArgument({name:'Offline Response',kind:'argument',"
            "targets:[{type:'argument',id,verdict_id:'opposes'}]})", parent)
        from_work = page.evaluate("workId => prksCreateArgumentFromWork({"
            "name:'Offline Work Argument',kind:'stance',workId,pages:'44'})", server.ids["work_a"])
        ops = page.evaluate("() => prksSync.store.listOperations().then(rows => rows.filter("
            "op => op.operation === 'CREATE_ARGUMENT').map(op => op.payload))")
        by_name = {op["name"]: op for op in ops}
        self.assertEqual(by_name["Offline Response"]["targets"],
                         [{"type": "argument", "id": parent, "verdict_id": "opposes"}])
        self.assertEqual(by_name["Offline Work Argument"]["sources"],
                         [{"work_id": server.ids["work_a"], "pages": "44"}])
        self.assertIsNotNone(response); self.assertIsNotNone(from_work)
        self.reconnect(page, context); self.drained(page)
        self.assertEqual(get_argument(self.db_for(server), response["id"])["targets"][0]["id"], parent)
        self.assertEqual(get_argument(self.db_for(server), from_work["id"])["sources"][0]["work_id"],
                         server.ids["work_a"])

    def test_position_argument_argument_dependency_chain_and_failure(self):
        _server, page, context = self.start()
        self.offline(page, context)
        chain = page.evaluate("""async () => {
            const p=await createPosition({name:'Pending Root'});
            const a=await createArgument({name:'Pending A',targets:[
                {type:'position',id:p.id,verdict_id:'supports'}]});
            const b=await createArgument({name:'Pending B',targets:[
                {type:'argument',id:a.id,verdict_id:'opposes'}]});
            const rows=await prksSync.store.listOperations();
            return {p,a,b,rows};
        }""")
        by_entity = {row["entity_id"]: row for row in chain["rows"]}
        self.assertEqual(by_entity[chain["a"]["id"]]["depends_on"],
                         [by_entity[chain["p"]["id"]]["op_id"]])
        self.assertEqual(by_entity[chain["b"]["id"]]["depends_on"],
                         [by_entity[chain["a"]["id"]]["op_id"]])
        failed = page.evaluate("""async rootId => {
            const rows=await prksSync.store.listOperations();
            const root=rows.find(op=>op.entity_id===rootId);
            await prksSync.store.updateOperationSyncState(root.op_id,{status:'conflict',
                server_result:{code:'INVALID_ENVELOPE'}});
            await prksSync.store.resolveConflict(root.op_id,false);
            return await prksSync.store.listOperations();
        }""", chain["p"]["id"])
        descendants = [row for row in failed if row["entity_id"] in
                       (chain["a"]["id"], chain["b"]["id"])]
        self.assertEqual([row["server_result"]["code"] for row in descendants],
                         ["DEPENDENCY_FAILED", "DEPENDENCY_FAILED"])

    def test_invalid_initial_connection_refuses_without_standalone_row(self):
        server, page, context = self.start(); self.offline(page, context)
        created = page.evaluate("() => createArgument({name:'Must Stay Atomic',targets:["
            "{type:'position',id:'P-MISSING',verdict_id:'supports'}]})")
        self.reconnect(page, context); self.settled(page)
        result = page.evaluate("id => prksSync.store.listOperations().then(rows => rows.find("
            "op => op.entity_id === id).server_result)", created["id"])
        self.assertEqual(result["code"], "POSITION_NOT_FOUND")
        rows = self.db_for(server).execute_query("SELECT id FROM arguments WHERE id = ?",
                                                 (created["id"],))
        self.assertEqual(rows, [], "refused construction rolls INSERT back")

    def test_delete_tombstone_ack_and_both_refusals(self):
        server, page, context = self.start()
        safe = server.ids["argument_unvisited"]
        self.prepare(page, safe); self.offline(page, context)
        page.evaluate("id => deleteArgument(id)", safe); self.wait_family(page, "DELETE_ARGUMENT")
        self.index(page)
        page.wait_for_function("name => !document.body.innerText.includes(name)",
                               arg="E2E Unvisited Argument")
        page.reload(wait_until="domcontentloaded"); page.wait_for_selector("#sidebar")
        self.offline(page, context); self.index(page)
        page.wait_for_function("name => !document.body.innerText.includes(name)",
                               arg="E2E Unvisited Argument")
        self.reconnect(page, context); self.drained(page)
        self.assertIsNone(get_argument(self.db_for(server), safe))

        for entity_id, code, name in (
            (server.ids["argument_a"], "ARGUMENT_IN_USE", ARGUMENT_A_NAME),
            (server.ids["argument_target"], "ARGUMENT_TARGETED", ARGUMENT_B_NAME),
        ):
            with self.subTest(code=code):
                self.prepare(page, entity_id); self.offline(page, context)
                page.evaluate("id => deleteArgument(id)", entity_id)
                self.index(page)
                page.wait_for_function("name => !document.body.innerText.includes(name)", arg=name)
                self.reconnect(page, context); self.settled(page)
                result = page.evaluate("id => prksSync.store.listOperations().then(rows => rows.find("
                    "op => op.operation === 'DELETE_ARGUMENT' && op.entity_id === id).server_result)",
                    entity_id)
                self.assertEqual(result["code"], code)
                self.index(page); o._wait_content_contains(page, name)

    def test_pending_rename_reaches_position_argument_picker_and_graph(self):
        server, page, context = self.start()
        argument = server.ids["position_argument"]
        self.prepare(page, argument)
        self.prepare(page, server.ids["argument_target"])
        self.prepare(page, server.ids["argument_response"])
        # Warm every acknowledged surface before disconnect.
        self.detail(page, server.ids["argument_a"])
        o._wait_entity_cached(page, "argument", server.ids["argument_a"])
        page.evaluate("id => prksNavigate('#/positions/' + id)", server.ids["position_a"])
        o._wait_content_contains(page, server.ids["position_a_name"])
        o._wait_entity_cached(page, "position", server.ids["position_a"])
        page.evaluate("() => prksNavigate('#/graph')")
        page.wait_for_function("() => { const d=prksGetResearchGraphDebug(); return !!(d&&d.cy); }")
        o._wait_entity_cached(page, "research-graph-core", "snapshot")
        self.offline(page, context)
        page.evaluate("id => updateArgument(id,{name:'Pending Argument Name'})", argument)

        page.evaluate("id => prksNavigate('#/positions/' + id)", server.ids["position_a"])
        o._wait_content_contains(page, "Pending Argument Name")
        picker_name = page.evaluate("id => fetchArguments().then(rows => rows.find(x=>x.id===id).name)",
                                    argument)
        self.assertEqual(picker_name, "Pending Argument Name")
        graph_name = page.evaluate("""async id => {
            await prksRefreshPendingArgumentNames();
            const result=await prksOfflineResearchGraphFetch(false);
            const node=result.snapshot.nodes.find(n=>n.type==='argument'&&n.record_id===id);
            return node&&node.label;
        }""", argument)
        self.assertEqual(graph_name, "Pending Argument Name")

        # Another Argument's target and response rows use same hydrated map.
        target = server.ids["argument_target"]
        page.evaluate("id => updateArgument(id,{name:'Pending Target Name'})", target)
        self.detail(page, server.ids["argument_a"])
        o._wait_content_contains(page, "Pending Target Name")
        response = server.ids["argument_response"]
        page.evaluate("id => updateArgument(id,{name:'Pending Response Name'})", response)
        self.detail(page, server.ids["argument_a"])
        o._wait_content_contains(page, "Pending Response Name")


if __name__ == "__main__":
    unittest.main()
