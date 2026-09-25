"""Build a machine-readable OpenAPI 3.1 document for the Positions API family.

Generated from the Pydantic boundary models so schema and implementation share
one source of truth for wire shape. This is a vertical slice toward #45 — not
the full PRKS surface.
"""
from __future__ import annotations

from typing import Any

from backend.api_contract.errors import ApiErrorEnvelope
from backend.api_contract.positions import (
    PositionCreateRequest,
    PositionDeleted,
    PositionDetail,
    PositionSummary,
    PositionSyncState,
    PositionUpdateRequest,
)


def _schema(model) -> dict[str, Any]:
    return model.model_json_schema(ref_template="#/components/schemas/{model}")


def _merge_defs(components: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Fold ``$defs`` from a model schema into components.schemas."""
    defs = schema.pop("$defs", None) or {}
    for name, body in defs.items():
        components.setdefault(name, body)
    # Top-level model name
    title = schema.get("title")
    if title:
        components[title] = {k: v for k, v in schema.items() if k != "title"}
    return schema


def positions_openapi_document() -> dict[str, Any]:
    """Return an OpenAPI 3.1 object covering Positions HTTP routes."""
    schemas: dict[str, Any] = {}
    for model in (
        ApiErrorEnvelope,
        PositionCreateRequest,
        PositionUpdateRequest,
        PositionSummary,
        PositionDetail,
        PositionDeleted,
        PositionSyncState,
    ):
        raw = _schema(model)
        _merge_defs(schemas, raw)

    # List responses use arrays of PositionSummary
    schemas["PositionSummaryList"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/PositionSummary"},
    }

    error_ref = {"$ref": "#/components/schemas/ApiErrorEnvelope"}
    detail_ref = {"$ref": "#/components/schemas/PositionDetail"}
    summary_list_ref = {"$ref": "#/components/schemas/PositionSummaryList"}

    def _json_content(schema_ref: dict[str, Any]) -> dict[str, Any]:
        return {"application/json": {"schema": schema_ref}}

    def _json_error(description: str) -> dict[str, Any]:
        return {
            "description": description,
            "content": _json_content(error_ref),
        }

    # Shared body-read refusals from PRKSHandler._read_json_body (POST/PATCH).
    mutation_body_read_errors = {
        "413": _json_error(
            "Request body larger than the JSON body limit (request_too_large)."
        ),
        "415": _json_error(
            "Missing or unsupported Content-Type (unsupported_media_type)."
        ),
    }

    paths: dict[str, Any] = {
        "/api/positions": {
            "get": {
                "operationId": "listPositions",
                "summary": "List Positions",
                "tags": ["positions"],
                "responses": {
                    "200": {
                        "description": "Position index (no embedded arguments).",
                        "content": _json_content(summary_list_ref),
                    },
                },
            },
            "post": {
                "operationId": "createPosition",
                "summary": "Create a Position",
                "tags": ["positions"],
                "requestBody": {
                    "required": True,
                    "content": _json_content(
                        {"$ref": "#/components/schemas/PositionCreateRequest"}
                    ),
                },
                "responses": {
                    "201": {
                        "description": "Created Position detail.",
                        "content": _json_content(detail_ref),
                    },
                    "400": _json_error("Validation or domain refusal."),
                    **mutation_body_read_errors,
                },
            },
        },
        "/api/positions/{position_id}": {
            "parameters": [
                {
                    "name": "position_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "get": {
                "operationId": "getPosition",
                "summary": "Get Position detail",
                "tags": ["positions"],
                "responses": {
                    "200": {
                        "description": "Position detail with targeting Arguments.",
                        "content": _json_content(detail_ref),
                    },
                    "404": _json_error("Not found."),
                },
            },
            "patch": {
                "operationId": "updatePosition",
                "summary": "Update Position fields",
                "tags": ["positions"],
                "requestBody": {
                    "required": True,
                    "content": _json_content(
                        {"$ref": "#/components/schemas/PositionUpdateRequest"}
                    ),
                },
                "responses": {
                    "200": {
                        "description": "Updated Position detail.",
                        "content": _json_content(detail_ref),
                    },
                    "400": _json_error("Validation or domain refusal."),
                    "404": _json_error("Not found."),
                    **mutation_body_read_errors,
                },
            },
            "delete": {
                "operationId": "deletePosition",
                "summary": "Delete a Position",
                "tags": ["positions"],
                "responses": {
                    "200": {
                        "description": "Deleted.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/PositionDeleted"}
                        ),
                    },
                    "404": _json_error("Not found."),
                    "409": _json_error(
                        "Position is still targeted by an Argument or Stance "
                        "(position_in_use)."
                    ),
                },
            },
        },
        "/api/positions/{position_id}/sync-state": {
            "parameters": [
                {
                    "name": "position_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "get": {
                "operationId": "getPositionSyncState",
                "summary": "Position field revisions (sync-state)",
                "tags": ["positions"],
                "parameters": [
                    {
                        "name": "If-None-Match",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": (
                            "Conditional read: when equal to the current ETag, "
                            "responds 304 Not Modified (bodyless)."
                        ),
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Revisions only.",
                        "headers": {
                            "ETag": {
                                "description": (
                                    "Opaque revision token for conditional GETs."
                                ),
                                "schema": {"type": "string"},
                            }
                        },
                        "content": _json_content(
                            {"$ref": "#/components/schemas/PositionSyncState"}
                        ),
                    },
                    "304": {
                        "description": (
                            "Not modified: If-None-Match matched the current "
                            "ETag. Bodyless."
                        ),
                        "headers": {
                            "ETag": {
                                "description": (
                                    "Current revision token (same as a matching "
                                    "200)."
                                ),
                                "schema": {"type": "string"},
                            }
                        },
                    },
                    "404": _json_error("Not found."),
                },
            },
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PRKS API — Positions slice",
            "version": "0.1.0",
            "description": (
                "Vertical-slice OpenAPI for the Positions HTTP family "
                "(#180 / #45). Not the full PRKS surface. Request/response "
                "schemas are generated from Pydantic boundary models; "
                "canonical mutations remain in research_network/position_sync."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": schemas,
        },
    }
