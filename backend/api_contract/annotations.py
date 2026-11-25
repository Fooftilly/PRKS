"""PDF annotations and Work PDF bytes wire shapes (#1431).

Shape and JSON types only. The rows are the queries of
``backend/domain/annotations``; an annotation's normalization, the set
revisions and the materialization claim stay in that package and
``backend/pdf``, which refuse with their own ``{error, code}``.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.api_contract.boundary import omittable

_FORBID = ConfigDict(extra="forbid")
# A request names the fields it sends; the routes ignore keys they do not know.
_IGNORE = ConfigDict(extra="ignore")


class PdfAnnotation(BaseModel):
    """One stored annotation as the viewer creates it: the canonical columns and its geometry keys."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    contents: str
    pageIndex: Optional[int]
    color: str
    updated_at: str = omittable("When it was last stored, ``YYYY-MM-DD HH:MM:SS`` UTC.")


class AnnotationRevision(BaseModel):
    model_config = _FORBID

    annotation_id: str
    revision: int


class AnnotationsState(BaseModel):
    """``GET /api/works/{id}/annotations-state``: each annotation's revision, tombstones included."""

    model_config = _FORBID

    work_id: str
    annotations: list[AnnotationRevision]
    known_absent: dict[str, int] = Field(
        description="Deleted annotation ids whose revision is kept: id -> revision.")


class AnnotationsSnapshot(AnnotationsState):
    """``GET /api/works/{id}/annotations-snapshot``: the annotations, their revisions and the set revisions."""

    items: list[PdfAnnotation]
    canonical_annotation_set_revision: int
    materialized_pdf_annotation_revision: int


class Materialization(BaseModel):
    """``GET /api/works/{id}/pdf-materialization``: whether the PDF bytes hold the current set."""

    model_config = _FORBID

    work_id: str
    canonical_annotation_set_revision: int
    materialized_pdf_annotation_revision: int
    stale: bool
    code: Optional[Literal["ANNOTATION_MATERIALIZATION_STALE"]] = Field(description="Set when stale.")


class AnnotationList(BaseModel):
    """``GET /api/works/{id}/annotations``: the list as one JSON text (``[]`` for a missing Work)."""

    model_config = _FORBID

    work_id: str
    annotations_json: str


class AnnotationsReplaceRequest(BaseModel):
    model_config = _IGNORE

    annotations_json: Any = Field(description="The whole list: a JSON array, or its JSON text.")
    canonical_annotation_set_revision: int = Field(description="The set revision the list was edited at.")


class AnnotationsReplaced(BaseModel):
    model_config = _FORBID

    status: Literal["saved"]
    path: Literal["legacy-full-list"]
    canonical_annotation_set_revision: int


class AdoptRequest(BaseModel):
    model_config = _IGNORE

    viewer_annotations: list[Any] = Field(description="The annotations the viewer read from the PDF bytes.")


class Adopted(BaseModel):
    """The user markup only the bytes held, now stored; the rest counted by why it was skipped."""

    model_config = _FORBID

    work_id: str
    adopted: list[str]
    adopted_count: int
    skipped_existing: int
    skipped_non_user: int
    skipped_known_absent: int


class PdfWriteRequest(BaseModel):
    model_config = _IGNORE

    file_b64: str = Field(description="The PDF, base64 encoded.")
    materialized_annotation_set_revision: Optional[int] = omittable(
        "The set revision the bytes render: they are the viewer's annotated PDF. Left out or null: the "
        "bytes replace the Work's PDF.")


class _PdfWritten(BaseModel):
    model_config = _FORBID

    status: Literal["success"]
    work_id: str
    file_path: str = Field(description="``/api/pdfs/<name>``.")
    content_sha256: str = Field(description="The stored working file (linearization may rewrite it).")
    byte_size: int
    canonical_annotation_set_revision: int
    materialized_pdf_annotation_revision: int
    stale: bool


class PdfReplaced(_PdfWritten):
    """The Work's PDF was replaced."""

    ingest_sha256: str = Field(description="The bytes as uploaded.")


class PdfMaterialized(_PdfWritten):
    """The viewer's annotated PDF is the Work's bytes at the claimed set revision."""

    export_sha256: str = Field(description="The bytes as uploaded.")
    export_byte_size: int


PdfWritten = Union[PdfReplaced, PdfMaterialized]


class MaterializationStale(BaseModel):
    """The claimed set revision is not the current one."""

    model_config = _FORBID

    error: str
    code: Literal["ANNOTATION_MATERIALIZATION_STALE"]
    canonical_annotation_set_revision: Optional[int]
    materialized_pdf_annotation_revision: Optional[int]
