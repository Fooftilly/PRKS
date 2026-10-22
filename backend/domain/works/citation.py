"""What a Work's BibTeX entry is built from, on PostgreSQL (L-W-2 #631).

``generate_bibtex`` formats one entry from three inputs, and the formatter
(``PRKSDatabase.format_bibtex``) is shared by both backends, so equal inputs
give byte-identical text. This module reads the inputs, in the caller's one
``library_query`` snapshot, by SQLite's rules (``work_projection.citation_target``
and ``citation_record``, ``_roles_for_manifestation_on_conn``,
``_get_bibtex_export_profile``):

- **the citation target**: the Work's ``citation_manifestation_id``, else
  its primary Manifestation;
- **the citation record**: the Work-shaped row of that Manifestation, by the
  projection's rules (``backend.domain.works.projection.work_core``): the
  Manifestation's title, abstract and bibliographic fields override the
  Work's, ``source_url`` is a stream's URL or else the Manifestation's
  citation URL, ``source_kind`` and ``file_path`` come from its primary
  Asset, and ``author_text`` and ``updated_at`` are the Work's;
- **the credits**: ``target_credits``, i.e. ``credits(target)``, the Work-scoped roles and the roles
  scoped to the target, ``ORDER BY order_index, seq`` (SQLite's ``rowid``),
  each as its Person's row with ``role_type``, ``order_index``,
  ``credit_name`` and ``manifestation_id``;
- **the export profile**: the Library's ``bibtex_export_fields`` setting,
  defaults filled in (``backend.domain.settings.app_settings``).

Every statement filters by the scope's Library, so a Work of another Library
is not found.
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import persons
from backend.domain.settings import app_settings
from backend.domain.works import projection

_PRIMARY_JOIN: LiteralString = "m.id = w.primary_manifestation_id AND m.work_id = w.id"
_TARGET_JOIN: LiteralString = "m.id = COALESCE(w.citation_manifestation_id, w.primary_manifestation_id) AND m.work_id = w.id"
if _PRIMARY_JOIN not in projection.WORK_ROWS_SQL:  # pragma: no cover - a projection edit must update this
    raise RuntimeError("the Work row query no longer joins the primary Manifestation")
# The projection's Work row with the citation target in place of the primary Manifestation.
_RECORD_SQL: LiteralString = (
    projection.WORK_ROWS_SQL.replace(_PRIMARY_JOIN, _TARGET_JOIN) + projection.BY_ID_SQL
)
_TARGET_SQL: LiteralString = """
SELECT COALESCE(citation_manifestation_id, primary_manifestation_id) AS manifestation_id
FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s
"""
_CREDITS_SQL: LiteralString = """
SELECT person_id, role_type, order_index, credit_name, manifestation_id
FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = %(work_id)s
  AND (manifestation_id IS NULL OR manifestation_id = %(manifestation_id)s)
ORDER BY order_index, seq
"""


def citation_target(scope: LibraryScope, work_id: str) -> Optional[str]:
    """The Manifestation the Work is cited as; None when the Work is not here."""
    row = scope.execute(_TARGET_SQL, {"work_id": work_id}).fetchone()
    return None if row is None else str(row["manifestation_id"])


def citation_record(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """The Work-shaped record of its citation target (``work_projection.citation_record``)."""
    row = scope.execute(_RECORD_SQL, {"work_id": work_id}).fetchone()
    return None if row is None else projection.work_core(row)


def target_credits(scope: LibraryScope, work_id: str, manifestation_id: str) -> list[dict[str, Any]]:
    """``credits(M)``: Work-scoped roles and those scoped to M, as Person rows plus the role."""
    rows = [dict(row) for row in scope.execute(
        _CREDITS_SQL, {"work_id": work_id, "manifestation_id": manifestation_id}).fetchall()]
    people = persons.persons_by_id(scope, sorted({str(row["person_id"]) for row in rows}))
    out: list[dict[str, Any]] = []
    for row in rows:
        person = people.get(str(row["person_id"]))
        if person is not None:  # pragma: no branch - the role's key holds its Person
            out.append({**person, "role_type": row["role_type"], "order_index": row["order_index"],
                        "credit_name": row["credit_name"], "manifestation_id": row["manifestation_id"]})
    return out


def bibtex_inputs(scope: LibraryScope, work_id: str
                  ) -> Optional[tuple[dict[str, Any], list[dict[str, Any]], dict[str, bool]]]:
    """The record, credits and export profile of ``generate_bibtex``; None for no such Work."""
    target = citation_target(scope, work_id)
    if target is None:
        return None
    record = citation_record(scope, work_id)
    if record is None:  # pragma: no cover - the target is the Work's own Manifestation
        return None
    profile = app_settings.settings_response(scope)[app_settings.BIBTEX_EXPORT_FIELDS]
    return record, target_credits(scope, work_id, target), dict(profile)
