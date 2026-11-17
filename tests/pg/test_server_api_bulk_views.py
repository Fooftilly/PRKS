"""Bulk Work actions and Saved Views over HTTP on the PostgreSQL Library (PG-C2 #641).

A bulk status change counts each Work once, touches only the named Works
and never reindexes text; a bad status, an unknown action or a missing Work
changes nothing; a cross-origin or non-JSON request is refused; no title
reaches the logs. A Saved View is created, listed, read, renamed and
deleted, its name unique by identity key, its search definition complete,
every request and response checked against the Saved Views OpenAPI slice;
it stores the search, never its results, so the results stay live; a
refused request changes nothing and no saved query reaches the logs.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional
from urllib.parse import quote, urlsplit

from tests.pg.server_api_fixtures import ServerApiTestCase

SEARCH = {"mode": "all", "q": "", "tag": "", "author": "", "publisher": ""}
EVIL = "https://evil.example"


@contextmanager
def captured(*names: str) -> Iterator[list[str]]:
    records: list[str] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = Handler(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    loggers = [logging.getLogger(name) for name in names]
    previous = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        for logger, level in zip(loggers, previous):
            logger.removeHandler(handler)
            logger.setLevel(level)


class BulkTests(ServerApiTestCase):
    def status(self, work_id: str) -> str:
        return self.ok(self.get(f"/api/works/{work_id}"))["status"]

    def bulk(self, work_ids: list[str], action: str = "set_status", **fields: Any):
        return self.send("POST", "/api/works/bulk", {"work_ids": work_ids, "action": action, **fields})

    def test_a_status_change_counts_each_work_once_and_touches_only_those(self) -> None:
        a, b, c = (self.create_work(f"Bulk {name}", status="Paused")["id"] for name in "ABC")
        body = self.ok(self.bulk([a, a, b], status="Completed"))
        self.assertEqual((body["status"], body["action"], body["requested"], body["updated"]),
                         ("updated", "set_status", 2, 2))
        self.assertNotIn("works", body)
        self.assertEqual([self.status(w) for w in (a, b, c)], ["Completed", "Completed", "Paused"])

    def test_a_bad_status_an_unknown_action_or_a_missing_work_changes_nothing(self) -> None:
        work_id = self.create_work("Bulk Refused", status="Planned")["id"]
        self.refused(self.bulk([work_id], status="Finished"), 400)
        self.refused(self.bulk([work_id], action="delete_everything"), 400)
        body = self.refused(self.bulk([work_id, "W-MISSING"], status="Completed"), 404)
        self.assertEqual(body.get("error"), "One or more selected files no longer exist.")
        self.assertEqual(self.status(work_id), "Planned")

    def test_a_status_change_never_reindexes_text(self) -> None:
        work_id = self.create_work("Bulk No Index", status="Paused")["id"]
        jobs = self.sql("SELECT count(*) AS n FROM prks.jobs")[0]["n"]
        # The text is the database's: a status change queues no extraction.
        self.ok(self.bulk([work_id], status="Completed"))
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.jobs")[0]["n"], jobs)

    def test_a_cross_origin_or_non_json_request_changes_nothing(self) -> None:
        work_id = self.create_work("Bulk Trust", status="Planned")["id"]
        body = {"work_ids": [work_id], "action": "set_status", "status": "Completed"}
        res = self.send_as("POST", "/api/works/bulk", body, origin=EVIL)
        self.assertEqual((res.status, res.body.get("error")), (403, "origin_not_allowed"))
        self.assertEqual(self.send_as("POST", "/api/works/bulk", body, content_type="text/plain").status, 415)
        self.assertEqual(self.status(work_id), "Planned")

    def test_no_title_reaches_the_logs(self) -> None:
        sentinel = "PRIVATE_BULK_TITLE_X9Q7"
        work_id = self.create_work(sentinel, status="Paused")["id"]
        with captured("prks.server", "prks.db") as records:
            self.ok(self.bulk([work_id], status="Completed"))
            snapshot = self.server_module.performance_snapshot()
        self.assertNotIn(sentinel, "\n".join(records) + json.dumps(snapshot))
        self.assertNotIn(sentinel, self.logs.getvalue())
        self.assertTrue(any("bulk_work_update" in line for line in records))


class SavedViewTests(ServerApiTestCase):
    def views(self, method: str, path: str, payload: Optional[Any] = None, *,
              schema_valid: bool = True) -> tuple[int, Any]:
        """Send one Saved Views request and check it against the OpenAPI slice.

        ``schema_valid=False`` sends a body the schema refuses; only the
        response is checked then.
        """
        from openapi_core import OpenAPI
        from openapi_core.testing import MockRequest, MockResponse

        from backend.api_contract.openapi import saved_views_openapi_document

        res = self.get(path) if method == "GET" else self.send(method, path, payload)
        data = None if payload is None else json.dumps(payload).encode()
        api = OpenAPI.from_dict(saved_views_openapi_document())
        request = MockRequest(host_url="http://127.0.0.1", method=method.lower(), path=urlsplit(path).path,
                              data=data)
        if schema_valid:
            api.validate_request(request)
        api.validate_response(request, MockResponse(data=res.raw, status_code=res.status,
                                                    content_type="application/json"))
        return res.status, json.loads(res.raw.decode())

    def create(self, name: str, **search: str) -> dict[str, Any]:
        status, body = self.views("POST", "/api/saved-views", {"name": name, "search": {**SEARCH, **search}})
        self.assertEqual(status, 201, body)
        return body

    def test_a_view_is_created_listed_read_renamed_and_deleted(self) -> None:
        created = self.create("Adorno — culture industry", mode="advanced", q="culture industry", author="Adorno")
        self.assertTrue(created["id"].startswith("SV-"))
        self.assertEqual(created["search"]["author"], "Adorno")
        self.assertNotIn("search_q", created)
        view_id = created["id"]
        status, listed = self.views("GET", "/api/saved-views")
        self.assertEqual(status, 200)
        self.assertIn(view_id, [view["id"] for view in listed])
        self.assertEqual(self.views("GET", f"/api/saved-views/{view_id}")[1]["name"], "Adorno — culture industry")
        status, dup = self.views("POST", "/api/saved-views", {"name": "adorno — culture industry",
                                                              "search": created["search"]})
        self.assertEqual((status, dup["error"]), (409, "A Saved View with that name already exists."))
        status, patched = self.views("PATCH", f"/api/saved-views/{view_id}", {"name": "Critical Theory"})
        self.assertEqual((status, patched["id"], patched["name"]), (200, view_id, "Critical Theory"))
        self.assertEqual(self.views("GET", "/api/saved-views/SV-missing")[0], 404)
        self.assertEqual(self.views("DELETE", f"/api/saved-views/{view_id}")[0], 200)
        self.assertEqual(self.views("GET", f"/api/saved-views/{view_id}")[0], 404)

    def test_an_incomplete_search_is_refused_on_create_and_on_update(self) -> None:
        incomplete = (400, {"error": "Search definition is incomplete."})
        self.assertEqual(self.views("POST", "/api/saved-views", {"name": "Bad", "search": {
            "mode": "advanced", "author": "Adorno"}}, schema_valid=False), incomplete)
        created = self.create("Valid View", q="critical theory")
        self.assertEqual(self.views("PATCH", f"/api/saved-views/{created['id']}", {"search": {"author": "Adorno"}},
                                    schema_valid=False), incomplete)
        self.assertEqual(self.views("GET", f"/api/saved-views/{created['id']}")[1]["search"]["q"], "critical theory")

    def test_wrong_types_keep_the_domain_messages(self) -> None:
        search = {**SEARCH, "q": "typed"}
        cases = (
            ({"name": 7, "search": search}, "Name must be a string."),
            ({"name": "Typed", "search": "all"}, "Search definition is required."),
            ({"name": "Typed", "search": {**search, "q": 1}}, "Search fields must be strings."),
            ({"name": "Typed", "search": {**search, "mode": "regex"}}, "Invalid search mode."),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                self.assertEqual(self.views("POST", "/api/saved-views", payload, schema_valid=False),
                                 (400, {"error": message}))
        created = self.create("Typed", q="typed")
        self.assertEqual(self.views("PATCH", f"/api/saved-views/{created['id']}", {"name": 7}, schema_valid=False),
                         (400, {"error": "Name must be a string."}))
        self.assertEqual(self.views("PATCH", f"/api/saved-views/{created['id']}", {}),
                         (400, {"error": "Nothing to update."}))
        self.assertEqual(self.views("DELETE", "/api/saved-views/SV-missing"), (404, {"error": "Saved View not found."}))

    def test_the_request_schema_matches_the_live_endpoint(self) -> None:
        """Inputs the schema accepts, the server accepts, and the reverse."""
        from openapi_core import OpenAPI
        from openapi_core.testing import MockRequest

        from backend.api_contract.openapi import saved_views_openapi_document

        search = {**SEARCH, "q": "parity"}
        status, created = self.views("POST", "/api/saved-views", {"name": "Parity", "search": {**search, "extra": 1},
                                                                  "unused": True})
        self.assertEqual((status, created["search"]), (201, search))
        status, trimmed = self.views("POST", "/api/saved-views", {"name": "Trimmed", "search": {**search,
                                                                                                "mode": " all "}})
        self.assertEqual((status, trimmed["search"]["mode"]), (201, "all"))
        self.assertEqual(self.views("POST", "/api/saved-views", {"name": "Regex", "search": {**search,
                                                                                             "mode": "regex"}}),
                         (400, {"error": "Invalid search mode."}))
        view_id = created["id"]
        self.assertEqual(self.views("PATCH", f"/api/saved-views/{view_id}", {"name": None, "search": None}),
                         (400, {"error": "Nothing to update."}))
        status, renamed = self.views("PATCH", f"/api/saved-views/{view_id}", {"name": "Parity 2", "search": None})
        self.assertEqual((status, renamed["name"], renamed["search"]), (200, "Parity 2", search))
        short = {"name": "Short", "search": {"mode": "all", "q": "x"}}
        self.assertEqual(self.views("POST", "/api/saved-views", short, schema_valid=False),
                         (400, {"error": "Search definition is incomplete."}))
        api = OpenAPI.from_dict(saved_views_openapi_document())
        with self.assertRaises(Exception):
            api.validate_request(MockRequest(host_url="http://127.0.0.1", method="post", path="/api/saved-views",
                                             data=json.dumps(short).encode()))

    def test_an_author_view_stores_the_search_and_its_results_stay_live(self) -> None:
        a = self.create_work("Work A", author_text="Adorno")["id"]
        view = self.create("Adorno files", mode="advanced", author="Adorno")
        self.assertIn(a, self.search_ids("/api/search?author=Adorno"))
        stored = self.views("GET", f"/api/saved-views/{view['id']}")[1]
        self.assertEqual(stored["search"]["author"], "Adorno")
        self.assertNotIn("works", stored)
        b = self.create_work("Work B", author_text="Adorno")["id"]
        later = self.search_ids("/api/search?author=Adorno")
        self.assertIn(a, later)
        self.assertIn(b, later)
        self.assertEqual(self.views("GET", f"/api/saved-views/{view['id']}")[1]["search"], stored["search"])

    def test_a_tag_view_follows_a_bulk_tag_removal(self) -> None:
        tag_id = self.ok(self.send("POST", "/api/tags", {"name": "Frankfurt School"}))["id"]
        a, b = (self.create_work(f"Tagged {name}")["id"] for name in "AB")
        self.ok(self.send("POST", "/api/works/bulk", {"work_ids": [a, b], "action": "add_tags", "tag_ids": [tag_id]}))
        view = self.create("Frankfurt tag view", mode="tag", tag="Frankfurt School")
        path = f"/api/search?tag={quote('Frankfurt School')}"
        before = self.search_ids(path)
        self.assertIn(a, before)
        self.assertIn(b, before)
        self.ok(self.send("POST", "/api/works/bulk", {"work_ids": [a, b], "action": "remove_tags",
                                                      "tag_ids": [tag_id]}))
        after = self.search_ids(path)
        self.assertNotIn(a, after)
        self.assertNotIn(b, after)
        self.assertEqual(self.views("GET", f"/api/saved-views/{view['id']}")[1]["search"]["tag"], "Frankfurt School")

    def test_a_cross_origin_or_non_json_request_changes_nothing(self) -> None:
        payload = {"name": "Trust View", "search": {**SEARCH, "q": "trust"}}
        self.assertEqual(self.send_as("POST", "/api/saved-views", payload, origin=EVIL).status, 403)
        self.assertEqual(self.send_as("POST", "/api/saved-views", payload, content_type="text/plain").status, 415)
        self.assertNotIn("Trust View", [view["name"] for view in self.views("GET", "/api/saved-views")[1]])
        created = self.create("Trust Keep", q="keep")
        path = f"/api/saved-views/{created['id']}"
        self.assertEqual(self.send_as("PATCH", path, {"name": "Hijacked"}, origin=EVIL).status, 403)
        self.assertEqual(self.send_as("DELETE", path, origin=EVIL).status, 403)
        self.assertEqual(self.views("GET", path), (200, created))

    def test_no_saved_query_reaches_the_logs(self) -> None:
        sentinel = "PRIVATE_SAVED_QUERY_X9Q7"
        search = {**SEARCH, "q": sentinel}
        with captured("prks.server", "prks.api.saved_views", "prks.db", "prks.performance") as records:
            created = self.create(sentinel, q=sentinel)
            self.views("GET", f"/api/saved-views/{created['id']}")
            self.views("PATCH", f"/api/saved-views/{created['id']}", {"search": search})
            snapshot = self.server_module.performance_snapshot()
        self.assertNotIn(sentinel, "\n".join(records) + json.dumps(snapshot))
        self.assertNotIn(sentinel, self.logs.getvalue())
        self.assertTrue(any("saved_view_created" in line for line in records))
        self.assertTrue(any("saved_view_updated" in line for line in records))
        self.assertEqual(self.views("GET", f"/api/saved-views/{created['id']}")[1]["search"]["q"], sentinel)
