"""Works wire shapes: ``/api/works``, ``/api/recent``, ``/api/recently-added``, ``/api/search`` (#1431).

Shape and JSON types only. The Work rows are the projections of
``backend/domain/works/projection.py``; statuses, document types, lengths and
the bulk limits stay in ``backend/domain/works``, which refuses them with a
``400`` and its own message.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.api_contract.boundary import omittable

_FORBID = ConfigDict(extra="forbid")
# A request names the fields it changes; the routes ignore keys they do not know.
_IGNORE = ConfigDict(extra="ignore")


class LinkedPerson(BaseModel):
    """One credit of the Work's primary Manifestation, in role order."""

    model_config = _FORBID

    person_id: str
    role_type: str
    order_index: int
    canonical_name: str = Field(description="The Person's first and last name.")
    credit_name: str = Field(description="The name as credited on this Work; empty when none.")
    display_name: str = Field(description="The credit name, else the canonical name.")


class _WorkCard(BaseModel):
    """The fields every Work row carries: the browse columns and the people columns."""

    model_config = _FORBID

    id: str
    title: str
    status: str
    doc_type: Optional[str]
    file_path: str = Field(description="``/api/pdfs/<name>`` for a managed PDF, else empty.")
    source_kind: Optional[Literal["pdf", "video"]]
    source_url: Optional[str]
    thumb_url: Optional[str]
    thumb_page: Optional[int]
    author_text: Optional[str]
    year: Optional[str]
    published_date: Optional[str]
    provider: Optional[str]
    provider_id: Optional[str]
    primary_manifestation_id: str
    primary_asset_id: Optional[str]
    manifestation_count: int
    asset_count: int
    primary_author: Optional[str]
    primary_editor: Optional[str]
    linked_authors: Optional[str] = Field(description="The Authors' display names, comma separated.")
    linked_people: list[LinkedPerson]
    file_size_bytes: Optional[int] = Field(description="The managed PDF's size on disk; null without one.")


class WorkBrowseRow(_WorkCard):
    """``GET /api/works?projection=browse``: the compact card row."""

    abstract_excerpt: str = Field(description="The abstract's first 100 code points.")


class RecentWork(_WorkCard):
    """``GET /api/recent``: a card row with its last open."""

    last_opened_at: Optional[str]


class RecentlyAddedWork(_WorkCard):
    """``GET /api/recently-added``: a card row with its creation, publisher and Folder."""

    created_at: str
    publisher: Optional[str]
    folder_id: Optional[str]


class WorkSummary(_WorkCard):
    """``GET /api/works`` and ``GET /api/search``: the whole summary row."""

    abstract: Optional[str]
    source_mime: Optional[str]
    urldate: Optional[str]
    publisher: Optional[str]
    location: Optional[str]
    edition: Optional[str]
    journal: Optional[str]
    volume: Optional[str]
    issue: Optional[str]
    pages: Optional[str]
    isbn: Optional[str]
    doi: Optional[str]
    last_opened_at: Optional[str]
    created_at: str
    updated_at: str
    folder_id: Optional[str] = Field(
        default=None, description="Left out of a Tag filter's rows (``GET /api/search?tag=``).")


WorkCatalog = Union[list[WorkSummary], list[WorkBrowseRow]]


class WorkTag(BaseModel):
    model_config = _FORBID

    id: str
    name: str
    color: Optional[str]
    created_at: str


class WorkRole(BaseModel):
    """A credit on the Work's primary Manifestation: the Person's row and the role."""

    model_config = _FORBID

    id: str
    first_name: Optional[str]
    last_name: Optional[str]
    aliases: Optional[str]
    about: Optional[str]
    birth_date: Optional[str]
    death_date: Optional[str]
    image_url: Optional[str]
    link_wikipedia: Optional[str]
    link_stanford_encyclopedia: Optional[str]
    link_iep: Optional[str]
    links_other: Optional[str]
    created_at: str
    updated_at: str
    role_type: str
    order_index: int
    credit_name: Optional[str]
    manifestation_id: Optional[str]


class ConceptRef(BaseModel):
    model_config = _FORBID

    id: str
    name: str
    aliases: list[str]


class ArgumentRef(BaseModel):
    model_config = _FORBID

    id: str
    name: str


class ResearchRefs(BaseModel):
    """The Concepts and Arguments the Research Note links to."""

    model_config = _FORBID

    concepts: list[ConceptRef]
    arguments: list[ArgumentRef]
    pending: bool = omittable("True while the research index has not caught up with the note; "
                              "left out when the index could not be read.")


class WorkDetail(BaseModel):
    """``GET /api/works/:id``: the Work with its notes, credits, Tags and annotations."""

    model_config = _FORBID

    id: str
    title: str
    status: str
    abstract: Optional[str]
    text_content: str = Field(description="The Research Note (Markdown); empty when none.")
    html_content: str = omittable("The Research Note with its wiki links resolved; left out when empty.")
    private_notes: Optional[str]
    file_path: str
    file_size_bytes: Optional[int]
    source_kind: Optional[Literal["pdf", "video"]]
    source_url: Optional[str]
    source_mime: Optional[str]
    thumb_url: Optional[str]
    thumb_page: Optional[int]
    provider: Optional[str]
    provider_id: Optional[str]
    urldate: Optional[str]
    author_text: Optional[str]
    doc_type: Optional[str]
    year: Optional[str]
    published_date: Optional[str]
    publisher: Optional[str]
    location: Optional[str]
    edition: Optional[str]
    journal: Optional[str]
    volume: Optional[str]
    issue: Optional[str]
    pages: Optional[str]
    isbn: Optional[str]
    doi: Optional[str]
    hide_pdf_link_annotations: Literal[0, 1]
    citation_style: str = Field(description="The Work's citation style id; empty for the Library's.")
    canonical_annotation_set_revision: int
    materialized_pdf_annotation_revision: int
    last_opened_at: Optional[str]
    created_at: str
    updated_at: str
    primary_manifestation_id: str
    primary_asset_id: Optional[str]
    manifestation_count: int
    asset_count: int
    folder_id: Optional[str]
    folder_title: Optional[str]
    playlist_id: Optional[str]
    playlist_title: Optional[str]
    roles: list[WorkRole]
    tags: list[WorkTag]
    annotations: str = Field(description="The Work's annotations as a JSON array, in text.")
    arguments: list[Any] = Field(description="Always empty: the linked Arguments are in research_refs.")
    research_refs: ResearchRefs


class MetadataFieldRevision(BaseModel):
    """A field measured in bytes: its revision alone, never its value."""

    model_config = _FORBID

    revision: int


class MetadataFieldValue(BaseModel):
    """A small field: its revision and its current value."""

    model_config = _FORBID

    revision: int
    value: Optional[Union[str, int]]


class WorkMetadataFields(BaseModel):
    """Every synchronized field (``metadata_rules.SYNCED_FIELDS``), each one present."""

    model_config = _FORBID

    abstract: MetadataFieldRevision
    author_text: MetadataFieldRevision
    citation_style: MetadataFieldValue
    doc_type: MetadataFieldValue
    doi: MetadataFieldValue
    edition: MetadataFieldValue
    isbn: MetadataFieldValue
    issue: MetadataFieldValue
    journal: MetadataFieldValue
    location: MetadataFieldValue
    pages: MetadataFieldValue
    published_date: MetadataFieldValue
    publisher: MetadataFieldValue
    source_url: MetadataFieldRevision
    status: MetadataFieldValue
    thumb_page: MetadataFieldValue
    title: MetadataFieldRevision
    volume: MetadataFieldValue
    year: MetadataFieldValue


class WorkMetadataState(BaseModel):
    """``GET /api/works/:id/metadata-state``: each synchronized field's revision."""

    model_config = _FORBID

    work_id: str
    fields: WorkMetadataFields


class WorkSourceState(BaseModel):
    """``GET /api/works/:id/source-state``: the source identity's revision only."""

    model_config = _FORBID

    work_id: str
    revision: int


class WorkNotesState(BaseModel):
    """``GET /api/works/:id/notes-state``: the two notes' revisions (the bodies are on the Work)."""

    model_config = _FORBID

    work_id: str
    research_note_revision: int
    private_note_revision: int


class InitialRole(BaseModel):
    """A credit the new Work is born with; one without a known role type is skipped."""

    model_config = _IGNORE

    person_id: str
    role_type: str = omittable()
    credit_name: Optional[str] = omittable()


class WorkCreateRequest(BaseModel):
    """``POST /api/works``: an upload, an existing managed PDF, a video URL or no file.

    Every field may be left out; an empty title or author of a YouTube video is
    filled from its oEmbed answer.
    """

    model_config = _IGNORE

    title: str = omittable()
    status: str = omittable()
    abstract: str = omittable()
    text_content: str = omittable()
    private_notes: Optional[str] = omittable()
    author_text: str = omittable()
    doc_type: str = omittable()
    year: str = omittable()
    published_date: Optional[str] = omittable("Null: none (the New File form sends null for no date).")
    publisher: str = omittable()
    location: str = omittable()
    edition: str = omittable()
    journal: str = omittable()
    volume: str = omittable()
    issue: str = omittable()
    pages: str = omittable()
    isbn: str = omittable()
    doi: str = omittable()
    file_path: str = omittable("A managed PDF (``/api/pdfs/<name>``) the new Work adopts.")
    file_name: Optional[str] = omittable("The uploaded file's name; sent with file_b64. Null: no upload.")
    file_b64: Optional[str] = omittable("The uploaded PDF, base64 encoded. Null: no upload.")
    source_kind: str = omittable("``pdf`` or ``video``; inferred when left out.")
    source_url: str = omittable("A YouTube URL for a video Work, else the citation URL.")
    source_mime: str = omittable()
    provider: str = omittable()
    provider_id: str = omittable()
    thumb_url: str = omittable()
    thumb_page: Optional[int] = omittable()
    urldate: str = omittable()
    folder_id: Optional[str] = Field(default=None, description="Left out or null: Uncategorized.")
    playlist_id: str = omittable("A Playlist to add the Work to (best effort).")
    roles: list[InitialRole] = omittable()


class WorkCreated(BaseModel):
    model_config = _FORBID

    id: str


class WorkPatch(BaseModel):
    """``PATCH /api/works/:id``: the fields sent change; the others are kept.

    ``file_path`` may only echo the stored path (another one is ``501``); the
    source identity fields are refused (``400``): they change through
    ``SET_WORK_SOURCE``.
    """

    model_config = _IGNORE

    title: str = omittable()
    status: str = omittable()
    abstract: Optional[str] = omittable()
    text_content: str = omittable("The Research Note (Markdown).")
    private_notes: Optional[str] = omittable()
    author_text: Optional[str] = omittable()
    doc_type: Optional[str] = omittable()
    year: Optional[str] = omittable()
    published_date: Optional[str] = omittable()
    publisher: Optional[str] = omittable()
    location: Optional[str] = omittable()
    edition: Optional[str] = omittable()
    journal: Optional[str] = omittable()
    volume: Optional[str] = omittable()
    issue: Optional[str] = omittable()
    pages: Optional[str] = omittable()
    isbn: Optional[str] = omittable()
    doi: Optional[str] = omittable()
    urldate: Optional[str] = omittable()
    thumb_page: Optional[Union[int, str]] = omittable()
    source_mime: Optional[str] = omittable()
    hide_pdf_link_annotations: Union[bool, int, str] = omittable("true, 1 or \"1\"/\"true\"/\"yes\" hide them.")
    citation_style: str = omittable("A style id, or empty for the Library's.")
    folder_id: Optional[str] = Field(default=None, description="Move to this Folder; null: Uncategorized.")
    file_path: Optional[str] = omittable("Only the stored path, echoed.")


class WorkStatus(BaseModel):
    """``{"status": ...}``: what the request did."""

    model_config = _FORBID

    status: Literal["updated", "deleted", "opened"]


class _BulkWorks(BaseModel):
    model_config = _IGNORE

    work_ids: list[str] = Field(min_length=1, max_length=500, description=(
        "1 to 500 entries; the limit counts repeats, which then count once."))


class BulkSetStatus(_BulkWorks):
    action: Literal["set_status"]
    status: str = Field(description="The new status, one of the Work statuses.")


class BulkMoveFolder(_BulkWorks):
    action: Literal["move_folder"]
    folder_id: Optional[str] = Field(
        description="The Folder; null: Uncategorized. Required (null included); an empty string is refused.")


class BulkTags(_BulkWorks):
    action: Literal["add_tags", "remove_tags"]
    tag_ids: list[str] = Field(min_length=1, max_length=500, description=(
        "The Tags to add or remove: 1 to 500 entries; the limit counts repeats."))


BulkWorkRequest = Annotated[Union[BulkSetStatus, BulkMoveFolder, BulkTags], Field(discriminator="action")]
"""``POST /api/works/bulk``: one action on up to 500 Works, all or nothing; ``action`` picks the fields."""


class BulkWorkResult(BaseModel):
    model_config = _FORBID

    status: Literal["updated"]
    action: str
    requested: int = Field(description="The distinct Works named.")
    updated: int = Field(description="Equal to ``requested``: every selected Work, changed or not.")


class TextRebuildRequest(BaseModel):
    model_config = _IGNORE

    force: bool = omittable("Accepted; asks for nothing more (only what has no current row is re-extracted).")


class TextRebuildQueued(BaseModel):
    """``POST /api/works/reindex-pdf-text``: the rebuild is queued; the counters are zero."""

    model_config = _FORBID

    status: Literal["ok"]
    queued: bool = Field(description="False when a rebuild was already queued.")
    processed: int
    indexed: int
    updated: int
    unchanged: int
    empty: int
    truncated: int
    missing: int
    failed: int
    removed_orphans: int
    skipped: int
    fts_rebuilt: bool
    extractor_unavailable: bool
