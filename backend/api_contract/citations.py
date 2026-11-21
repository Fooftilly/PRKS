"""Citations HTTP request/response DTOs (Citation V1, #41).

``POST /api/citations`` is a read: it answers the selected Works' citations
as CSL-JSON items or as one BibTeX text and changes nothing.

Shape and JSON types only. The domain (``work_commands.citations``) already
refuses every malformed body with its own message (``Unknown citation
format.``, the bulk-selection messages), so, as with Saved Views, the
controller passes the JSON object straight to the domain. The request model
describes exactly what the endpoint accepts; ``tests/test_api_contract_citations.py``
and the PostgreSQL API tests keep it in step with the live controller.

A CSL item leaves out every variable it has no value for, as CSL-JSON does,
so :class:`CslItem` is a ``TypedDict`` whose optional variables are
``NotRequired`` rather than nullable.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, with_config
from typing_extensions import NotRequired, TypedDict

CitationFormat = Literal["csl-json", "bibtex"]


class CitationsRequest(BaseModel):
    """POST /api/citations. At most ``PRKS_BULK_WORK_MAX`` ids, deduplicated in order by the domain."""

    model_config = ConfigDict(extra="ignore")

    work_ids: list[str] = Field(..., min_length=1)
    format: CitationFormat


@with_config(ConfigDict(extra="forbid"))
class CslName(TypedDict):
    """A CSL name: ``family`` with an optional ``given``, or one ``literal`` (a channel)."""

    family: NotRequired[str]
    given: NotRequired[str]
    literal: NotRequired[str]


#: A CSL date: one ``[year]``, ``[year, month]`` or ``[year, month, day]``.
#: ``date-parts`` is not an identifier, hence the functional form.
CslDate = with_config(ConfigDict(extra="forbid"))(
    TypedDict("CslDate", {"date-parts": list[list[int]]})
)


CslItem = with_config(ConfigDict(extra="forbid"))(TypedDict(
    "CslItem",
    {
        "id": str,
        "type": str,
        "title": NotRequired[str],
        "author": NotRequired[list[CslName]],
        "editor": NotRequired[list[CslName]],
        "translator": NotRequired[list[CslName]],
        "issued": NotRequired[CslDate],
        "accessed": NotRequired[CslDate],
        "genre": NotRequired[str],
        "container-title": NotRequired[str],
        "publisher": NotRequired[str],
        "publisher-place": NotRequired[str],
        "edition": NotRequired[str],
        "volume": NotRequired[str],
        "issue": NotRequired[str],
        "page": NotRequired[str],
        "ISBN": NotRequired[str],
        "DOI": NotRequired[str],
        "URL": NotRequired[str],
        "abstract": NotRequired[str],
    },
))


class CitationsCslJson(BaseModel):
    """``format: csl-json``: one item per found Work, in request order."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["csl-json"]
    items: list[CslItem]
    missing: list[str]


class CitationsBibtex(BaseModel):
    """``format: bibtex``: each found Work's entry, separated by a blank line."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["bibtex"]
    text: str
    missing: list[str]


CitationsResult = Union[CitationsCslJson, CitationsBibtex]
