"""Citation styles a Library imports (Citation V1, #41).

PRKS ships seven styles (:mod:`backend.domain.works.citation_styles`). An
owner may add others from the Citation Style Language repository or the CSL
visual editor. Settings uploads the ``.csl`` file's text, and this module
checks it before it is stored:

- at most :data:`MAX_CSL_BYTES` of UTF-8, with no NUL;
- no document type declaration or entity (CSL styles have none), so the
  standard-library parser is never asked to expand one;
- well-formed XML whose root is a CSL ``<style>`` of version 1.0;
- an *independent* style: a dependent one names a parent style
  (``<link rel="independent-parent">``) and holds no formatting of its own;
- a ``<citation>`` and a ``<bibliography>``, each with a ``<layout>``,
  because PRKS formats bibliography entries;
- a title (``<info><title>``), which is what the pickers show.

The file is stored as it came, except where the browser's formatter
(citeproc-js, which parses the text itself) would misread it: a style whose
CSL elements carry a namespace prefix (``<cs:style xmlns:cs=…>``), or one that
writes ``&apos;``, is stored re-serialized with CSL as the default namespace
and the apostrophe as itself. The text is then the same style, as citeproc
reads it.

A Library holds at most :data:`MAX_STYLES` imported styles. Each gets an id
``custom-`` and 32 hex digits. Deleting a style that is the Library's
``citation_style`` setting removes the setting in the same transaction, so
the Library falls back to :data:`~backend.domain.works.citation_styles.DEFAULT_CITATION_STYLE`.
A Work that names a deleted style keeps the id and is cited in the Library's
default style, the same rule an offline edit replayed after the delete
follows.

Reads need ``READ_LIBRARY``; an import or a delete is Library
administration (``ADMIN_LIBRARY``), like the setting it feeds.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, LiteralString, Optional
from xml.etree import ElementTree

from backend.db.database import Database
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.works.citation_styles import is_custom_style_id

MAX_CSL_BYTES = 1024 * 1024
MAX_STYLES = 50
MAX_TITLE_CHARS = 300

_CSL = "{http://purl.org/net/xbiblio/csl}"
_DECLARATION_RE = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
# What citeproc-js's own XML parser misreads: a prefixed CSL element, and the ``&apos;`` entity.
_PREFIXED_RE = re.compile(r"<\s*/?\s*[A-Za-z_][\w.-]*:")
_APOS = "&apos;"

_LIST_SQL: LiteralString = """
SELECT id, title, created_at FROM prks.citation_styles
WHERE library_id = %(library_id)s ORDER BY lower(title), id
"""
_GET_SQL: LiteralString = """
SELECT id, title, csl FROM prks.citation_styles WHERE library_id = %(library_id)s AND id = %(id)s
"""
_EXISTS_SQL: LiteralString = """
SELECT 1 FROM prks.citation_styles WHERE library_id = %(library_id)s AND id = %(id)s
"""
_COUNT_SQL: LiteralString = """
SELECT count(*) AS n FROM prks.citation_styles WHERE library_id = %(library_id)s
"""
# Serializes imports into one Library, so two at once cannot pass the limit together.
_LOCK_SQL: LiteralString = """
SELECT pg_advisory_xact_lock(hashtextextended('prks.citation_styles:' || %(library_id)s, 0))
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.citation_styles (library_id, id, title, csl) VALUES (%(library_id)s, %(id)s, %(title)s, %(csl)s)
RETURNING id, title, created_at
"""
_DELETE_SQL: LiteralString = """
DELETE FROM prks.citation_styles WHERE library_id = %(library_id)s AND id = %(id)s RETURNING id
"""
_CLEAR_SETTING_SQL: LiteralString = """
DELETE FROM prks.library_settings
WHERE library_id = %(library_id)s AND key = 'citation_style' AND value = %(id)s
"""


class StyleRefused(ValueError):
    """The file is not a style PRKS can import; the message says why, for the owner."""


def _child(element: ElementTree.Element, name: str) -> Optional[ElementTree.Element]:
    return element.find(_CSL + name)


def _has_layout(style: ElementTree.Element, name: str) -> bool:
    section = _child(style, name)
    return section is not None and _child(section, "layout") is not None


def check_style(csl: Any) -> tuple[str, str]:
    """The style's title and the text to store when ``csl`` is a style PRKS can import, else :class:`StyleRefused`."""
    if not isinstance(csl, str) or not csl.strip():
        raise StyleRefused("Choose a .csl style file.")
    if len(csl.encode("utf-8")) > MAX_CSL_BYTES:
        raise StyleRefused("The style file is larger than 1 MB.")
    if "\x00" in csl or _DECLARATION_RE.search(csl):
        raise StyleRefused("The file is not a CSL style.")
    try:
        root = ElementTree.fromstring(csl.lstrip("﻿"))
    except ElementTree.ParseError:
        raise StyleRefused("The file is not valid XML, so it is not a CSL style.") from None
    if root.tag != _CSL + "style":
        raise StyleRefused("The file is not a CSL style.")
    if not str(root.get("version", "")).startswith("1.0"):
        raise StyleRefused("PRKS reads CSL 1.0 styles; this file is another version.")
    info = _child(root, "info")
    if info is not None and any(link.get("rel") == "independent-parent" for link in info.iter(_CSL + "link")):
        raise StyleRefused("This is a dependent style: it only points to another style. Import that style instead.")
    if not _has_layout(root, "citation"):
        raise StyleRefused("The style has no citation layout, so it is not a complete CSL style.")
    if not _has_layout(root, "bibliography"):
        raise StyleRefused("The style has no bibliography, so PRKS cannot format references with it.")
    title_element = _child(info, "title") if info is not None else None
    title = re.sub(r"\s+", " ", title_element.text or "").strip() if title_element is not None else ""
    if not title:
        raise StyleRefused("The style has no title.")
    stored = _for_citeproc(csl, root)
    # Re-serializing can lengthen a file just under the cap (``<x/>`` becomes ``<x />``).
    if len(stored.encode("utf-8")) > MAX_CSL_BYTES:
        raise StyleRefused("The style file is larger than 1 MB.")
    return title[:MAX_TITLE_CHARS], stored


def _for_citeproc(csl: str, root: ElementTree.Element) -> str:
    """``csl`` as is, or re-serialized when citeproc-js would misread it (see the module docstring)."""
    if _APOS not in csl and not _PREFIXED_RE.search(csl):
        return csl
    ElementTree.register_namespace("", _CSL[1:-1])
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ElementTree.tostring(root, encoding="unicode")


def _summary(row: Any) -> dict[str, Any]:
    return {"id": str(row["id"]), "title": str(row["title"]), "created_at": row["created_at"].isoformat()}


def exists(scope: LibraryScope, style_id: str) -> bool:
    if not is_custom_style_id(style_id):
        return False
    return scope.execute(_EXISTS_SQL, {"id": style_id}).fetchone() is not None


def list_styles(scope: LibraryScope) -> list[dict[str, Any]]:
    return [_summary(row) for row in scope.execute(_LIST_SQL).fetchall()]


def get_style(scope: LibraryScope, style_id: str) -> Optional[dict[str, Any]]:
    if not is_custom_style_id(style_id):
        return None
    row = scope.execute(_GET_SQL, {"id": style_id}).fetchone()
    return None if row is None else {"id": str(row["id"]), "title": str(row["title"]), "csl": str(row["csl"])}


def import_style(scope: LibraryScope, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StyleRefused("invalid body")
    csl = data.get("csl")
    title, csl = check_style(csl)
    scope.execute(_LOCK_SQL)
    count = scope.execute(_COUNT_SQL).fetchone()
    if count is not None and int(count["n"]) >= MAX_STYLES:
        raise StyleRefused(f"A library can hold {MAX_STYLES} imported styles. Delete one first.")
    row = scope.execute(_INSERT_SQL, {"id": "custom-" + uuid.uuid4().hex, "title": title, "csl": csl}).fetchone()
    if row is None:  # pragma: no cover - INSERT … RETURNING answers its row
        raise RuntimeError("the imported style was not stored")
    return _summary(row)


def delete_style(scope: LibraryScope, style_id: str) -> bool:
    """Delete the style; the Library setting that named it goes with it."""
    if not is_custom_style_id(style_id):
        return False
    if scope.execute(_DELETE_SQL, {"id": style_id}).fetchone() is None:
        return False
    scope.execute(_CLEAR_SETTING_SQL, {"id": style_id})
    return True


# --- commands for ``backend/api/citation_styles.py`` ------------------------------------


def listed(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    return library_query(db, ctx, list_styles)


def read(db: Database, ctx: RequestContext, style_id: str) -> Optional[dict[str, Any]]:
    return library_query(db, ctx, lambda scope: get_style(scope, style_id))


def imported(db: Database, ctx: RequestContext, data: Any) -> dict[str, Any]:
    return library_command(db, ctx, Capability.ADMIN_LIBRARY, lambda scope: import_style(scope, data))


def deleted(db: Database, ctx: RequestContext, style_id: str) -> bool:
    return library_command(db, ctx, Capability.ADMIN_LIBRARY, lambda scope: delete_style(scope, style_id))
