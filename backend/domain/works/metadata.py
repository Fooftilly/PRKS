"""Work metadata fields on PostgreSQL: per-field revisions (L-W-1 #628).

The rules are ``backend/work_metadata_sync.py``'s, imported rather than
copied, so the two backends cannot drift on what a valid value is, what its
canonical spelling is, or how big a conflict result may be: one revision
scope per field (``work-field / ["W-…","field"]``), only a change advances
it, a stale base conflicts only when the values differ.

What moves is where each field lives. On SQLite every field is a ``works``
column; here a field belongs to the row that owns it:

- the Work: ``title``, ``status``, ``abstract``, ``author_text``,
  ``hide_pdf_link_annotations``;
- the primary Manifestation: the bibliographic fields, ``urldate`` and
  ``source_url`` (a PDF's provenance, its citation URL);
- the primary Asset: ``thumb_page``, ``thumb_url`` and ``source_mime``.

A Work with no Asset has nowhere to keep the Asset's fields, so setting one
to a value is refused (SQLite keeps it on the Work row). A video's
``source_url`` is its stream's identity and is refused as a field, exactly
as on SQLite (``WRONG_OPERATION_FOR_SOURCE``); ``SET_WORK_SOURCE`` changes
it. PostgreSQL has no inferred videos: a URL set on a Work without a source
stays its citation URL.

Every write locks the Work row (``FOR NO KEY UPDATE``) before any revision
row, the order every Works command uses, so a field edit and a delete of the
same Work queue rather than deadlock, and a field never advances on a Work
that a concurrent delete removed.
"""

from __future__ import annotations

from typing import Any, LiteralString, Mapping, Optional

from backend import work_metadata_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions

SCOPE_TYPE = "work-field"

_WORK_COLUMNS: tuple[LiteralString, ...] = (
    "title", "status", "abstract", "author_text", "hide_pdf_link_annotations",
)
_MANIFESTATION_COLUMNS: tuple[LiteralString, ...] = (
    "doc_type", "year", "published_date", "edition", "publisher", "location", "journal",
    "volume", "issue", "pages", "isbn", "doi", "urldate", "url",
)
_ASSET_COLUMNS: tuple[LiteralString, ...] = ("thumb_page", "thumb_url", "media_type")

# Legacy field name -> (owning row, column).
FIELD_COLUMNS: dict[str, tuple[str, str]] = {
    **{name: ("work", name) for name in _WORK_COLUMNS},
    **{name: ("manifestation", name) for name in _MANIFESTATION_COLUMNS if name != "url"},
    "source_url": ("manifestation", "url"),
    "thumb_page": ("asset", "thumb_page"),
    "thumb_url": ("asset", "thumb_url"),
    "source_mime": ("asset", "media_type"),
}

_SET_WORK: dict[str, LiteralString] = {
    column: "UPDATE prks.works SET " + column + " = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(work_id)s"
    for column in _WORK_COLUMNS
}
_SET_MANIFESTATION: dict[str, LiteralString] = {
    column: "UPDATE prks.manifestations SET " + column + " = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(manifestation_id)s"
    for column in _MANIFESTATION_COLUMNS
}
_SET_ASSET: dict[str, LiteralString] = {
    column: "UPDATE prks.assets SET " + column + " = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(asset_id)s"
    for column in _ASSET_COLUMNS
}
_TOUCH_WORK: LiteralString = (
    "UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(work_id)s"
)

_TARGET_SQL: LiteralString = """
SELECT w.id, w.title, w.status, w.abstract, w.author_text, w.hide_pdf_link_annotations,
       m.id AS manifestation_id, m.doc_type, m.year, m.published_date, m.edition, m.publisher,
       m.location, m.journal, m.volume, m.issue, m.pages, m.isbn, m.doi, m.urldate, m.url,
       a.id AS asset_id, a.kind AS asset_kind, a.url AS stream_url, a.provider, a.provider_id,
       a.thumb_page, a.thumb_url, a.media_type
FROM prks.works w
JOIN prks.manifestations m
  ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
LEFT JOIN prks.assets a
  ON a.library_id = m.library_id AND a.id = m.primary_asset_id AND a.manifestation_id = m.id
WHERE w.library_id = %(library_id)s AND w.id = %(work_id)s
"""
# The lock is its own statement. Under READ COMMITTED a statement that waits
# for a row lock returns the newest version of the locked row only, so a
# joined Manifestation or Asset would come from before the previous holder's
# commit. Reading the target after the lock, in a new statement, sees it.
_LOCK_WORK_SQL: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"
    " FOR NO KEY UPDATE"
)

WRONG_OPERATION_FOR_SOURCE = "WRONG_OPERATION_FOR_SOURCE"


class FieldRefused(ValueError):
    """A field that cannot be set on this Work (its source decides)."""

    def __init__(self, field: str, code: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.code = code


def target(scope: LibraryScope, work_id: str, *, for_update: bool) -> Optional[dict[str, Any]]:
    """The Work with its primary Manifestation and Asset; locked when writing.

    ``for_update`` locks the Work row first and then reads, so the values are
    the ones the previous lock holder committed.
    """
    params = {"work_id": work_id}
    if for_update and scope.execute(_LOCK_WORK_SQL, params).fetchone() is None:
        return None
    row = scope.execute(_TARGET_SQL, params).fetchone()
    return None if row is None else dict(row)


def is_video(row: Mapping[str, Any]) -> bool:
    return row["asset_kind"] == "external_stream"


def stored_value(row: Mapping[str, Any], field: str) -> Any:
    """The column value a legacy field reads, as the Work projection does."""
    owner, column = FIELD_COLUMNS[field]
    if field == "source_url" and is_video(row):
        return row["stream_url"]
    if owner == "asset" and row["asset_id"] is None:
        return None
    return row[column]


def scope_id(work_id: str, field: str) -> str:
    return rules.scope_key(work_id, field)


def refusal(row: Mapping[str, Any], field: str, database_value: Any) -> Optional[FieldRefused]:
    """Why ``field`` may not take ``database_value`` on this Work, or None."""
    if field == "source_url" and is_video(row):
        return FieldRefused(field, WRONG_OPERATION_FOR_SOURCE,
                            "source_url cannot be set on this Work: its source identity is not a "
                            "field-scoped value")
    owner, _column = FIELD_COLUMNS[field]
    if owner == "asset" and row["asset_id"] is None and database_value not in (None, ""):
        return FieldRefused(field, WRONG_OPERATION_FOR_SOURCE,
                            "%s cannot be set on this Work: it has no file or stream" % field)
    return None


def write(scope: LibraryScope, row: Mapping[str, Any], field: str, database_value: Any) -> None:
    """Store one field's value in the row that owns it (no revision)."""
    owner, column = FIELD_COLUMNS[field]
    params = {"value": database_value, "work_id": row["id"],
              "manifestation_id": row["manifestation_id"], "asset_id": row["asset_id"]}
    if owner == "work":
        scope.execute(_SET_WORK[column], params)
        return
    if owner == "manifestation":
        scope.execute(_SET_MANIFESTATION[column], params)
    elif row["asset_id"] is not None:
        scope.execute(_SET_ASSET[column], params)
    scope.execute(_TOUCH_WORK, params)


def set_synced_field(scope: LibraryScope, row: dict[str, Any], field: str, wire: str) -> tuple[bool, int]:
    """``set_field_on_conn``: write a synchronized field and advance its revision.

    The caller holds the Work row lock and has checked :func:`refusal`.
    Returns ``(changed, revision_after)``; a no-op advances nothing.
    """
    revision = revisions.lock(scope, SCOPE_TYPE, scope_id(row["id"], field))
    desired = rules.wire_to_database(field, wire)
    if rules.wire_to_database(field, stored_value(row, field)) == desired:
        return False, revision
    write(scope, row, field, desired)
    row[FIELD_COLUMNS[field][1]] = desired
    return True, revisions.advance(scope, SCOPE_TYPE, scope_id(row["id"], field))


def apply_field_operation(scope: LibraryScope, work_id: str, field: str, value: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_WORK_METADATA_FIELD``: the SQLite handler's decision table, result for result."""
    result: dict[str, Any] = {"work_id": work_id, "field": field}
    row = target(scope, work_id, for_update=True)
    if row is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    desired = rules.canonical_wire(field, value)
    refused = refusal(row, field, rules.wire_to_database(field, desired))
    if refused is not None and refused.code == WRONG_OPERATION_FOR_SOURCE:
        result["code"] = refused.code
        return 409, result
    current = rules.database_to_wire(field, stored_value(row, field))
    revision = revisions.lock(scope, SCOPE_TYPE, scope_id(work_id, field))
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **rules.disagreement(field, current, desired))
        return 400, rules.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      **rules.disagreement(field, current, desired))
        return 409, rules.fit_terminal_result(result, current, desired)
    changed, after = set_synced_field(scope, row, field, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed)
    if field in rules.BYTE_LIMITED_FIELDS:
        result["value_omitted"] = True
    else:
        result["value"] = desired
    return 200, result


def field_state(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``get_work_metadata_state``: every synchronized field's revision (and small value)."""
    row = target(scope, work_id, for_update=False)
    if row is None:
        return None
    fields = sorted(rules.SYNCED_FIELDS)
    by_scope = revisions.read(scope, SCOPE_TYPE, [scope_id(work_id, field) for field in fields])
    out: dict[str, Any] = {}
    for field in fields:
        entry: dict[str, Any] = {"revision": by_scope[scope_id(work_id, field)]}
        if field not in rules.BYTE_LIMITED_FIELDS:
            entry["value"] = rules.database_to_wire(field, stored_value(row, field))
        out[field] = entry
    return {"work_id": work_id, "fields": out}
