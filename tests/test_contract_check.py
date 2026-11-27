"""The live contract checker reads answers the way a client would (#1431)."""

from __future__ import annotations

import gzip
import json
import unittest
from typing import Any, cast

from backend.api import ROUTES
from backend.domain.works import metadata_rules as rules
from backend.web.http import Request, Response
from backend.web.routing import RouteTable
from tests.contract_check import ContractCheck

_JOBS = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/diagnostics/jobs")
_CREATE_POSITION = next(r for r in ROUTES if r.method == "POST" and r.pattern == "/api/positions")
_IMPORT_ONE = next(r for r in ROUTES if r.method == "POST" and r.pattern.endswith("/import")
                   and "{item_id}" in r.pattern)
_HEAD_PDF = next(r for r in ROUTES if r.method == "HEAD" and r.pattern == "/api/pdfs/{filename:path}")
_OPENAPI = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/openapi.json")
_PDF = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/processing-items/{item_id}/pdf")
_STORAGE = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/storage-status")
_METADATA_STATE = next(r for r in ROUTES if r.method == "GET"
                       and r.pattern == "/api/works/{work_id}/metadata-state")
_PORTRAIT = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/persons/{person_id}/profile-image")
_PROGRESS = next(r for r in ROUTES if r.method == "POST" and r.pattern == "/api/backups/progress")
_FOLDERS = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/folders")
_STORAGE_BODY = {"field": "value"}  # not read: the header checks run before the schema


_PDF_HEADERS = [("Accept-Ranges", "bytes"), ("Cache-Control", "no-store, max-age=0, must-revalidate"),
                ("Content-Length", "10")]


def _streamed(status: int, headers: list[tuple[str, str]]) -> Response:
    answer = Response(status, _PDF_HEADERS + headers, b"")
    answer.stream = cast(Any, object())  # never read by the checker
    return answer


def _request() -> Request:
    return Request(method="GET", path="/", query_string="", headers=cast(Any, None), app=cast(Any, None))


def _json(status: int, payload: Any, *, gzipped: bool = False) -> Response:
    body = json.dumps(payload).encode()
    headers = [("Content-Type", "application/json")]
    if gzipped:
        body = gzip.compress(body)
        headers.append(("Content-Encoding", "gzip"))
    return Response(status, headers, body)


class ContractCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.check = ContractCheck()

    def test_a_gzipped_answer_is_validated_after_decoding(self) -> None:
        self.check.check(_OPENAPI, _request(), _json(200, {"openapi": "3.1.0"}, gzipped=True))
        self.assertEqual(self.check.violations, [])
        self.check.check(_OPENAPI, _request(), _json(200, [1, 2], gzipped=True))
        self.assertEqual(len(self.check.violations), 1, self.check.violations)
        self.assertIn("answer", self.check.violations[0])

    def test_an_empty_body_where_the_contract_documents_one_is_a_violation(self) -> None:
        self.check.check(_OPENAPI, _request(), Response(200, [("Content-Type", "application/json")], b""))
        self.assertEqual(len(self.check.violations), 1)
        self.assertIn("no body", self.check.violations[0])

    def test_an_unreadable_answer_is_recorded_not_raised(self) -> None:
        broken = Response(200, [("Content-Type", "application/json"), ("Content-Encoding", "gzip")], b"not gzip")
        self.check.check(_OPENAPI, _request(), broken)
        self.assertEqual(len(self.check.violations), 1)
        self.assertIn("could not be checked", self.check.violations[0])

    def test_the_jobs_404_is_the_plain_text_refusal(self) -> None:
        answer = Response(404, [("Content-Type", "text/plain; charset=utf-8")], b"404 API endpoint not found\n")
        self.check.check(_JOBS, _request(), answer)
        self.assertEqual(self.check.violations, [])

    def test_a_success_without_a_required_body_is_a_violation(self) -> None:
        self.check.check(_CREATE_POSITION, _request(), _json(201, {}))
        self.assertTrue(any("requires one" in v for v in self.check.violations), self.check.violations)

    def test_a_success_without_an_optional_body_is_not(self) -> None:
        self.check.check(_IMPORT_ONE, _request(), _json(201, {}))
        self.assertFalse(any("request" in v for v in self.check.violations), self.check.violations)

    def test_an_empty_answer_still_names_a_documented_media_type(self) -> None:
        self.check.check(_JOBS, _request(), Response(404, [("Content-Type", "text/plain")], b""))
        self.assertEqual(self.check.violations, [])
        self.check.check(_JOBS, _request(), Response(404, [], b""))
        self.assertEqual(len(self.check.violations), 1, self.check.violations)

    def test_an_answer_without_a_required_header_is_a_violation(self) -> None:
        self.check.check(_HEAD_PDF, _request(), Response(416, [], b""))
        self.assertEqual(self.check.violations, ["HEAD /api/pdfs/{filename:path} -> 416: required header "
                                                 "Content-Range missing"])
        self.check.violations.clear()
        self.check.check(_HEAD_PDF, _request(), Response(416, [("Content-Range", "bytes */10")], b""))
        self.assertEqual(self.check.violations, [])

    def test_a_streamed_range_needs_its_content_range_and_media_type(self) -> None:
        self.check.check(_PDF, _request(), _streamed(206, [("Content-Type", "application/pdf"),
                                                           ("Content-Range", "bytes 0-9/100")]))
        self.assertEqual(self.check.violations, [])
        self.check.check(_PDF, _request(), _streamed(206, [("Content-Type", "application/pdf")]))
        self.assertEqual(self.check.violations, ["GET /api/processing-items/{item_id}/pdf -> 206: "
                                                 "required header Content-Range missing"])

    def test_a_streamed_answer_of_another_media_type_is_a_violation(self) -> None:
        self.check.check(_PDF, _request(), _streamed(200, [("Content-Type", "text/plain")]))
        self.assertEqual(len(self.check.violations), 1, self.check.violations)
        self.assertIn("streamed text/plain not documented", self.check.violations[0])

    def test_an_integer_header_is_read_as_its_decimal(self) -> None:
        self.check._check_headers("x", {"headers": {"Content-Length": {"schema": {"type": "integer", "minimum": 0}}}},
                                  {"Content-Length": "10"}.get)
        self.assertEqual(self.check.violations, [])
        for text in ("-1", "ten"):
            self.check._check_headers("x", {"headers": {"Content-Length": {"schema": {"type": "integer",
                                                                                      "minimum": 0}}}},
                                      {"Content-Length": text}.get)
        self.assertEqual(len(self.check.violations), 2, self.check.violations)

    def test_a_stream_on_a_status_without_a_body_is_a_violation(self) -> None:
        self.check.check(_PDF, _request(), _streamed(416, [("Content-Range", "bytes */10")]))
        self.assertEqual(len(self.check.violations), 1, self.check.violations)
        self.assertIn("streamed answer without Content-Type not documented", self.check.violations[0])

    def test_a_declared_header_holds_its_schema(self) -> None:
        def status(cache: list[tuple[str, str]]) -> Response:
            answer = _json(200, _STORAGE_BODY)
            answer.headers.extend(cache)
            return answer

        self.check.check(_STORAGE, _request(), status([("Cache-Control", "no-store")]))
        self.assertEqual([v for v in self.check.violations if "Cache-Control" in v], [])
        self.check.check(_STORAGE, _request(), status([]))
        self.check.check(_STORAGE, _request(), status([("Cache-Control", "max-age=60")]))
        found = [v for v in self.check.violations if "Cache-Control" in v]
        self.assertEqual(len(found), 2, self.check.violations)
        self.assertIn("required header Cache-Control missing", found[0])
        self.assertIn("header Cache-Control", found[1])

    def test_a_malformed_integer_header_is_a_violation(self) -> None:
        integer = {"headers": {"Content-Length": {"schema": {"type": "integer"}}}}
        self.check._check_headers("x", integer, {"Content-Length": "--1"}.get)
        self.assertEqual(len(self.check.violations), 1, self.check.violations)

    def test_a_backup_stream_must_be_sent_no_store(self) -> None:
        def progress(cache: list[tuple[str, str]]) -> Response:
            answer = Response(200, [("Content-Type", "application/x-ndjson"), *cache], b"")
            answer.stream = cast(Any, object())
            return answer

        self.check.check(_PROGRESS, _request(), progress([("Cache-Control", "no-store")]))
        self.assertEqual(self.check.violations, [])
        self.check.check(_PROGRESS, _request(), progress([]))
        self.check.check(_PROGRESS, _request(), progress([("Cache-Control", "public, max-age=3600")]))
        self.assertEqual(len(self.check.violations), 2, self.check.violations)

    def test_the_restored_epoch_is_checked_on_the_answer_sent(self) -> None:
        self.check.wrap(RouteTable(ROUTES))
        restored = _json(200, {"status": "restored", "epoch": 7, "objects_held": 0, "warnings": []})
        restore = next(r for r in ROUTES if r.method == "POST" and r.pattern == "/api/backups/restore")
        self.check.check(restore, _request(), restored)
        self.assertEqual([v for v in self.check.violations if "header" in v], [],
                         "the route's own answer has no epoch yet")
        self.check.violations.clear()
        self.check.check_sent("POST", "/api/backups/restore", 200, [("PRKS-Library-Epoch", "7")])
        self.assertEqual(self.check.violations, [])
        self.check.check_sent("POST", "/api/backups/restore", 200, [])
        self.check.check_sent("POST", "/api/backups/restore", 200, [("PRKS-Library-Epoch", "07")])
        self.assertEqual(len(self.check.violations), 2, self.check.violations)

    def test_a_conditional_read_needs_its_etag(self) -> None:
        self.check.check(_FOLDERS, _request(), Response(304, [("ETag", '"v1"')], b""))
        self.assertEqual(self.check.violations, [])
        for status in (200, 304):
            answer = _json(200, []) if status == 200 else Response(304, [], b"")
            self.check.check(_FOLDERS, _request(), answer)
        found = [v for v in self.check.violations if "required header ETag missing" in v]
        self.assertEqual(len(found), 2, self.check.violations)

    def test_a_pdf_must_be_sent_no_store(self) -> None:
        cacheable = [("Accept-Ranges", "bytes"), ("Cache-Control", "public, max-age=3600"),
                     ("Content-Length", "10"), ("Content-Type", "application/pdf")]
        answer = Response(200, cacheable, b"")
        answer.stream = cast(Any, object())
        self.check.check(_PDF, _request(), answer)
        self.assertEqual(len(self.check.violations), 1, self.check.violations)
        self.assertIn("header Cache-Control", self.check.violations[0])

    def test_a_revalidated_read_must_send_its_etag(self) -> None:
        self.check.check(_METADATA_STATE, _request(), Response(304, [], b""))
        self.assertEqual(self.check.violations, [f"GET {_METADATA_STATE.pattern} -> 304: "
                                                 "required header ETag missing"])

    def test_a_metadata_state_value_is_its_wire_text(self) -> None:
        def state(value: Any) -> Response:
            fields: dict[str, Any] = {field: {"revision": 0} if field in rules.BYTE_LIMITED_FIELDS
                                      else {"revision": 0, "value": ""} for field in rules.SYNCED_FIELDS}
            fields["thumb_page"]["value"] = value
            answer = _json(200, {"work_id": "W1", "fields": fields})
            answer.headers.append(("ETag", '"e"'))
            return answer

        for value in ("", "3"):
            self.check.check(_METADATA_STATE, _request(), state(value))
        self.assertEqual(self.check.violations, [])
        for value in (3, None):
            self.check.check(_METADATA_STATE, _request(), state(value))
        self.assertEqual(len(self.check.violations), 2, self.check.violations)
        self.assertIn("fields/thumb_page/value", self.check.violations[0])

    def test_a_portrait_must_be_cached_privately(self) -> None:
        def portrait(cache: str) -> Response:
            answer = Response(200, [("Content-Type", "image/webp"), ("Cache-Control", cache)], b"")
            answer.stream = cast(Any, object())
            return answer

        for cache in ("private, max-age=86400", "private, max-age=3600"):
            self.check.check(_PORTRAIT, _request(), portrait(cache))
        self.assertEqual(self.check.violations, [])
        self.check.check(_PORTRAIT, _request(), portrait("public, max-age=86400"))
        self.assertEqual(len(self.check.violations), 1, self.check.violations)
        self.assertIn("header Cache-Control", self.check.violations[0])


if __name__ == "__main__":
    unittest.main()
