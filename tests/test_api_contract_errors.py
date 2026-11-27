"""Admission and route refusals answer bodies the documented error envelope allows.

``backend.api.session`` answers every refusal as ``{"error": code, "code":
code, "message": text}``. The OpenAPI families document their 4xx bodies as
``ApiErrorEnvelope``, which forbids fields it does not name, so the envelope
must name ``message`` or every documented refusal breaks its own contract
(the Schemathesis scan's ``response_schema_conformance`` check reports it).
"""
from __future__ import annotations

import json
import unittest
from collections.abc import Callable
from typing import Any

from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse

from backend.api.session import Reply, _refused, route_failure
from backend.api_contract.errors import ApiErrorEnvelope
from backend.api_contract.openapi import (
    openapi_document,
    openapi_document,
    openapi_document,
)
from backend.db.errors import DatabaseError, DatabaseUnavailableError
from backend.domain.identity.authorization import AccessRefused, Refusal


class RefusalBodiesMatchTheEnvelope(unittest.TestCase):
    def test_every_admission_refusal_is_an_envelope(self) -> None:
        for refusal in Refusal:
            with self.subTest(refusal=refusal.name):
                body = _refused(refusal).body
                envelope = ApiErrorEnvelope.model_validate(body)
                self.assertEqual(envelope.as_dict(), body)
                self.assertEqual((envelope.error, envelope.code, envelope.message),
                                 (refusal.code, refusal.code, refusal.message))

    def test_a_route_failure_is_an_envelope(self) -> None:
        for exc in (AccessRefused(Refusal.LIBRARY_EPOCH_CHANGED),
                    DatabaseError("POSITION_NAME_TAKEN", "That name is taken.", http_status=409),
                    DatabaseUnavailableError()):
            with self.subTest(exc=type(exc).__name__):
                reply = route_failure(exc)
                assert reply is not None
                self.assertEqual(ApiErrorEnvelope.model_validate(reply.body).as_dict(), reply.body)

    def test_an_unnamed_field_is_still_refused(self) -> None:
        with self.assertRaises(ValueError):
            ApiErrorEnvelope.model_validate({"error": "forbidden", "request_id": "r-1"})

    def test_documented_refusal_statuses_validate_against_each_family(self) -> None:
        epoch_required = _refused(Refusal.LIBRARY_EPOCH_REQUIRED)
        epoch_changed = route_failure(AccessRefused(Refusal.LIBRARY_EPOCH_CHANGED))
        taken = route_failure(DatabaseError("SAVED_VIEW_NAME_TAKEN", "That name is taken.", http_status=409))
        assert epoch_changed is not None and taken is not None
        cases: tuple[tuple[Callable[[], dict[str, Any]], str, str, Reply], ...] = (
            (openapi_document, "post", "/api/positions", epoch_required),
            (openapi_document, "post", "/api/publishers", epoch_required),
            (openapi_document, "post", "/api/saved-views", epoch_required),
            (openapi_document, "post", "/api/saved-views", epoch_changed),
            (openapi_document, "post", "/api/saved-views", taken),
        )
        for builder, method, path, reply in cases:
            with self.subTest(path=path, code=reply.body["code"]):
                api = OpenAPI.from_dict(builder())
                request = MockRequest(host_url="http://127.0.0.1", method=method, path=path)
                response = MockResponse(data=json.dumps(reply.body).encode(), status_code=reply.status,
                                        content_type="application/json")
                api.validate_response(request, response)

if __name__ == "__main__":
    unittest.main()
