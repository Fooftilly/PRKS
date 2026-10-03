"""Saved Views HTTP request/response DTOs.

Shape and JSON types only. Name and search normalization (types, emptiness,
control characters, length, allowed modes, mode/field combinations), the
per-library limit, and name uniqueness stay in ``db_manager``. Saved Views are
online-only: there is no durable operation and no sync state.

The domain already refuses every wrong-type request field with its own
message (``Name must be a string.``, ``Search fields must be strings.``), so
the controller passes the JSON object straight to the domain. The request
models here are the published wire contract and the test oracle; they do not
replace those messages with a generic ``invalid_request``.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _strip_null_default(schema: dict[str, Any]) -> None:
    if schema.get("default") is None:
        schema.pop("default", None)


class SavedViewSearch(BaseModel):
    """A saved search definition. All five keys are always present."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["all", "advanced", "tag"]
    q: str
    tag: str
    author: str
    publisher: str


class SavedView(BaseModel):
    """One Saved View: GET list rows, GET by id, POST and PATCH bodies."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    search: SavedViewSearch
    # SQLite fills both on insert; the columns themselves are nullable.
    created_at: str | None
    updated_at: str | None


class SavedViewCreateRequest(BaseModel):
    """POST /api/saved-views."""

    model_config = ConfigDict(extra="ignore")

    name: str
    search: SavedViewSearch


class SavedViewUpdateRequest(BaseModel):
    """PATCH /api/saved-views/{id}. Omit a field to leave it unchanged.

    Sending neither field is a domain refusal (``Nothing to update.``). The
    published contract refuses JSON null; the controller still treats a null
    field as omitted, as it did before this contract existed.
    """

    model_config = ConfigDict(extra="ignore")

    name: Annotated[str, Field(default=None, json_schema_extra=_strip_null_default)]
    search: Annotated[
        SavedViewSearch, Field(default=None, json_schema_extra=_strip_null_default)
    ]


class SavedViewDeleted(BaseModel):
    """DELETE /api/saved-views/{id} body."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., pattern=r"^deleted$")
