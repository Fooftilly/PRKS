"""CSL-JSON for a Work's citation record (Citation V1, #41).

CSL-JSON is PRKS's citation interchange boundary (#179): formatted
citations, bibliography styles and CSL-JSON export are built from what
:func:`format_csl` returns, never from the ``works`` table shape. It reads
the same inputs as :func:`backend.domain.works.bibtex_format.format_bibtex`
(``backend.domain.works.citation``): the Work-shaped record of its citation
Manifestation and that Manifestation's credits. It reads no store, so equal
inputs give equal items.

The Library's BibTeX export profile does not apply: it chooses which fields
a ``.bib`` entry carries, while a CSL item carries everything a style may
need and the style chooses what it prints.

Field mapping, from the record's names to CSL 1.0.2 variables:

- ``doc_type`` (the BibLaTeX entry type) gives ``type`` (:data:`CSL_TYPES`);
  a stream's citation Manifestation is a ``motion_picture``.
- Author, Editor and Translator credits become ``author``, ``editor`` and
  ``translator`` in role order; with no linked Author, ``author_text``
  (``;``-separated names, or on a stream one corporate name).
- ``published_date`` (``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``), else
  ``year``, gives ``issued``.
- ``journal`` is the ``container-title``; ``publisher``, ``location``,
  ``edition``, ``volume``, ``issue``, ``pages``, ``isbn``, ``doi``,
  ``source_url`` and ``abstract`` map one to one. ``accessed`` is the
  ``updated_at`` date whenever there is a URL, as BibTeX's ``urldate`` is.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.domain.works.rules import normalize_doc_type

#: BibLaTeX entry type (``work_metadata_sync.DOC_TYPES``) -> CSL item type.
CSL_TYPES: dict[str, str] = {
    "article": "article-journal",
    "book": "book",
    "booklet": "pamphlet",
    "inbook": "chapter",
    "incollection": "chapter",
    "inproceedings": "paper-conference",
    "proceedings": "book",
    "manual": "report",
    "mastersthesis": "thesis",
    "phdthesis": "thesis",
    "techreport": "report",
    "unpublished": "manuscript",
    "misc": "document",
    "online": "webpage",
}
_GENRES = {"mastersthesis": "Master's thesis", "phdthesis": "PhD thesis"}
_ROLE_VARIABLES = (("Author", "author"), ("Editor", "editor"), ("Translator", "translator"))
_DATE_RE = re.compile(r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")
_ERA = r"(?:\s*(BCE|BC|CE|AD))?"
#: The whole Year field as a year, ``950`` or ``399 BCE``; anything else (``19th century``) is not a date.
_YEAR_RE = re.compile(r"^\s*(\d{1,4})" + _ERA + r"\s*$", re.IGNORECASE)
#: The whole Year field as a range of years: ``2020–2021``, ``1999–00``, ``950–951``, ``1914/1918``.
_YEAR_RANGE_RE = re.compile(r"^\s*(\d{1,4})\s*[-–—/]\s*(\d{1,4})\s*$")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _name(family: str, given: str) -> Optional[dict[str, str]]:
    if family and given:
        return {"family": family, "given": given}
    if family or given:
        return {"family": family or given}
    return None


def _name_from_credit(credit: str) -> Optional[dict[str, str]]:
    """One free-text name: ``Family, Given``, else ``Given Family`` split at the last word."""
    credit = re.sub(r"\s+", " ", credit).strip()
    if not credit:
        return None
    if "," in credit:
        family, given = (part.strip() for part in credit.split(",", 1))
        return _name(family, given)
    given, _, family = credit.rpartition(" ")
    return _name(family, given)


def _role_name(role: dict[str, Any]) -> Optional[dict[str, str]]:
    """The name a credit cites under: its credit name, else the Person's names."""
    credit = _text(role.get("credit_name"))
    if credit:
        return _name_from_credit(credit)
    return _name(_text(role.get("last_name")), _text(role.get("first_name")))


def _year(value: str, era: Optional[str]) -> int:
    """CSL counts years before the common era as negative (399 BCE is -399)."""
    return -int(value) if era and era.upper() in ("BCE", "BC") else int(value)


def _year_field(raw: Any) -> Optional[dict[str, Any]]:
    """The Year field as CSL date parts, only when the whole value is a year or a range of years."""
    text = _text(raw)
    match = _YEAR_RE.match(text)
    if match:
        return {"date-parts": [[_year(match.group(1), match.group(2))]]}
    match = _YEAR_RANGE_RE.match(text)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        digits = len(match.group(2))
        if digits < len(match.group(1)):
            # An abbreviated end is the first year after the start that ends
            # in those digits: 1998-99 is 1999, 1999-00 is 2000, 950-51 is 951.
            step = 10 ** digits
            end = start - start % step + end
            if end <= start:
                end += step
        if end > start:
            return {"date-parts": [[start], [end]]}
    return None


def _issued(work: dict[str, Any]) -> Optional[dict[str, Any]]:
    match = _DATE_RE.match(_text(work.get("published_date")))
    if match:
        parts = [int(part) for part in match.groups() if part]
        if len(parts) > 1 and not 1 <= parts[1] <= 12:
            parts = parts[:1]
        if len(parts) > 2 and not 1 <= parts[2] <= 31:
            parts = parts[:2]
        return {"date-parts": [parts]}
    return _year_field(work.get("year"))


def format_csl(work_id: str, work: dict[str, Any], roles: list[dict[str, Any]], *,
               video: bool) -> dict[str, Any]:
    """One CSL-JSON item from a citation record and its credits.

    ``work`` and ``roles`` are what ``format_bibtex`` takes; ``video`` is
    True when the citation Manifestation's primary Asset is a stream.
    Empty values are left out, so an item carries only what is known.
    """
    doc_type = normalize_doc_type(work.get("doc_type"))
    item: dict[str, Any] = {"id": work_id, "type": "motion_picture" if video else CSL_TYPES[doc_type]}
    title = _text(work.get("title"))
    if title:
        item["title"] = title
    for role_type, variable in _ROLE_VARIABLES:
        names = [name for name in (_role_name(r) for r in roles if r.get("role_type") == role_type) if name]
        if names:
            item[variable] = names
    if "author" not in item:
        text_credit = _text(work.get("author_text"))
        if text_credit and video:
            item["author"] = [{"literal": text_credit}]  # a channel or organization
        elif text_credit:
            names = [name for name in (_name_from_credit(part) for part in text_credit.split(";")) if name]
            if names:
                item["author"] = names
    issued = _issued(work)
    if issued:
        item["issued"] = issued
    if doc_type in _GENRES and not video:
        item["genre"] = _GENRES[doc_type]
    location = "; ".join(part for part in (
        re.sub(r"\s+", " ", seg).strip() for seg in _text(work.get("location")).split(";")) if part)
    for variable, value in (
        ("container-title", _text(work.get("journal"))),
        ("publisher", _text(work.get("publisher"))),
        ("publisher-place", location),
        ("edition", _text(work.get("edition"))),
        ("volume", _text(work.get("volume"))),
        ("issue", _text(work.get("issue"))),
        ("page", _text(work.get("pages")).replace("--", "-")),
        ("ISBN", _text(work.get("isbn"))),
        ("DOI", _text(work.get("doi"))),
        ("URL", _text(work.get("source_url"))),
        ("abstract", _text(work.get("abstract"))),
    ):
        if value:
            item[variable] = value
    accessed = _issued({"published_date": _text(work.get("updated_at"))[:10]})
    if "URL" in item and accessed and len(accessed["date-parts"][0]) == 3:
        item["accessed"] = accessed
    return item
