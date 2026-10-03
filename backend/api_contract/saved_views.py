"""Saved Views HTTP request/response DTOs.

Shape and JSON types only. Name and search normalization (types, emptiness,
control characters, length, allowed modes, mode/field combinations), the
per-library limit, and name uniqueness stay in ``db_manager``. Saved Views are
online-only: there is no durable operation and no sync state.

The domain already refuses every wrong-type request field with its own
message (``Name must be a string.``, ``Search fields must be strings.``), so
the controller passes the JSON object straight to the domain. The request
models here describe exactly what the endpoint accepts (extra keys ignored,
``mode`` trimmed by the domain, PATCH null treated as omitted); parity tests in
``tests/test_server_api.py`` keep them in step with the live controller.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class SavedViewSearchInput(BaseModel):
    """A search definition as the endpoint accepts it.

    All five keys are required strings. Extra keys are ignored. ``mode`` is
    trimmed by the domain before it must be ``all``, ``advanced``, or ``tag``,
    so the schema types it as a string; the allowed values, field lengths,
    and mode/field combinations are domain refusals (400).
    """

    model_config = ConfigDict(extra="ignore")

    mode: str = Field(
        ...,
        description="`all`, `advanced`, or `tag` after trimming (domain-checked).",
    )
    q: str
    tag: str
    author: str
    publisher: str


class SavedViewCreateRequest(BaseModel):
    """POST /api/saved-views."""

    model_config = ConfigDict(extra="ignore")

    name: str
    search: SavedViewSearchInput


class SavedViewUpdateRequest(BaseModel):
    """PATCH /api/saved-views/{id}. Omit a field, or send JSON null, to leave it unchanged.

    Sending neither field (or only nulls) is a domain refusal
    (``Nothing to update.``).
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    search: SavedViewSearchInput | None = None


class SavedViewDeleted(BaseModel):
    """DELETE /api/saved-views/{id} body."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., pattern=r"^deleted$")
