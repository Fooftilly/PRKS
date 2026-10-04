"""Build a machine-readable OpenAPI 3.1 document for the Positions API family.

Generated from the Pydantic boundary models so schema and implementation share
one source of truth for wire shape. This is a vertical slice toward #45 — not
the full PRKS surface.
"""
from __future__ import annotations

from typing import Any

from backend.api_contract.errors import ApiErrorEnvelope
from backend.api_contract.performance import (
    PerformanceCounters,
    PerformanceDiagnosticsReset,
    PerformanceDiagnosticsResetRequest,
    PerformanceRequestTotals,
    PerformanceRouteStat,
    PerformanceSnapshot,
    PerformanceSpanStat,
)
from backend.api_contract.positions import (
    PositionCreateRequest,
    PositionDeleted,
    PositionDetail,
    PositionSummary,
    PositionSyncFields,
    PositionSyncState,
    PositionUpdateRequest,
)
from backend.api_contract.processing_files import (
    ProcessingFile,
    ProcessingFileImported,
    ProcessingFileRole,
    ProcessingFileRoleInput,
    ProcessingFileTag,
    ProcessingFileTagInput,
    ProcessingFileUpdateRequest,
)
from backend.api_contract.publishers import (
    PublisherAliasAdded,
    PublisherAliasRequest,
    PublisherCreated,
    PublisherCreateRequest,
    PublisherDeleted,
    PublisherInUse,
)
from backend.api_contract.saved_views import (
    SavedView,
    SavedViewCreateRequest,
    SavedViewDeleted,
    SavedViewSearch,
    SavedViewSearchInput,
    SavedViewUpdateRequest,
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
        PositionSyncFields,
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


PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION = "0.1.0"


def performance_diagnostics_openapi_document() -> dict[str, Any]:
    """OpenAPI 3.1 for the performance-diagnostics HTTP family (#45 / #232).

    Same Pydantic-generated contract style as the Positions slice. Not a
    second API model and not the full PRKS surface.
    """
    schemas: dict[str, Any] = {}
    for model in (
        ApiErrorEnvelope,
        PerformanceRequestTotals,
        PerformanceRouteStat,
        PerformanceSpanStat,
        PerformanceCounters,
        PerformanceSnapshot,
        PerformanceDiagnosticsReset,
        PerformanceDiagnosticsResetRequest,
    ):
        raw = _schema(model)
        _merge_defs(schemas, raw)

    error_ref = {"$ref": "#/components/schemas/ApiErrorEnvelope"}
    snapshot_ref = {"$ref": "#/components/schemas/PerformanceSnapshot"}
    reset_ref = {"$ref": "#/components/schemas/PerformanceDiagnosticsReset"}

    def _json_content(schema_ref: dict[str, Any]) -> dict[str, Any]:
        return {"application/json": {"schema": schema_ref}}

    def _json_error(description: str) -> dict[str, Any]:
        return {
            "description": description,
            "content": _json_content(error_ref),
        }

    # Same body-read refusals as Positions POST/PATCH: PRKSHandler._read_json_body.
    mutation_body_read_errors = {
        "400": _json_error(
            "JSON body gate refusal, or a body that is not an empty object."
        ),
        "413": _json_error(
            "Request body larger than the JSON body limit (request_too_large)."
        ),
        "415": _json_error(
            "Missing or unsupported Content-Type (unsupported_media_type)."
        ),
    }

    paths: dict[str, Any] = {
        "/api/diagnostics/performance": {
            "get": {
                "operationId": "getPerformanceDiagnostics",
                "summary": "Runtime performance diagnostics snapshot",
                "tags": ["diagnostics"],
                "responses": {
                    "200": {
                        "description": "Aggregate in-process measurements.",
                        "content": _json_content(snapshot_ref),
                    },
                },
            },
        },
        "/api/diagnostics/performance/reset": {
            "post": {
                "operationId": "resetPerformanceDiagnostics",
                "summary": "Clear the in-process performance window",
                "tags": ["diagnostics"],
                "requestBody": {
                    "required": True,
                    "description": "Empty JSON object. Additional properties are rejected.",
                    "content": _json_content(
                        {"$ref": "#/components/schemas/PerformanceDiagnosticsResetRequest"}
                    ),
                },
                "responses": {
                    "200": {
                        "description": "Measurements were reset.",
                        "content": _json_content(reset_ref),
                    },
                    **mutation_body_read_errors,
                },
            },
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PRKS API — Performance diagnostics slice",
            "version": PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION,
            "description": (
                "Vertical-slice OpenAPI for performance diagnostics "
                "(#45 / #232). Schemas are generated from the same Pydantic "
                "boundary as the rest of backend/api_contract. The snapshot "
                "is disposable process metadata, not canonical library data."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": schemas,
        },
    }


PUBLISHERS_CONTRACT_VERSION = "0.1.0"


def publishers_openapi_document() -> dict[str, Any]:
    """OpenAPI 3.1 for the online-only Publishers HTTP family (#45 / #232).

    Same Pydantic-generated contract style as the Positions slice. Publishers
    have no durable operation, revision, or sync state.
    """
    schemas: dict[str, Any] = {}
    for model in (
        ApiErrorEnvelope,
        PublisherInUse,
        PublisherCreateRequest,
        PublisherCreated,
        PublisherAliasRequest,
        PublisherAliasAdded,
        PublisherDeleted,
    ):
        raw = _schema(model)
        _merge_defs(schemas, raw)

    schemas["PublisherInUseList"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/PublisherInUse"},
    }

    error_ref = {"$ref": "#/components/schemas/ApiErrorEnvelope"}
    deleted_ref = {"$ref": "#/components/schemas/PublisherDeleted"}

    def _json_content(schema_ref: dict[str, Any]) -> dict[str, Any]:
        return {"application/json": {"schema": schema_ref}}

    def _json_error(description: str) -> dict[str, Any]:
        return {
            "description": description,
            "content": _json_content(error_ref),
        }

    # Shared body-read refusals from PRKSHandler._read_json_body (POST).
    mutation_body_read_errors = {
        "413": _json_error(
            "Request body larger than the JSON body limit (request_too_large)."
        ),
        "415": _json_error(
            "Missing or unsupported Content-Type (unsupported_media_type)."
        ),
    }

    publisher_id_parameter = {
        "name": "publisher_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }

    paths: dict[str, Any] = {
        "/api/publishers": {
            "get": {
                "operationId": "listPublishersInUse",
                "summary": "List canonical Publishers with aliases and Work counts",
                "tags": ["publishers"],
                "parameters": [
                    {
                        "name": "used",
                        "in": "query",
                        "required": False,
                        "description": (
                            "`1`, `true`, or `yes` returns the catalog. Any "
                            "other value returns an empty list."
                        ),
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Publishers ordered by name, case-insensitively.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/PublisherInUseList"}
                        ),
                    },
                },
            },
            "post": {
                "operationId": "createPublisher",
                "summary": "Create a canonical Publisher, or return the existing one",
                "tags": ["publishers"],
                "requestBody": {
                    "required": True,
                    "content": _json_content(
                        {"$ref": "#/components/schemas/PublisherCreateRequest"}
                    ),
                },
                "responses": {
                    "200": {
                        "description": "Created or existing Publisher.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/PublisherCreated"}
                        ),
                    },
                    "400": _json_error("Schema or domain refusal (empty name)."),
                    **mutation_body_read_errors,
                },
            },
        },
        "/api/publishers/{publisher_id}": {
            "parameters": [publisher_id_parameter],
            "delete": {
                "operationId": "deletePublisher",
                "summary": "Delete a Publisher group and its aliases",
                "description": "Works keep their publisher text. Deleting a missing id is not an error.",
                "tags": ["publishers"],
                "responses": {
                    "200": {
                        "description": "Deleted.",
                        "content": _json_content(deleted_ref),
                    },
                },
            },
        },
        "/api/publishers/{publisher_id}/aliases": {
            "parameters": [publisher_id_parameter],
            "post": {
                "operationId": "addPublisherAlias",
                "summary": "Add an alternate spelling to a Publisher",
                "tags": ["publishers"],
                "requestBody": {
                    "required": True,
                    "content": _json_content(
                        {"$ref": "#/components/schemas/PublisherAliasRequest"}
                    ),
                },
                "responses": {
                    "200": {
                        "description": "Added, or already an alias of this Publisher.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/PublisherAliasAdded"}
                        ),
                    },
                    "400": _json_error(
                        "Schema or domain refusal (empty, unknown Publisher, "
                        "canonical name, or used elsewhere)."
                    ),
                    **mutation_body_read_errors,
                },
            },
            "delete": {
                "operationId": "removePublisherAlias",
                "summary": "Remove an alternate spelling from a Publisher",
                "tags": ["publishers"],
                "parameters": [
                    {
                        "name": "alias",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Removed.",
                        "content": _json_content(deleted_ref),
                    },
                    "400": _json_error("Missing alias."),
                    "404": _json_error("This Publisher has no such alias."),
                },
            },
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PRKS API — Publishers slice",
            "version": PUBLISHERS_CONTRACT_VERSION,
            "description": (
                "Vertical-slice OpenAPI for the online-only Publishers family "
                "(#45 / #232). Schemas are generated from the same Pydantic "
                "boundary as the rest of backend/api_contract."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": schemas,
        },
    }


SAVED_VIEWS_CONTRACT_VERSION = "0.1.0"


def saved_views_openapi_document() -> dict[str, Any]:
    """OpenAPI 3.1 for the online-only Saved Views HTTP family (#45 / #232).

    Same Pydantic-generated contract style as Publishers. Saved Views have no
    durable operation, revision, or sync state. Results are never stored.
    """
    schemas: dict[str, Any] = {}
    for model in (
        ApiErrorEnvelope,
        SavedViewSearch,
        SavedViewSearchInput,
        SavedView,
        SavedViewCreateRequest,
        SavedViewUpdateRequest,
        SavedViewDeleted,
    ):
        raw = _schema(model)
        _merge_defs(schemas, raw)

    schemas["SavedViewList"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/SavedView"},
    }

    error_ref = {"$ref": "#/components/schemas/ApiErrorEnvelope"}
    view_ref = {"$ref": "#/components/schemas/SavedView"}

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

    view_id_parameter = {
        "name": "view_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }

    paths: dict[str, Any] = {
        "/api/saved-views": {
            "get": {
                "operationId": "listSavedViews",
                "summary": "List Saved Views",
                "tags": ["saved-views"],
                "responses": {
                    "200": {
                        "description": "Saved Views ordered by name, case-insensitively.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/SavedViewList"}
                        ),
                    },
                },
            },
            "post": {
                "operationId": "createSavedView",
                "summary": "Save a named search definition",
                "tags": ["saved-views"],
                "requestBody": {
                    "required": True,
                    "content": _json_content(
                        {"$ref": "#/components/schemas/SavedViewCreateRequest"}
                    ),
                },
                "responses": {
                    "201": {
                        "description": "Created.",
                        "content": _json_content(view_ref),
                    },
                    "400": _json_error(
                        "Domain refusal: wrong type, empty or too long name, "
                        "or an incomplete or invalid search definition."
                    ),
                    "409": _json_error(
                        "Name already used, or the library has the maximum "
                        "number of Saved Views."
                    ),
                    **mutation_body_read_errors,
                },
            },
        },
        "/api/saved-views/{view_id}": {
            "parameters": [view_id_parameter],
            "get": {
                "operationId": "getSavedView",
                "summary": "Read one Saved View",
                "tags": ["saved-views"],
                "responses": {
                    "200": {
                        "description": "The Saved View.",
                        "content": _json_content(view_ref),
                    },
                    "404": _json_error("No such Saved View."),
                },
            },
            "patch": {
                "operationId": "updateSavedView",
                "summary": "Rename a Saved View or change its search",
                "tags": ["saved-views"],
                "requestBody": {
                    "required": True,
                    "content": _json_content(
                        {"$ref": "#/components/schemas/SavedViewUpdateRequest"}
                    ),
                },
                "responses": {
                    "200": {
                        "description": "Updated.",
                        "content": _json_content(view_ref),
                    },
                    "400": _json_error(
                        "Nothing to update, or a domain refusal of the name "
                        "or search definition."
                    ),
                    "404": _json_error("No such Saved View."),
                    "409": _json_error("Name already used by another Saved View."),
                    **mutation_body_read_errors,
                },
            },
            "delete": {
                "operationId": "deleteSavedView",
                "summary": "Delete a Saved View",
                "description": "Deleting a view never deletes Works or files.",
                "tags": ["saved-views"],
                "responses": {
                    "200": {
                        "description": "Deleted.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/SavedViewDeleted"}
                        ),
                    },
                    "404": _json_error("No such Saved View."),
                },
            },
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PRKS API — Saved Views slice",
            "version": SAVED_VIEWS_CONTRACT_VERSION,
            "description": (
                "Vertical-slice OpenAPI for the online-only Saved Views family "
                "(#45 / #232). Schemas are generated from the same Pydantic "
                "boundary as the rest of backend/api_contract."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": schemas,
        },
    }


PROCESSING_FILES_CONTRACT_VERSION = "0.1.0"


def processing_files_openapi_document() -> dict[str, Any]:
    """OpenAPI 3.1 for the online-only Files for Processing family (#45 / #232).

    Same Pydantic-generated contract style as Saved Views. The inbox has no
    durable operation, revision, or sync state. The PDF preview route serves
    bytes to an iframe and is not part of this JSON slice.
    """
    schemas: dict[str, Any] = {}
    for model in (
        ApiErrorEnvelope,
        ProcessingFileRole,
        ProcessingFileTag,
        ProcessingFile,
        ProcessingFileImported,
        ProcessingFileRoleInput,
        ProcessingFileTagInput,
        ProcessingFileUpdateRequest,
    ):
        raw = _schema(model)
        _merge_defs(schemas, raw)

    schemas["ProcessingFileList"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/ProcessingFile"},
    }

    error_ref = {"$ref": "#/components/schemas/ApiErrorEnvelope"}

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

    file_id_parameter = {
        "name": "processing_file_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }

    paths: dict[str, Any] = {
        "/api/processing-files": {
            "get": {
                "operationId": "listProcessingFiles",
                "summary": "List inbox files that are not imported yet",
                "tags": ["processing-files"],
                "parameters": [
                    {
                        "name": "rescan",
                        "in": "query",
                        "required": False,
                        "description": (
                            "`1`, `true`, or `yes` (any case) reconciles the "
                            "inbox folder on disk before listing."
                        ),
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": (
                            "Pending, missing, and failed files, in that "
                            "order, then by path case-insensitively."
                        ),
                        "content": _json_content(
                            {"$ref": "#/components/schemas/ProcessingFileList"}
                        ),
                    },
                },
            },
        },
        "/api/processing-files/{processing_file_id}": {
            "parameters": [file_id_parameter],
            "patch": {
                "operationId": "updateProcessingFile",
                "summary": "Save an inbox file's staged Work metadata",
                "tags": ["processing-files"],
                "requestBody": {
                    # Required so a client always sends a JSON body, and with
                    # it the JSON Content-Type the boundary needs (415 without).
                    "required": True,
                    "description": "A JSON object. `{}` changes nothing and returns the file.",
                    "content": _json_content(
                        {"$ref": "#/components/schemas/ProcessingFileUpdateRequest"}
                    ),
                },
                "responses": {
                    "200": {
                        "description": "The updated inbox file.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/ProcessingFile"}
                        ),
                    },
                    "400": _json_error(
                        "Malformed JSON, no such file, a body that is not an "
                        "object, roles or tags that are not arrays, or a domain "
                        "refusal of the draft status, folder, person, tag, or "
                        "role type."
                    ),
                    **mutation_body_read_errors,
                },
            },
        },
        "/api/processing-files/{processing_file_id}/import": {
            "parameters": [file_id_parameter],
            "post": {
                "operationId": "importProcessingFile",
                "summary": "Import an inbox file as a new Work",
                "description": (
                    "Uses the staged metadata. Importing a file that is "
                    "already imported returns its Work again."
                ),
                "tags": ["processing-files"],
                "requestBody": {
                    # Required for the same reason as the PATCH body.
                    "required": True,
                    "description": "Any JSON value, ignored. Send `{}`.",
                    "content": _json_content({}),
                },
                "responses": {
                    "200": {
                        "description": "Imported.",
                        "content": _json_content(
                            {"$ref": "#/components/schemas/ProcessingFileImported"}
                        ),
                    },
                    "400": _json_error(
                        "Malformed JSON, no such file, an unknown target folder, "
                        "a file that is gone or not a PDF, or a failed import."
                    ),
                    **mutation_body_read_errors,
                },
            },
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PRKS API — Files for Processing slice",
            "version": PROCESSING_FILES_CONTRACT_VERSION,
            "description": (
                "Vertical-slice OpenAPI for the online-only Files for Processing "
                "family (#45 / #232). Schemas are generated from the same "
                "Pydantic boundary as the rest of backend/api_contract."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": schemas,
        },
    }
