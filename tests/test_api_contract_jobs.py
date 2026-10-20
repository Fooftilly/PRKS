"""Job-diagnostics wire contract (L-J-1 #627); the PostgreSQL behavior is in tests/pg/test_embedded_worker.py."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse
from pydantic import ValidationError

from backend.api_contract.boundary import dump_response
from backend.api_contract.jobs import JobDiagnostics
from backend.api_contract.openapi import job_diagnostics_openapi_document
from backend.log_safety import safe_route

_ROOT = Path(__file__).resolve().parents[1]
_ARTIFACT = _ROOT / "docs" / "api" / "openapi-job-diagnostics.json"

_COUNTERS = dict.fromkeys(
    ("claimed", "succeeded", "retried", "cancelled", "failed", "lost", "released", "reaped", "errors"), 0
)


def _body(**worker):
    return {
        "embedded_worker": {"state": "running", "reason": None, "counters": dict(_COUNTERS), **worker},
        "queue": [{"kind": "jobs.prune", "state": "queued", "count": 1, "oldest_age_seconds": 2.5}],
    }


class JobDiagnosticsContractTests(unittest.TestCase):
    def test_checked_in_openapi_artifact_matches_generator(self):
        self.assertTrue(_ARTIFACT.is_file(), "commit docs/api/openapi-job-diagnostics.json")
        self.assertEqual(json.loads(_ARTIFACT.read_text(encoding="utf-8")), job_diagnostics_openapi_document())

    def test_a_body_validates_against_the_document(self):
        api = OpenAPI.from_dict(job_diagnostics_openapi_document())
        request = MockRequest(host_url="http://127.0.0.1", method="get", path="/api/diagnostics/jobs")
        api.validate_request(request)
        for body in (_body(), {"embedded_worker": None, "queue": []}, _body(state="waiting", reason="pending")):
            with self.subTest(body=body):
                data = json.dumps(dump_response(JobDiagnostics, body)).encode()
                api.validate_response(
                    request, MockResponse(data=data, status_code=200, content_type="application/json")
                )

    def test_anything_beyond_counts_kinds_and_codes_is_refused(self):
        for bad in (
            {**_body(), "library_id": "L-1"},
            _body(state="busy"),
            _body(counters={**_COUNTERS, "payloads": 1}),
            {"embedded_worker": None, "queue": [{"kind": "k", "state": "succeeded", "count": 1, "oldest_age_seconds": 0}]},
            {"embedded_worker": None, "queue": [{"kind": "k", "state": "queued", "count": 1,
                                                 "oldest_age_seconds": 0, "payload": {}}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                JobDiagnostics.model_validate(bad)

    def test_routes_are_logged_as_themselves(self):
        for route in ("/api/diagnostics/jobs", "/api/openapi/job-diagnostics.json"):
            with self.subTest(route=route):
                self.assertEqual(safe_route(route), route)


if __name__ == "__main__":
    unittest.main()
