"""Files for Processing HTTP request/response DTOs.

Shape and JSON types only. Inbox discovery, draft normalization (status,
document type, thumbnail page, target folder, roles, tags) and import stay in
``db_manager``. The inbox is online-only: there is no durable operation and no
sync state.

The PATCH controller passes the JSON object straight to the domain, which
keeps only the draft fields it knows, stringifies and trims their values, and
refuses an unknown folder, person, tag, role type, or draft status with its
own message. The request models here describe that endpoint: extra keys are
ignored, JSON ``null`` for ``roles`` or ``tags`` leaves them unchanged, and
``status`` is the legacy spelling of ``status_draft``. Parity tests in
``tests/test_server_api.py`` keep them in step with the live controller.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProcessingFileStatus = Literal["pending", "missing", "imported", "error"]
ProcessingDraftStatus = Literal["Planned", "In Progress", "Completed", "Paused", "Not Started"]

# The domain stringifies and trims every non-null draft value it keeps,
# arrays and objects included.
DraftValue = str | int | float | bool | None | list[Any] | dict[str, Any]

# A role or tag entry that is not an object is skipped by the domain.
SkippedEntry = str | int | float | bool | None | list[Any]


class ProcessingFileRole(BaseModel):
    """A staged person role, in inbox order."""

    model_config = ConfigDict(extra="forbid")

    person_id: str
    person_name: str
    role_type: str
    order_index: int


class ProcessingFileTag(BaseModel):
    """A staged tag."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    # Tag columns are nullable with defaults.
    color: str | None
    created_at: str | None


class ProcessingFile(BaseModel):
    """One inbox file: GET list rows and the PATCH body."""

    model_config = ConfigDict(extra="forbid")

    id: str
    rel_path: str
    filename: str
    folder: str
    status: ProcessingFileStatus
    last_error: str | None
    imported_work_id: str | None
    imported_at: str | None
    discovered_at: str | None
    updated_at: str | None
    exists: bool
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
    # Empty when the import goes to the default folder.
    target_folder_id: str
    roles: list[ProcessingFileRole]
    tags: list[ProcessingFileTag]


class ProcessingFileImported(BaseModel):
    """POST /api/processing-files/{id}/import body."""

    model_config = ConfigDict(extra="forbid")

    processing_file_id: str
    work_id: str


class ProcessingFileRoleInput(BaseModel):
    """A staged role as the endpoint accepts it.

    An item without a person or role type is skipped. A duplicate
    person/role pair keeps its first position.
    """

    model_config = ConfigDict(extra="ignore")

    person_id: DraftValue = None
    role_type: DraftValue = Field(
        default=None,
        description="A People role type (domain-checked).",
    )


class ProcessingFileTagInput(BaseModel):
    """A staged tag as the endpoint accepts it. ``tag_id`` is read when ``id`` is empty."""

    model_config = ConfigDict(extra="ignore")

    id: DraftValue = None
    tag_id: DraftValue = None


class ProcessingFileUpdateRequest(BaseModel):
    """PATCH /api/processing-files/{id}. Omit a field to leave it unchanged.

    Draft values are stringified and trimmed (arrays and objects too; send
    strings). ``thumb_page`` keeps a whole
    page number of at least 1 and clears anything else. An empty
    ``target_folder_id`` clears it. ``roles`` and ``tags`` replace the staged
    list; ``null`` leaves it unchanged.
    """

    model_config = ConfigDict(extra="ignore")

    title: DraftValue = None
    status_draft: DraftValue = Field(
        default=None,
        description=(
            "`Planned`, `In Progress`, `Completed`, `Paused`, or `Not Started` "
            "after trimming (domain-checked; null is refused)."
        ),
    )
    status: DraftValue = Field(
        default=None,
        description="Legacy spelling of `status_draft`, read only when that is absent.",
    )
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
    doc_type: DraftValue = Field(
        default=None,
        description="Normalized by the domain; an unknown type becomes `misc`.",
    )
    private_notes: DraftValue = None
    thumb_page: DraftValue = None
    target_folder_id: DraftValue = Field(
        default=None,
        description="An existing folder id, or empty to clear (domain-checked).",
    )
    roles: list[ProcessingFileRoleInput | SkippedEntry] | None = Field(
        default=None,
        description="Entries that are not objects are skipped.",
    )
    tags: list[ProcessingFileTagInput | SkippedEntry] | None = Field(
        default=None,
        description="Entries that are not objects are skipped.",
    )
