"""The Research HTTP routes served from PostgreSQL (L-R-1 #634; the route seam PG-C1 #640 binds).

``server.py`` reaches Research records only through its bound ``db`` and
mentions and the graph only through its bound ``research_index``, and with
``PRKS_AUTH`` it binds the admitted :class:`RequestContext` for the route.
These tests bind :class:`~backend.pg_facade.PostgresLibrary` and
:class:`~backend.pg_facade.research_index.PostgresResearchIndex` there (the
way the cutover will), sign in over HTTP, and check that the routes answer
from PostgreSQL with today's statuses, bodies and refusal codes: Concept,
Position and Argument CRUD, Version-aware citations, the Research Note save
and its references with ``pending`` until ``research.reindex_work`` runs,
mention counts, backlinks and the graph from the derived index, a reader's
refused writes, and another Library's records out of reach. The browser
suite is not repeated here; the SQLite routes keep their own tests.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext
from backend.jobs import queue
from backend.jobs.runner import Outcome, run_job
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.pg_facade.research_index import PostgresResearchIndex
from tests.pg.facade_context import provision_actor
from tests.pg.http_fixtures import HttpAuthTestCase, Response
from tests.pg.jobs_fixtures import LEASE

_MISSING_ARGUMENT = "A-" + "0" * 32


class ResearchHttpTestCase(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        from backend.domain.research import reindex  # noqa: F401  registers research.reindex_work

        module = self.server_module
        previous = (module.db, module.research_index)
        self.sqlite = previous
        self.addCleanup(self._restore, previous)
        self.facade = PostgresLibrary(self.db, storage=module._bound_storage)
        module.db = self.facade
        module.research_index = PostgresResearchIndex(self.db)
        self.serve()
        self.cookies = self.session_cookie()

    def _restore(self, previous: tuple[Any, Any]) -> None:
        self.server_module.db, self.server_module.research_index = previous

    # -- requests ----------------------------------------------------------------------

    def get(self, path: str, cookies: Optional[dict[str, str]] = None) -> Response:
        return self.call("GET", path, cookies=cookies or self.cookies)

    def send(self, method: str, path: str, body: Any = None,
             cookies: Optional[dict[str, str]] = None) -> Response:
        return self.call(method, path, body=body, cookies=cookies or self.cookies,
                         epoch=self.library_epoch())

    def ok(self, res: Response, status: int = 200) -> Any:
        self.assertEqual(res.status, status, (res.raw, self.logs.getvalue()[-3000:]))
        return res.body

    def refused(self, res: Response, status: int, code: str) -> dict[str, Any]:
        self.assertEqual(res.status, status, res.raw)
        self.assertEqual(res.body["code"], code, res.raw)
        return res.body

    # -- records the routes do not create --------------------------------------------------

    def acting(self, cookies: Optional[dict[str, str]] = None) -> Any:
        """The owner's session as a facade context, for setup the Research routes do not cover."""
        token = (cookies or self.cookies)["prks_session"]
        valid = sessions.resolve_session(self.db, token)
        assert valid is not None
        return facade_context.acting_as(RequestContext.for_session(valid, epoch=self.library_epoch()))

    def work(self, title: str) -> str:
        with self.acting():
            return self.facade.add_work(title)

    def primary(self, work_id: str) -> str:
        return self.run_sql("SELECT primary_manifestation_id AS id FROM prks.works WHERE id = %s",
                            (work_id,))[0]["id"]

    def run_jobs(self) -> None:
        while (job := queue.claim(self.db, "http-worker", lease=LEASE)) is not None:
            self.assertIs(run_job(self.db, job, lease=LEASE), Outcome.SUCCEEDED)

    def note(self, work_id: str, text: Any) -> Response:
        return self.send("PATCH", f"/api/works/{work_id}", {"text_content": text})


class ConceptRouteTests(ResearchHttpTestCase):
    def test_create_read_update_and_delete(self) -> None:
        ethics = self.ok(self.send("POST", "/api/concepts", {"name": "Ethics", "description": "Conduct"}), 201)
        self.assertEqual((ethics["name"], ethics["description"]), ("Ethics", "Conduct"))
        self.refused(self.send("POST", "/api/concepts", {"name": "ETHICS"}), 409, "concept_exists")
        self.refused(self.send("POST", "/api/concepts", {"name": "  "}), 400, "invalid_name")
        logic = self.ok(self.send("POST", "/api/concepts", {"name": "Logic"}), 201)

        cid = ethics["id"]
        renamed = self.ok(self.send("PATCH", f"/api/concepts/{cid}", {"name": "Morality"}))
        self.assertEqual(renamed["name"], "Morality")
        aliased = self.ok(self.send("PUT", f"/api/concepts/{cid}/aliases", {"aliases": ["Ethics"]}))
        self.assertEqual([alias["alias"] if isinstance(alias, dict) else alias for alias in aliased["aliases"]],
                         ["Ethics"])
        parented = self.ok(self.send("PUT", f"/api/concepts/{cid}/parents", {"parent_ids": [logic["id"]]}))
        self.assertEqual([parent["id"] for parent in parented["parents"]], [logic["id"]])
        self.refused(self.send("PUT", f"/api/concepts/{logic['id']}/parents", {"parent_ids": [cid]}),
                     409, "concept_cycle")

        listed = self.ok(self.get("/api/concepts"))
        self.assertEqual({item["id"]: item["mention_count"] for item in listed}, {cid: 0, logic["id"]: 0})
        detail = self.ok(self.get(f"/api/concepts/{cid}"))
        self.assertEqual((detail["name"], detail["mention_count"], detail["mentions"]), ("Morality", 0, []))
        state = self.ok(self.get(f"/api/concepts/{cid}/sync-state"))
        self.assertEqual(state["concept_id"], cid)

        self.assertEqual(self.ok(self.send("DELETE", f"/api/concepts/{cid}")), {"status": "deleted"})
        self.assertEqual(self.get(f"/api/concepts/{cid}").status, 404)
        self.refused(self.send("DELETE", f"/api/concepts/{cid}"), 404, "not_found")


class PositionAndArgumentRouteTests(ResearchHttpTestCase):
    def test_positions(self) -> None:
        thesis = self.ok(self.send("POST", "/api/positions", {"name": "Thesis", "description": "About"}), 201)
        pid = thesis["id"]
        self.ok(self.send("PATCH", f"/api/positions/{pid}", {"description": "Revised"}))
        self.assertEqual(self.ok(self.get(f"/api/positions/{pid}"))["description"], "Revised")
        self.assertEqual([row["id"] for row in self.ok(self.get("/api/positions"))], [pid])
        self.assertEqual(self.ok(self.get(f"/api/positions/{pid}/sync-state"))["position_id"], pid)
        self.assertEqual(self.ok(self.send("DELETE", f"/api/positions/{pid}")), {"status": "deleted"})
        self.assertEqual(self.get(f"/api/positions/{pid}").status, 404)

    def test_arguments_with_targets_and_verdicts(self) -> None:
        thesis = self.ok(self.send("POST", "/api/positions", {"name": "Thesis"}), 201)["id"]
        claim = self.ok(self.send("POST", "/api/arguments", {
            "name": "Claim", "kind": "argument",
            "targets": [{"type": "position", "id": thesis, "verdict_id": "supports"}]}), 201)
        aid = claim["id"]
        self.assertEqual([t["id"] for t in claim["targets"]], [thesis])
        self.ok(self.send("PATCH", f"/api/arguments/{aid}", {"main_text": "Because."}))
        reply = self.ok(self.send("POST", "/api/arguments", {
            "name": "Reply", "kind": "stance",
            "targets": [{"type": "argument", "id": aid, "verdict_id": "opposes"}]}), 201)
        self.refused(self.send("DELETE", f"/api/arguments/{aid}"), 409, "argument_targeted")
        self.ok(self.send("PUT", f"/api/arguments/{reply['id']}/targets", {"targets": []}))

        verdicts = self.ok(self.get("/api/argument-verdicts"))
        self.assertIn("supports", [verdict["id"] for verdict in verdicts])
        detail = self.ok(self.get(f"/api/arguments/{aid}"))
        self.assertEqual((detail["main_text"], detail["mention_count"], detail["mentions"]), ("Because.", 0, []))
        self.assertEqual(detail["verdicts"], verdicts)
        self.assertEqual({item["id"] for item in self.ok(self.get("/api/arguments?kind=stance"))},
                         {reply["id"]})
        self.refused(self.get("/api/arguments?kind=nope"), 400, "invalid_kind")
        self.assertEqual(self.ok(self.get(f"/api/arguments/{aid}/sync-state"))["argument_id"], aid)
        self.assertEqual(self.ok(self.send("DELETE", f"/api/arguments/{aid}")), {"status": "deleted"})
        self.refused(self.send("PATCH", f"/api/arguments/{_MISSING_ARGUMENT}", {"name": "x"}), 404, "not_found")

    def test_version_aware_citations(self) -> None:
        book = self.work("Book")
        other = self.work("Other")
        version = self.primary(book)
        claim = self.ok(self.send("POST", "/api/arguments", {
            "name": "Claim", "kind": "argument",
            "sources": [{"work_id": book, "manifestation_id": version, "pages": "1-2"},
                        {"work_id": book, "manifestation_id": None, "pages": ""}]}), 201)
        self.assertEqual([(s["work_id"], s["manifestation_id"], s["pages"]) for s in claim["sources"]],
                         [(book, version, "1-2"), (book, None, "")])
        aid = claim["id"]
        self.refused(self.send("PUT", f"/api/arguments/{aid}/sources", {"sources": [
            {"work_id": book, "manifestation_id": "MF-" + "0" * 32, "pages": ""}]}),
            404, "argument_source_version_not_found")
        self.refused(self.send("PUT", f"/api/arguments/{aid}/sources", {"sources": [
            {"work_id": other, "manifestation_id": version, "pages": ""}]}),
            409, "argument_source_version_mismatch")
        legacy = self.ok(self.send("PUT", f"/api/arguments/{aid}/sources",
                                   {"sources": [{"work_id": other, "pages": "7"}]}))
        self.assertEqual([(s["work_id"], s["manifestation_id"]) for s in legacy["sources"]],
                         [(other, self.primary(other))])
        self.assertEqual(self.ok(self.get(f"/api/arguments/{aid}"))["sources"], legacy["sources"])


class ResearchNoteRouteTests(ResearchHttpTestCase):
    def test_note_refs_pending_until_the_reindex_runs(self) -> None:
        ethics = self.ok(self.send("POST", "/api/concepts", {"name": "Ethics"}), 201)["id"]
        claim = self.ok(self.send("POST", "/api/arguments", {"name": "Claim", "kind": "argument"}), 201)["id"]
        work = self.work("Commentary")
        self.assertEqual(self.ok(self.note(work, f"On [[concept:ethics]] and [[argument:{claim}|it]].")),
                         {"status": "updated"})
        detail = self.ok(self.get(f"/api/works/{work}"))
        self.assertIs(detail["research_refs"]["pending"], True)
        self.assertEqual(self.ok(self.get(f"/api/concepts/{ethics}"))["mention_count"], 0)

        self.run_jobs()
        refs = self.ok(self.get(f"/api/works/{work}"))["research_refs"]
        self.assertEqual(refs["pending"], False)
        self.assertEqual([c["id"] for c in refs["concepts"]], [ethics])
        self.assertEqual([a["id"] for a in refs["arguments"]], [claim])
        concept = self.ok(self.get(f"/api/concepts/{ethics}"))
        self.assertEqual(concept["mention_count"], 1)
        self.assertEqual([(m["work_id"], len(m["occurrences"])) for m in concept["mentions"]], [(work, 1)])
        self.assertEqual({c["id"]: c["mention_count"] for c in self.ok(self.get("/api/concepts"))}, {ethics: 1})
        argument = self.ok(self.get(f"/api/arguments/{claim}"))
        self.assertEqual((argument["mention_count"], [m["work_id"] for m in argument["mentions"]]), (1, [work]))
        self.assertEqual({a["id"]: a["mention_count"] for a in self.ok(self.get("/api/arguments"))}, {claim: 1})
        # The note blocks the deletes, as on SQLite.
        self.refused(self.send("DELETE", f"/api/concepts/{ethics}"), 409, "concept_in_use")
        self.refused(self.send("DELETE", f"/api/arguments/{claim}"), 409, "argument_in_use")
        # Nothing ran against the SQLite store or its index file.
        sqlite_db, sqlite_index = self.sqlite
        self.assertEqual((sqlite_db.list_concepts(), sqlite_db.list_arguments()), ([], []))
        self.assertEqual(sqlite_index.concept_mention_counts(), {})

    def test_a_note_names_new_concepts_and_refuses_bad_bodies(self) -> None:
        work = self.work("W")
        self.ok(self.note(work, "[[concept:Fresh]]"))
        self.assertEqual([c["name"] for c in self.ok(self.get("/api/concepts"))], ["Fresh"])
        self.refused(self.note(work, None), 400, "invalid_text")
        self.refused(self.note(work, "bad\x01"), 400, "invalid_text")
        self.refused(self.note("W-" + "0" * 32, "x"), 404, "not_found")

    def test_graph_follows_the_derived_index(self) -> None:
        ethics = self.ok(self.send("POST", "/api/concepts", {"name": "Ethics"}), 201)["id"]
        work = self.work("Commentary")
        self.ok(self.note(work, "[[concept:Ethics]]"))
        graph = self.ok(self.get("/api/research-graph"))
        self.assertEqual({node["id"] for node in graph["nodes"]}, {f"concept:{ethics}"})
        self.assertIs(graph["meta"]["derived_note_edges_available"], True)
        self.run_jobs()
        graph = self.ok(self.get("/api/research-graph?people=1"))
        self.assertIs(graph["meta"]["people_included"], True)
        edges = {(e["source"], e["target"]): e for e in graph["edges"] if e["type"] == "mentions_concept"}
        self.assertEqual(edges[(f"work:{work}", f"concept:{ethics}")]["count"], 1)


class ResearchAccessTests(ResearchHttpTestCase):
    def test_a_reader_reads_and_is_refused_every_write(self) -> None:
        ethics = self.ok(self.send("POST", "/api/concepts", {"name": "Ethics"}), 201)["id"]
        self.user("Reader", library=self.lib, role="reader")
        reader = self.session_cookie("Reader")
        self.assertEqual(self.ok(self.get(f"/api/concepts/{ethics}", cookies=reader))["name"], "Ethics")
        self.assertEqual(self.get("/api/research-graph", cookies=reader).status, 200)
        for method, path, body in (
            ("POST", "/api/concepts", {"name": "Logic"}),
            ("PATCH", f"/api/concepts/{ethics}", {"name": "Morals"}),
            ("PUT", f"/api/concepts/{ethics}/aliases", {"aliases": []}),
            ("DELETE", f"/api/concepts/{ethics}", None),
            ("POST", "/api/positions", {"name": "P"}),
            ("POST", "/api/arguments", {"name": "A", "kind": "argument"}),
        ):
            with self.subTest(method=method, path=path):
                res = self.send(method, path, body, cookies=reader)
                self.assertEqual((res.status, res.body["error"]), (403, "forbidden"), res.raw)
        self.assertEqual([c["name"] for c in self.ok(self.get("/api/concepts"))], ["Ethics"])

    def test_another_library_is_out_of_reach(self) -> None:
        """This server's Library is the session's; another Library's records answer as missing."""
        stranger = provision_actor(self.db, name="Other")
        with facade_context.acting_as(stranger.context):
            theirs = self.facade.create_concept("Ethics")["id"]
            claim = self.facade.create_argument(name="Claim", kind="argument")["id"]
        self.assertEqual(self.ok(self.get("/api/concepts")), [])
        self.assertEqual(self.get(f"/api/concepts/{theirs}").status, 404)
        self.assertEqual(self.get(f"/api/arguments/{claim}").status, 404)
        self.assertEqual(self.ok(self.get("/api/research-graph"))["nodes"], [])
        self.refused(self.send("PATCH", f"/api/concepts/{theirs}", {"name": "Mine"}), 404, "not_found")
        self.refused(self.send("DELETE", f"/api/arguments/{claim}"), 404, "not_found")
        # Names are per Library: the same name is free here.
        mine = self.ok(self.send("POST", "/api/concepts", {"name": "Ethics"}), 201)["id"]
        self.assertNotEqual(mine, theirs)
        with facade_context.acting_as(stranger.context):
            self.assertEqual(self.facade.get_concept(theirs)["name"], "Ethics")
            self.assertIsNotNone(self.facade.get_argument(claim))

    def test_a_stale_epoch_is_refused_before_the_route(self) -> None:
        res = self.call("POST", "/api/concepts", body={"name": "Ethics"}, cookies=self.cookies,
                        epoch=self.library_epoch() + 1)
        self.assertEqual(res.status, 409, res.raw)
        self.assertEqual(self.ok(self.get("/api/concepts")), [])
