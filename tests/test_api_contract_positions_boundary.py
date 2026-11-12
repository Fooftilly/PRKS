"""The Positions API contract below the HTTP layer: typed requests, responses and OpenAPI.

Driver-free: the request parser, the response models, the error envelope
and the generated OpenAPI slice (and its checked-in copy). The live HTTP
contract is ``tests/pg/test_http_positions_contract.py``. Moved from
``tests/test_api_contract_positions.py`` (PG-C2 #641).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse
from pydantic import ValidationError

from backend.api_contract.boundary import dump_response
from backend.api_contract.errors import ApiErrorEnvelope, validation_error_envelope
from backend.api_contract.openapi import positions_openapi_document
from backend.api_contract.positions import (
    PositionArgumentSummary,
    PositionCreateRequest,
    PositionDetail,
    PositionSummary,
    PositionSyncFields,
    PositionSyncState,
    PositionUpdateRequest,
    parse_position_request,
)

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _openapi_api() -> OpenAPI:
    """openapi-core is a required test dep for this module (CI installs it)."""
    return OpenAPI.from_dict(positions_openapi_document())


def _validate_http_against_openapi(
    *,
    method: str,
    path: str,
    status: int,
    response_body: bytes,
    request_body: bytes | None = None,
    path_pattern: str | None = None,
    view_args: dict | None = None,
    check_request: bool = True,
    request_headers: dict | None = None,
    response_headers: dict | None = None,
) -> None:
    """Fail the test if openapi-core rejects the live request/response pair.

    Set ``check_request=False`` for deliberately schema-invalid bodies (wrong
    JSON types) where only the error *response* is under contract — openapi-core
    correctly refuses those requests against the request schema.
    """
    api = _openapi_api()
    req = MockRequest(
        host_url="http://127.0.0.1",
        method=method.lower(),
        path=path,
        path_pattern=path_pattern,
        view_args=view_args,
        headers=request_headers,
        data=request_body,
    )
    # Bodyless 304 must not claim application/json content.
    content_type = "" if status == 304 else "application/json"
    resp = MockResponse(
        data=response_body,
        status_code=status,
        content_type=content_type,
        headers=response_headers,
    )
    if check_request:
        api.validate_request(req)
    api.validate_response(req, resp)


class PositionBoundaryUnitTests(unittest.TestCase):
    def test_create_request_rejects_non_object(self):
        model, err = parse_position_request(PositionCreateRequest, ["x"])
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_request")

    def test_create_request_wrong_type_name_preserves_domain_code(self):
        model, err = parse_position_request(PositionCreateRequest, {"name": 12})
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_name")
        self.assertEqual(err["error"], "Name must be a string.")

    def test_create_request_wrong_type_description_preserves_domain_code(self):
        model, err = parse_position_request(
            PositionCreateRequest, {"name": "ok", "description": 5}
        )
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_text")
        self.assertEqual(err["error"], "Text must be a string.")

    def test_create_request_falsy_non_string_description_is_invalid_text(self):
        """Stricter typed contract: no pre-slice falsy coercion on POST."""
        for falsy in (False, 0, []):
            with self.subTest(description=falsy):
                model, err = parse_position_request(
                    PositionCreateRequest,
                    {"name": "Falsy desc", "description": falsy},
                )
                self.assertIsNone(model)
                self.assertEqual(err["code"], "invalid_text")
                self.assertEqual(err["error"], "Text must be a string.")

    def test_create_request_null_description_is_invalid_text(self):
        """POST description must be a string when supplied — null is not empty."""
        model, err = parse_position_request(
            PositionCreateRequest, {"name": "Null desc", "description": None}
        )
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_text")
        self.assertEqual(err["error"], "Text must be a string.")

    def test_create_request_omitted_description_defaults_to_empty_string(self):
        model, err = parse_position_request(
            PositionCreateRequest, {"name": "No desc"}
        )
        self.assertIsNone(err)
        self.assertEqual(model.description, "")

    def test_create_request_passes_plain_values(self):
        model, err = parse_position_request(
            PositionCreateRequest, {"name": "Realism", "description": "note"}
        )
        self.assertIsNone(err)
        self.assertIsInstance(model.name, str)
        self.assertEqual(model.name, "Realism")
        # Domain receives plain attrs, not the model as business state.
        self.assertEqual(type(model.name), str)

    def test_update_request_tracks_which_fields_were_sent(self):
        model, err = parse_position_request(PositionUpdateRequest, {"name": "Only name"})
        self.assertIsNone(err)
        self.assertEqual(model.domain_field_kwargs(), {"name": "Only name"})

    def test_update_explicit_null_description_is_invalid_text(self):
        """Intentional PATCH: explicit null is invalid; empty string clears."""
        model, err = parse_position_request(
            PositionUpdateRequest, {"description": None}
        )
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_text")
        self.assertEqual(err["error"], "Text must be a string.")

    def test_update_explicit_null_name_is_invalid_name(self):
        model, err = parse_position_request(PositionUpdateRequest, {"name": None})
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_name")
        self.assertEqual(err["error"], "Name must be a string.")

    def test_update_omitted_description_not_in_kwargs(self):
        model, err = parse_position_request(
            PositionUpdateRequest, {"name": "Keep desc"}
        )
        self.assertIsNone(err)
        self.assertNotIn("description", model.domain_field_kwargs())

    def test_update_empty_description_clears(self):
        model, err = parse_position_request(
            PositionUpdateRequest, {"description": ""}
        )
        self.assertIsNone(err)
        self.assertEqual(model.domain_field_kwargs(), {"description": ""})

    def test_update_name_with_null_description_is_invalid_text(self):
        model, err = parse_position_request(
            PositionUpdateRequest, {"name": "Renamed", "description": None}
        )
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_text")
        self.assertEqual(err["error"], "Text must be a string.")

    def test_update_request_wrong_type_name_preserves_domain_code(self):
        model, err = parse_position_request(PositionUpdateRequest, {"name": 1})
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_name")
        self.assertEqual(err["error"], "Name must be a string.")

    def test_response_dump_round_trips_detail_shape(self):
        payload = {
            "id": "P-TEST",
            "name": "A",
            "description": "",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "arguments": [],
        }
        out = dump_response(PositionDetail, payload)
        self.assertEqual(out["id"], "P-TEST")
        self.assertEqual(out["arguments"], [])
        self.assertIn("description", out)
        self.assertIn("created_at", out)
        self.assertIn("updated_at", out)

    def test_response_detail_requires_established_keys(self):
        """Missing established response fields must fail — not silently default."""
        base = {
            "id": "P-TEST",
            "name": "A",
            "description": "",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "arguments": [],
        }
        for key in (
            "description",
            "created_at",
            "updated_at",
            "arguments",
        ):
            incomplete = {k: v for k, v in base.items() if k != key}
            with self.assertRaises(ValidationError):
                PositionDetail.model_validate(incomplete)

    def test_response_summary_requires_established_keys(self):
        base = {
            "id": "P-TEST",
            "name": "A",
            "description": None,
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }
        for key in ("description", "created_at", "updated_at"):
            incomplete = {k: v for k, v in base.items() if k != key}
            with self.assertRaises(ValidationError):
                PositionSummary.model_validate(incomplete)

    def test_response_models_forbid_unexpected_fields(self):
        detail = {
            "id": "P-TEST",
            "name": "A",
            "description": "",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "arguments": [],
            "extra_field": "nope",
        }
        with self.assertRaises(ValidationError):
            PositionDetail.model_validate(detail)
        summary = {
            "id": "P-TEST",
            "name": "A",
            "description": "",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "sneaky": True,
        }
        with self.assertRaises(ValidationError):
            PositionSummary.model_validate(summary)
        with self.assertRaises(ValidationError):
            PositionArgumentSummary.model_validate(
                {
                    "id": "A-1",
                    "name": "n",
                    "kind": "argument",
                    "verdict_id": "V-1",
                    "verdict_label": "Supports",
                    "noise": 1,
                }
            )
        with self.assertRaises(ValidationError):
            PositionSyncState.model_validate(
                {
                    "position_id": "P-TEST",
                    "fields": {
                        "name": {"revision": 0},
                        "description": {"revision": 0},
                    },
                    "values": {"name": "x"},
                }
            )

    def test_sync_state_fields_require_name_and_description_only(self):
        """Sync-state fields is a fixed map matching position_sync.FIELDS."""
        ok = {
            "position_id": "P-TEST",
            "fields": {
                "name": {"revision": 0},
                "description": {"revision": 1},
            },
        }
        PositionSyncState.model_validate(ok)
        PositionSyncFields.model_validate(ok["fields"])

        missing_description = {
            "position_id": "P-TEST",
            "fields": {"name": {"revision": 0}},
        }
        with self.assertRaises(ValidationError):
            PositionSyncState.model_validate(missing_description)

        missing_name = {
            "position_id": "P-TEST",
            "fields": {"description": {"revision": 0}},
        }
        with self.assertRaises(ValidationError):
            PositionSyncState.model_validate(missing_name)

        unexpected_field = {
            "position_id": "P-TEST",
            "fields": {
                "name": {"revision": 0},
                "description": {"revision": 0},
                "unexpected": {"revision": 0},
            },
        }
        with self.assertRaises(ValidationError):
            PositionSyncState.model_validate(unexpected_field)

        only_unexpected = {
            "position_id": "P-TEST",
            "fields": {"unexpected": {"revision": 0}},
        }
        with self.assertRaises(ValidationError):
            PositionSyncState.model_validate(only_unexpected)

    def test_openapi_response_schemas_require_established_keys(self):
        schemas = positions_openapi_document()["components"]["schemas"]
        summary_required = set(schemas["PositionSummary"]["required"])
        self.assertTrue(
            {"id", "name", "description", "created_at", "updated_at"}
            <= summary_required
        )
        detail_required = set(schemas["PositionDetail"]["required"])
        self.assertTrue(
            {
                "id",
                "name",
                "description",
                "created_at",
                "updated_at",
                "arguments",
            }
            <= detail_required
        )
        self.assertEqual(
            schemas["PositionSummary"].get("additionalProperties"), False
        )
        self.assertEqual(
            schemas["PositionDetail"].get("additionalProperties"), False
        )
        self.assertEqual(
            schemas["PositionArgumentSummary"].get("additionalProperties"),
            False,
        )
        self.assertEqual(
            schemas["PositionSyncState"].get("additionalProperties"), False
        )
        sync_fields = schemas["PositionSyncFields"]
        self.assertEqual(
            set(sync_fields["required"]), {"name", "description"}
        )
        self.assertEqual(sync_fields.get("additionalProperties"), False)
        self.assertIn("name", sync_fields["properties"])
        self.assertIn("description", sync_fields["properties"])
        # Request null is not part of the published create/update schemas.
        create_desc = schemas["PositionCreateRequest"]["properties"]["description"]
        self.assertEqual(create_desc.get("type"), "string")
        self.assertNotIn("anyOf", create_desc)
        update_name = schemas["PositionUpdateRequest"]["properties"]["name"]
        self.assertEqual(update_name.get("type"), "string")
        self.assertNotIn("anyOf", update_name)
        update_desc = schemas["PositionUpdateRequest"]["properties"]["description"]
        self.assertEqual(update_desc.get("type"), "string")
        self.assertNotIn("anyOf", update_desc)

    def test_error_envelope_omits_null_code(self):
        body = ApiErrorEnvelope(error="Position not found.").as_dict()
        self.assertEqual(body, {"error": "Position not found."})

    def test_validation_error_envelope_has_stable_code(self):
        body = validation_error_envelope(ValueError("x"))
        self.assertEqual(body["code"], "invalid_request")

    def test_openapi_document_covers_positions_family(self):
        doc = positions_openapi_document()
        self.assertEqual(doc["openapi"], "3.1.0")
        self.assertIn("/api/positions", doc["paths"])
        self.assertIn("/api/positions/{position_id}", doc["paths"])
        self.assertIn(
            "/api/positions/{position_id}/sync-state", doc["paths"]
        )
        schemas = doc["components"]["schemas"]
        for name in (
            "PositionCreateRequest",
            "PositionUpdateRequest",
            "PositionDetail",
            "PositionSummary",
            "ApiErrorEnvelope",
            "PositionDeleted",
            "PositionSyncFields",
            "PositionSyncState",
        ):
            self.assertIn(name, schemas)
        delete_responses = doc["paths"]["/api/positions/{position_id}"]["delete"][
            "responses"
        ]
        self.assertIn("409", delete_responses)
        self.assertNotIn("400", delete_responses)
        self.assertIn("position_in_use", delete_responses["409"]["description"])
        sync_responses = doc["paths"]["/api/positions/{position_id}/sync-state"]["get"][
            "responses"
        ]
        self.assertIn("304", sync_responses)
        sync_get = doc["paths"]["/api/positions/{position_id}/sync-state"]["get"]
        sync_params = {p["name"]: p for p in sync_get.get("parameters", [])}
        self.assertIn("If-None-Match", sync_params)
        self.assertEqual(sync_params["If-None-Match"]["in"], "header")
        self.assertIn("ETag", sync_responses["200"].get("headers", {}))
        self.assertIn("ETag", sync_responses["304"].get("headers", {}))
        post_responses = doc["paths"]["/api/positions"]["post"]["responses"]
        patch_responses = doc["paths"]["/api/positions/{position_id}"]["patch"][
            "responses"
        ]
        for responses in (post_responses, patch_responses):
            self.assertIn("413", responses)
            self.assertIn("415", responses)

    def test_checked_in_openapi_artifact_matches_generator(self):
        artifact = Path(_PROJECT_DIR) / "docs" / "api" / "openapi-positions.json"
        self.assertTrue(artifact.is_file(), "commit docs/api/openapi-positions.json")
        on_disk = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, positions_openapi_document())

    def test_openapi_core_accepts_positions_document(self):
        api = _openapi_api()
        self.assertIsNotNone(api)


if __name__ == "__main__":
    unittest.main()
