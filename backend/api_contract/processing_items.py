"""Processing queue HTTP request/response DTOs (``docs/postgresql-architecture.md`` §11.5).

Shape and JSON types only. The queue is served by the PostgreSQL
(``PRKS_AUTH``) runtime alone; the rules live in
``backend/domain/processing``. An upload's body is the raw PDF, so it has no
model here: its metadata travels in headers (``backend/api/processing.py``).

The draft PATCH passes the JSON object to the domain, which keeps only the
draft fields it knows, stringifies and trims their values, and refuses an
unknown folder, person, tag, role type, or draft status. ``status`` is the
legacy spelling of ``status_draft``, as in the SQLite inbox.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api_contract.processing_files import (
    DraftValue,
    ProcessingDraftStatus,
    ProcessingFileRole,
    ProcessingFileRoleInput,
    ProcessingFileTag,
    ProcessingFileTagInput,
    SkippedEntry,
)

ProcessingItemStatus = Literal["ready", "imported", "deleted"]


class ProcessingItemDraft(BaseModel):
    """The Work a ``ready`` item will become, as the user drafts it."""

    model_config = ConfigDict(extra="forbid")

    title: str
    status_draft: ProcessingDraftStatus
    published_date: str
    abstract: str
    source_url: str
    author_text: str
    year: str
    publisher: str
    location: str
    edition: str
    journal: str
    volume: str
    issue: str
    pages: str
    isbn: str
    doi: str
    doc_type: str
    private_notes: str
    thumb_page: int | None
    # Empty when the import goes to "Uncategorized".
    target_folder_id: str
    roles: list[ProcessingFileRole]
    tags: list[ProcessingFileTag]


class ProcessingItem(BaseModel):
    """One queue item: a ``ready`` item with its draft, or an ``imported`` or ``deleted`` marker."""

    model_config = ConfigDict(extra="forbid")

    id: str
    original_filename: str
    sha256: str
    byte_size: int
    status: ProcessingItemStatus
    revision: int
    created_at: str
    updated_at: str
    imported_at: str | None
    # Null on an imported marker once its Work was deleted.
    imported_work_id: str | None
    deleted_at: str | None
    # Null on a marker.
    draft: ProcessingItemDraft | None


class ProcessingItemPage(BaseModel):
    """GET /api/processing-items: one page of ``ready`` items, oldest upload first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ProcessingItem]
    next_cursor: str | None = Field(description="Pass as `cursor` for the next page; null on the last.")


class DuplicateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    original_filename: str


class DuplicateWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str


class ProcessingDuplicates(BaseModel):
    """Other ``ready`` items and Works of this Library with the same bytes: a warning only."""

    model_config = ConfigDict(extra="forbid")

    items: list[DuplicateItem]
    works: list[DuplicateWork]


class ProcessingItemUploaded(BaseModel):
    """POST /api/processing-items: the item (201 created, 200 for a used Idempotency-Key)."""

    model_config = ConfigDict(extra="forbid")

    item: ProcessingItem
    created: bool
    duplicates: ProcessingDuplicates


class ProcessingItemUpdateRequest(BaseModel):
    """PATCH /api/processing-items/{id}. ``revision`` is required; omit a field to leave it unchanged.

    Draft values are stringified and trimmed. ``thumb_page`` keeps a whole
    page number of at least 1 and clears anything else. An empty
    ``target_folder_id`` clears it. ``roles`` and ``tags`` replace the
    drafted list; ``null`` leaves it unchanged.
    """

    model_config = ConfigDict(extra="ignore")

    revision: int = Field(ge=0, description="The item's revision the edit was made against.")
    title: DraftValue = None
    status_draft: DraftValue = Field(default=None, description="One of the draft statuses (domain-checked).")
    status: DraftValue = Field(default=None, description="Legacy alias of status_draft.")
    published_date: DraftValue = None
    abstract: DraftValue = None
    source_url: DraftValue = None
    author_text: DraftValue = None
    year: DraftValue = None
    publisher: DraftValue = None
    location: DraftValue = None
    edition: DraftValue = None
    journal: DraftValue = None
    volume: DraftValue = None
    issue: DraftValue = None
    pages: DraftValue = None
    isbn: DraftValue = None
    doi: DraftValue = None
    doc_type: DraftValue = None
    private_notes: DraftValue = None
    thumb_page: DraftValue = None
    target_folder_id: DraftValue = None
    roles: list[ProcessingFileRoleInput | SkippedEntry] | None = None
    tags: list[ProcessingFileTagInput | SkippedEntry] | None = None


class ProcessingItemImported(BaseModel):
    """POST /api/processing-items/{id}/import: the Work (201 created, 200 for an import already done)."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    work_id: str
    created: bool


class ProcessingImportRequest(BaseModel):
    """POST /api/processing-items/import: the items to import, each in its own transaction, in order."""

    model_config = ConfigDict(extra="forbid")

    item_ids: list[str] = Field(min_length=1, max_length=500)


class ProcessingImportResult(BaseModel):
    """One item's outcome: ``work_id`` when it imported, else ``code`` (and ``item`` when there is one)."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    ok: bool
    work_id: str | None
    created: bool
    code: str | None
    message: str | None
    item: ProcessingItem | None


class ProcessingImportResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ProcessingImportResult]


class ProcessingItemRefusal(BaseModel):
    """A refused item command: the code, and the item as it now is when there is one."""

    model_config = ConfigDict(extra="forbid")

    error: str
    code: str
    message: str
    item: ProcessingItem | None = None
