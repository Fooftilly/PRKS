"""The live contract checker reads answers the way a client would (#1431)."""

from __future__ import annotations

import gzip
import json
import unittest
from typing import Any, cast

from backend.api import ROUTES
from backend.web.http import Request, Response
from tests.contract_check import ContractCheck

_JOBS = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/diagnostics/jobs")
_OPENAPI = next(r for r in ROUTES if r.method == "GET" and r.pattern == "/api/openapi.json")


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


if __name__ == "__main__":
    unittest.main()
